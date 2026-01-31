"""Excel-catalog driven ESG metrics extraction.

Implements the requested pipeline:
1) English-first embedding recall
2) Rerank to keep high-signal candidates
3) HippoRAG expansion (boost-only)
4) LLM/API strict extraction (no hallucinations)
5) Hard quality gates + conflict resolution
6) Persist JSON outputs

The output JSON record schema matches the user's requirement:
  id, name, Primary Navigation, Secondary Navigation, Topic, Sub-topic,
  page, data, year, unit, detail
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from loguru import logger

# -----------------------------------------------------------------------------
# Concurrency controls
#
# Goal: accelerate extraction without reducing quality.
# - Report-level concurrency: process different reports in parallel.
# - LLM concurrency: cap concurrent API calls to avoid rate-limit instability.
# - Retrieval GPU lock: serialize GPU-heavy retrieval/rerank to prevent contention
#   and nondeterministic slowdowns / OOMs on single-GPU deployments.
# -----------------------------------------------------------------------------

_RETRIEVAL_LOCK = threading.Lock()
_CACHE_WRITE_LOCK = threading.Lock()

def _env_int(name: str, default: int) -> int:
    try:
        v = int(str(os.getenv(name, str(default)) or str(default)).strip())
        return v
    except Exception:
        return default


_LLM_CONCURRENCY = max(1, min(12, _env_int("EXCEL_METRICS_LLM_CONCURRENCY", 4)))
_LLM_SEM = threading.BoundedSemaphore(_LLM_CONCURRENCY)

_REPORT_WORKERS = max(1, min(4, _env_int("EXCEL_METRICS_REPORT_WORKERS", 2)))

_USE_GPU_LOCK = str(os.getenv("EXCEL_METRICS_GPU_LOCK", "1") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "y",
)


def _with_retrieval_lock(fn, *args, **kwargs):
    if not _USE_GPU_LOCK:
        return fn(*args, **kwargs)
    with _RETRIEVAL_LOCK:
        return fn(*args, **kwargs)


def _llm_call_with_retry(call_fn, *, max_attempts: int = 3, base_sleep: float = 0.8):
    """Execute an LLM call with bounded concurrency + light retries.

    Retries are important under concurrency to avoid *missing data* due to transient
    429/5xx/timeouts. This does not change extraction logic; it only improves
    stability.
    """
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            with _LLM_SEM:
                return call_fn()
        except Exception as e:
            last_err = e
            # Backoff: 0.8s, 1.6s, 2.4s (bounded)
            if attempt < max_attempts:
                time.sleep(min(6.0, base_sleep * attempt))
                continue
            raise


from .cross_analysis import (
    CROSS_CACHE_DIR,
    _clean_snippet_for_llm,
    _get_llm_client,
    _get_processing_config,
    _value_in_context,
    get_embedding_model,
    load_artifacts,
    topn_segments,
)
from .cross_analysis import _norm_unit as _norm_unit_internal
from .cross_analysis import _to_data_str as _to_data_str_internal
from .cross_analysis import _split_unit_from_data as _split_unit_from_data_internal
from .cross_analysis import _YEAR as _YEAR_RE
from .cross_analysis import _NUM_UNIT as _NUM_UNIT_RE
from .cross_analysis import _PERCENT as _PERCENT_RE
from .cross_analysis import _COUNT as _COUNT_RE

from .cross_analysis import get_reports_info

from .excel_metrics_catalog import ExcelMetricSpec, group_specs, load_excel_catalog


# -----------------------------
# Unit normalization + conversion
# -----------------------------

_WS = re.compile(r"\s+")


def _norm_unit_phrase(u: Optional[str]) -> Optional[str]:
    """Normalize a unit phrase from reports/catalog.

    Keeps composite units (e.g., "tCO2e/mn currency") while normalizing
    common variations, spaces, and unicode.
    """
    if not u:
        return None
    s = str(u).strip()
    if not s:
        return None
    s = s.replace("CO₂", "CO2").replace("co₂", "co2")
    s = s.replace("t CO2e", "tCO2e").replace("t CO2-e", "tCO2e")
    s = s.replace("m^3", "m3").replace("m³", "m3")
    s = _WS.sub(" ", s)
    return s.strip()


def _unit_tokens(spec: ExcelMetricSpec) -> List[str]:
    """All acceptable unit tokens/phrases for a metric (lower-cased)."""
    items = list(spec.unit_items or ())
    out: List[str] = []
    for x in items:
        xx = _norm_unit_phrase(x)
        if not xx:
            continue
        out.append(xx.lower())
    # Add allow-list fallbacks (legacy)
    for x in (spec.units_allow or ()):  # already normalized-ish
        xx = _norm_unit_phrase(x)
        if not xx:
            continue
        out.append(xx.lower())
    # de-dup preserve order
    uniq: List[str] = []
    for x in out:
        if x not in uniq:
            uniq.append(x)
    return uniq


def _canonical_unit(spec: ExcelMetricSpec) -> Optional[str]:
    cu = _norm_unit_phrase(spec.canonical_unit) if getattr(spec, "canonical_unit", None) else None
    if cu:
        return cu
    # Fallback: first listed unit
    if spec.unit_items:
        return _norm_unit_phrase(spec.unit_items[0])
    return None


def _parse_float(s: str) -> Optional[float]:
    if not s:
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def _convert_value(value: float, from_unit: str, to_unit: str) -> Optional[float]:
    """Convert value between common ESG units.

    This is intentionally conservative: only handles high-confidence conversions.
    Returns None if unsupported.
    """
    fu = (from_unit or "").strip()
    tu = (to_unit or "").strip()
    if not fu or not tu:
        return None

    f = fu.lower().replace(" ", "")
    t = tu.lower().replace(" ", "")

    # Percent/fraction
    if t == "%":
        if f in ("%", "percent", "percentage"):
            return value
        if f in ("fraction", "ratio", "0-1", "0to1"):
            return value * 100.0

    # Emissions to tCO2e
    # Normalize a few common variants
    if t in ("tco2e", "co2e"):
        if f in ("tco2e", "co2e", "metrictonsco2e", "mtco2e", "mtco2", "mtco2e/"):
            return value
        if f in ("kgco2e", "kgco2"):
            return value / 1000.0
        if f in ("ktco2e", "ktco2"):
            return value * 1000.0
        if f in ("mtco2e_megaton", "mtco2e(megaton)"):
            return value * 1_000_000.0

    # Energy to GJ
    if t == "gj":
        if f == "gj":
            return value
        if f == "tj":
            return value * 1000.0
        if f == "pj":
            return value * 1_000_000.0
        if f == "kwh":
            return value * 0.0036
        if f == "mwh":
            return value * 3.6
        if f == "gwh":
            return value * 3600.0
        if f == "mmbtu":
            return value * 1.055056
        if f == "therm":
            return value * 0.1055056
        if f == "toe":
            return value * 41.868
        if f == "ktoe":
            return value * 41.868 * 1000.0

    # Water volume to m3
    if t in ("m3", "m³"):
        if f in ("m3", "m³"):
            return value
        if f in ("l", "litre", "liter", "litres", "liters"):
            return value / 1000.0
        if f in ("ml",):
            return value * 1000.0
        if f in ("gl",):
            return value * 1_000_000.0

    # Mass to t
    if t in ("t", "ton", "tonne", "tonnes", "tons"):
        if f in ("t", "ton", "tonne", "tonnes", "tons"):
            return value
        if f == "kg":
            return value / 1000.0

    return None


def _format_number(v: float) -> str:
    # Avoid scientific notation while keeping precision reasonable.
    if v is None:
        return ""
    try:
        if float(v).is_integer():
            return str(int(round(float(v))))
    except Exception:
        pass
    try:
        from decimal import Decimal
        d = Decimal(str(v))
        s = format(d, 'f').rstrip('0').rstrip('.')
        return s or str(v)
    except Exception:
        return str(v)


def _norm_unit(u: Optional[str]) -> Optional[str]:
    return _norm_unit_internal(u)


def _to_data_str(v: Any) -> Optional[str]:
    return _to_data_str_internal(v)


def _split_unit_from_data(s: str) -> Tuple[Optional[str], str]:
    return _split_unit_from_data_internal(s)


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y")


def _allowed_keys(specs: List[ExcelMetricSpec]) -> List[Tuple[str, str]]:
    return [(s.topic, s.display_subtopic()) for s in specs]


def _build_group_query(specs: List[ExcelMetricSpec]) -> Tuple[List[str], List[str], str]:
    """Build query_pack, query_pack_vec, query_text for a (Primary, Secondary) group."""
    # Flatten unique English query phrases across the group.
    seen = set()
    q: List[str] = []
    for s in specs:
        for x in s.query_pack_en:
            xl = x.lower()
            if xl in seen:
                continue
            seen.add(xl)
            q.append(x)
            if len(q) >= 36:
                break
        if len(q) >= 36:
            break

    # Vector query pack is English-first by design.
    q_vec = q[:24]
    # Text query helps rerank / HippoRAG.
    q_text = " ; ".join(q[:18])
    return q, q_vec, q_text


def _definition_terms(defn: str) -> List[str]:
    """Extract a small set of high-signal terms from a definition cell."""
    t = (defn or "").strip()
    if not t:
        return []
    low = t.lower()
    candidates: List[str] = []
    for kw in [
        "market-based",
        "location-based",
        "scope 1",
        "scope 2",
        "scope 3",
        "intensity",
        "renewable",
        "fossil",
        "withdrawal",
        "consumption",
        "recycling",
        "hazardous",
        "fatal",
        "trir",
        "ltifr",
    ]:
        if kw in low:
            candidates.append(kw)
    # Acronyms (very useful for rerank)
    for m in re.findall(r"\b[A-Z]{2,8}\b", t):
        if m not in candidates:
            candidates.append(m)
    return candidates[:12]




# -----------------------------
# Embedding-based spec matching (Topic/Sub-topic/Definitions)
# -----------------------------

_SPEC_VEC_CACHE = None
_SPEC_VEC_LOCK = threading.Lock()

# Disk cache (requested):
# - Process cache avoids repeated work within a single backend process.
# - Disk cache avoids re-embedding the full ESGMetrics.xlsx catalog across
#   restarts.
#
# Cache files (default):
#   outputs/catalog_cache/spec_vectors.npz
#   outputs/catalog_cache/spec_vectors.meta.json
#
# The cache is invalidated when any of these change:
#   - ESGMetrics.xlsx file hash (sha256)
#   - embedding model identifier
#   - spec ordering/keys derived from the loaded catalog

_SPEC_DISK_CACHE_LOCK = threading.Lock()


def _outputs_root_dir() -> Path:
    """Return the canonical *uploads* outputs root directory.

    We intentionally derive this from CROSS_CACHE_DIR to avoid hard-coding paths.
    In this codebase CROSS_CACHE_DIR is:
      <uploads>/outputs/cross_analysis
    """
    try:
        return Path(CROSS_CACHE_DIR).parent
    except Exception:
        return Path(__file__).resolve().parents[2] / "outputs"


def _catalog_cache_paths() -> Tuple[Path, Path]:
    root = _outputs_root_dir() / "catalog_cache"
    root.mkdir(parents=True, exist_ok=True)
    # Requested stable names.
    return (root / "spec_vectors.npz", root / "spec_vectors.meta.json")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _embedding_model_id() -> str:
    """A stable identifier used to invalidate disk cache when the embedding model changes."""
    try:
        cfg = _get_processing_config()
        mid = str(getattr(cfg, "embedding_model", "") or "").strip()
        if mid:
            return mid
    except Exception:
        pass
    # Fallbacks
    for k in ("EMBEDDING_MODEL", "embedding_model"):
        v = str(os.getenv(k, "") or "").strip()
        if v:
            return v
    return "unknown"


def _spec_keys(specs: List[ExcelMetricSpec]) -> List[str]:
    """Stable keys describing the *order* of specs in the vectors matrix.

    We store these keys in the npz to guarantee correct alignment between
    vectors and the currently loaded ESGMetrics.xlsx.
    """
    out: List[str] = []
    for sp in specs:
        k = "||".join(
            [
                str(sp.primary or "").strip().lower(),
                str(sp.secondary or "").strip().lower(),
                str(sp.topic or "").strip().lower(),
                str(sp.display_subtopic() or "").strip().lower(),
            ]
        )
        out.append(k)
    return out


def _try_load_spec_vectors_from_disk(*, specs: List[ExcelMetricSpec], catalog_path: Optional[Path]) -> Optional[dict]:
    """Load precomputed spec vectors from disk if valid."""
    if not _env_flag("EXCEL_METRICS_SPEC_VEC_DISK_CACHE", "1"):
        return None

    npz_path, meta_path = _catalog_cache_paths()
    if not (npz_path.exists() and meta_path.exists()):
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Validate model + catalog
    want_model = _embedding_model_id()
    have_model = str(meta.get("embedding_model") or "").strip()
    if have_model != want_model:
        return None

    if catalog_path is not None and catalog_path.exists():
        try:
            want_sha = _sha256_file(catalog_path)
        except Exception:
            want_sha = ""
        have_sha = str(meta.get("catalog_sha256") or "").strip()
        if want_sha and have_sha and want_sha != have_sha:
            return None

    # Validate ordering keys
    want_keys = _spec_keys(specs)
    try:
        with np.load(npz_path, allow_pickle=True) as z:
            keys = z["spec_keys"].tolist()
            if isinstance(keys, np.ndarray):
                keys = keys.tolist()
            keys = [str(x) for x in (keys or [])]
            if keys != want_keys:
                return None

            full_vecs = z["full_vecs"].astype(np.float32)
            topic_vecs = z["topic_vecs"].astype(np.float32)
    except Exception:
        return None

    if full_vecs.shape[0] != len(specs) or topic_vecs.shape[0] != len(specs):
        return None

    logger.info(
        f"[ExcelMetrics] Loaded catalog spec vectors from disk cache: {npz_path} (n={len(specs)})"
    )
    return {"full_vecs": _l2_normalize(full_vecs), "topic_vecs": _l2_normalize(topic_vecs)}


def _save_spec_vectors_to_disk(*, specs: List[ExcelMetricSpec], catalog_path: Optional[Path], full_vecs: np.ndarray, topic_vecs: np.ndarray) -> None:
    """Persist spec vectors to disk using an atomic replace."""
    if not _env_flag("EXCEL_METRICS_SPEC_VEC_DISK_CACHE", "1"):
        return

    npz_path, meta_path = _catalog_cache_paths()
    tmp_npz = npz_path.with_suffix(f".npz.tmp.{os.getpid()}")
    tmp_meta = meta_path.with_suffix(f".json.tmp.{os.getpid()}")

    keys = _spec_keys(specs)
    meta = {
        "embedding_model": _embedding_model_id(),
        "catalog_path": str(catalog_path) if catalog_path else "",
        "catalog_sha256": _sha256_file(catalog_path) if (catalog_path and catalog_path.exists()) else "",
        "n_specs": len(specs),
        "dim": int(full_vecs.shape[1]) if full_vecs.ndim == 2 else 0,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }

    try:
        np.savez_compressed(
            tmp_npz,
            full_vecs=full_vecs.astype(np.float32),
            topic_vecs=topic_vecs.astype(np.float32),
            spec_keys=np.array(keys, dtype=object),
        )
        tmp_npz.replace(npz_path)

        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_meta.replace(meta_path)

        logger.info(
            f"[ExcelMetrics] Saved catalog spec vectors disk cache: {npz_path} (n={len(specs)})"
        )
    except Exception as e:
        # Best-effort only. Cache failures must not break extraction.
        try:
            if tmp_npz.exists():
                tmp_npz.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if tmp_meta.exists():
                tmp_meta.unlink(missing_ok=True)
        except Exception:
            pass
        logger.warning(f"[ExcelMetrics] Failed to persist spec vector cache (ignored): {e}")


def _spec_key(spec: ExcelMetricSpec) -> tuple:
    return (spec.primary, spec.secondary, spec.topic, spec.display_subtopic())


def _spec_full_text(spec: ExcelMetricSpec) -> str:
    # Put Topic/Sub-topic/Definitions first as the strongest signal.
    parts = [
        f"Topic: {spec.topic}",
        f"Sub-topic: {spec.display_subtopic()}",
    ]
    if (spec.definition or '').strip():
        parts.append(f"Definition: {spec.definition}")
    # Navigation labels can still help disambiguate without forcing group membership.
    if (spec.secondary or '').strip():
        parts.append(f"Secondary: {spec.secondary}")
    if (spec.primary or '').strip():
        parts.append(f"Primary: {spec.primary}")
    # Units can help separate absolute vs intensity, % vs tCO2e, etc.
    if spec.unit_items:
        parts.append("Units: " + "; ".join(list(spec.unit_items)[:6]))
    return "\n".join(parts)


def _spec_topic_text(spec: ExcelMetricSpec) -> str:
    return str(spec.topic or '').strip()


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat.reshape(1, -1)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)


def _embed_texts(texts: List[str]) -> np.ndarray:
    # Embedding model is shared with cross_analysis; guard to avoid GPU contention.
    model = get_embedding_model()
    with _RETRIEVAL_LOCK:
        vec = model.encode(
            texts,
            batch_size=int(os.getenv('EXCEL_METRICS_EMBED_BATCH', '32') or '32'),
            show_progress_bar=False,
        )
    return _l2_normalize(np.asarray(vec, dtype=np.float32))


def _get_spec_vectors(all_specs: List[ExcelMetricSpec], *, catalog_path: Optional[Path] = None) -> dict:
    """Build (and cache) catalog vectors for embedding matching.

    Requirements:
    - Process-level cache to avoid repeated work within one backend process.
    - Disk cache (npz + meta.json) to avoid re-embedding the full catalog across
      restarts.
    """
    global _SPEC_VEC_CACHE
    if _SPEC_VEC_CACHE is not None:
        return _SPEC_VEC_CACHE

    with _SPEC_VEC_LOCK:
        if _SPEC_VEC_CACHE is not None:
            return _SPEC_VEC_CACHE

        specs = list(all_specs or [])
        full_texts = [_spec_full_text(sp) for sp in specs]
        topic_texts = [_spec_topic_text(sp) for sp in specs]

        # 1) Try disk cache first (fast path)
        loaded = None
        with _SPEC_DISK_CACHE_LOCK:
            loaded = _try_load_spec_vectors_from_disk(specs=specs, catalog_path=catalog_path)

        if loaded is not None:
            full_vecs = loaded["full_vecs"]
            topic_vecs = loaded["topic_vecs"]
        else:
            # 2) Compute vectors once (slow path) and persist to disk cache (best-effort)
            full_vecs = _embed_texts(full_texts) if specs else np.zeros((0, 1), dtype=np.float32)
            topic_vecs = _embed_texts(topic_texts) if specs else np.zeros((0, 1), dtype=np.float32)
            with _SPEC_DISK_CACHE_LOCK:
                _save_spec_vectors_to_disk(
                    specs=specs,
                    catalog_path=catalog_path,
                    full_vecs=full_vecs,
                    topic_vecs=topic_vecs,
                )

        key_to_idx = {_spec_key(sp): i for i, sp in enumerate(specs)}
        primary_to_idx: dict = {}
        group_to_idx: dict = {}
        full_lower = [t.lower() for t in full_texts]

        for i, sp in enumerate(specs):
            primary_to_idx.setdefault(sp.primary, []).append(i)
            group_to_idx.setdefault((sp.primary, sp.secondary), []).append(i)

        _SPEC_VEC_CACHE = {
            'specs': specs,
            'full_texts': full_texts,
            'topic_texts': topic_texts,
            'full_vecs': full_vecs,
            'topic_vecs': topic_vecs,
            'key_to_idx': key_to_idx,
            'primary_to_idx': primary_to_idx,
            'group_to_idx': group_to_idx,
            'full_lower': full_lower,
        }
        return _SPEC_VEC_CACHE


def _token_hints(label: str) -> List[str]:
    t = (label or '').lower()
    hints: List[str] = []
    for k in [
        'scope 1', 'scope 2', 'scope 3',
        'market-based', 'location-based',
        'intensity', 'per revenue', 'per net revenue', 'per production',
        'renewable', 'renewables',
        'water withdrawal', 'water consumption',
        'trir', 'ltifr',
    ]:
        if k in t:
            hints.append(k)
    return hints


def _filter_candidate_indices(indices: List[int], vecs: dict, label: str) -> List[int]:
    hints = _token_hints(label)
    if not hints:
        return indices
    full_lower = vecs.get('full_lower') or []
    out: List[int] = []
    for i in indices:
        if i < 0 or i >= len(full_lower):
            continue
        txt = full_lower[i]
        ok = True
        for h in hints:
            # Only enforce high-precision disambiguators.
            if h in ('market-based', 'location-based') and h not in txt:
                ok = False
                break
            if h.startswith('scope') and h not in txt:
                ok = False
                break
        if ok:
            out.append(i)
    return out or indices


def _best_spec_by_embedding(
    *,
    query_vec: np.ndarray,
    cand_idx: List[int],
    vecs: dict,
    prefer_group: tuple,
) -> tuple:
    """Return (best_index, best_score_full, best_score_topic, fused_score)."""
    if not cand_idx:
        return (-1, 0.0, 0.0, 0.0)

    full_vecs = vecs['full_vecs']
    topic_vecs = vecs['topic_vecs']

    q = query_vec.reshape(-1).astype(np.float32)
    f = full_vecs[cand_idx] @ q
    t = topic_vecs[cand_idx] @ q

    w_topic = float(os.getenv('EXCEL_METRICS_MATCH_W_TOPIC', '0.35') or '0.35')
    w_full = 1.0 - w_topic
    fused = (w_full * f) + (w_topic * t)

    # Light boost if candidate is inside the current (primary, secondary) group.
    boost = float(os.getenv('EXCEL_METRICS_MATCH_GROUP_BOOST', '0.03') or '0.03')
    if boost > 0 and prefer_group:
        grp_idx = set(vecs.get('group_to_idx', {}).get(prefer_group, []) or [])
        for j, idx in enumerate(cand_idx):
            if idx in grp_idx:
                fused[j] = fused[j] + boost

    best_pos = int(np.argmax(fused))
    best_idx = int(cand_idx[best_pos])
    return (best_idx, float(f[best_pos]), float(t[best_pos]), float(fused[best_pos]))


def _match_spec_for_item(
    *,
    label: str,
    detail: str,
    primary: str,
    secondary: str,
    group_specs: List[ExcelMetricSpec],
    all_specs: List[ExcelMetricSpec],
    catalog_path: Optional[Path] = None,
) -> Optional[ExcelMetricSpec]:
    """Match an extracted label to the best catalog spec using embeddings.

    Strategy:
    - First try within the current group (primary, secondary).
    - If confidence is low, broaden to the same primary.
    - As a last resort, search the entire catalog.

    This removes the brittle requirement that LLM must output exact (Topic, Sub-topic)
    strings, while still assigning a canonical catalog metric.
    """
    vecs = _get_spec_vectors(all_specs, catalog_path=catalog_path)
    key_to_idx = vecs['key_to_idx']

    qtext = (label or '').strip()
    dtext = (detail or '').strip()
    if dtext:
        qtext = (qtext + ' ' + dtext).strip()
    if not qtext:
        return None

    qvec = _embed_texts([qtext])[0]

    # Candidate indices
    group_idx = []
    for sp in (group_specs or []):
        i = key_to_idx.get(_spec_key(sp))
        if i is not None:
            group_idx.append(int(i))

    group_idx = _filter_candidate_indices(group_idx, vecs, label)

    # Thresholds
    th_group = float(os.getenv('EXCEL_METRICS_MATCH_THRESHOLD_GROUP', '0.26') or '0.26')
    th_primary = float(os.getenv('EXCEL_METRICS_MATCH_THRESHOLD_PRIMARY', '0.24') or '0.24')
    th_global = float(os.getenv('EXCEL_METRICS_MATCH_THRESHOLD_GLOBAL', '0.22') or '0.22')

    prefer_group = (primary, secondary)

    bidx, _, _, fused = _best_spec_by_embedding(query_vec=qvec, cand_idx=group_idx, vecs=vecs, prefer_group=prefer_group)
    if bidx >= 0 and fused >= th_group:
        return vecs['specs'][bidx]

    # Broaden to primary
    primary_idx = list(vecs.get('primary_to_idx', {}).get(primary, []) or [])
    primary_idx = _filter_candidate_indices(primary_idx, vecs, label)
    bidx2, _, _, fused2 = _best_spec_by_embedding(query_vec=qvec, cand_idx=primary_idx, vecs=vecs, prefer_group=prefer_group)
    if bidx2 >= 0 and fused2 >= th_primary:
        return vecs['specs'][bidx2]

    # Global fallback
    all_idx = list(range(len(vecs['specs'])))
    all_idx = _filter_candidate_indices(all_idx, vecs, label)
    bidx3, _, _, fused3 = _best_spec_by_embedding(query_vec=qvec, cand_idx=all_idx, vecs=vecs, prefer_group=prefer_group)
    if bidx3 >= 0 and fused3 >= th_global:
        return vecs['specs'][bidx3]

    return None


def _match_items_to_specs(
    *,
    primary: str,
    secondary: str,
    group_specs: List[ExcelMetricSpec],
    items: List[dict],
    all_specs: List[ExcelMetricSpec],
    catalog_path: Optional[Path] = None,
) -> List[Tuple[ExcelMetricSpec, dict]]:
    """Map raw extracted items to catalog specs.

    Priority:
    1) exact Topic/Sub-topic match if present (backward compatible)
    2) embedding match using label/detail vs (Topic/Sub-topic/Definitions)
    """
    out: List[Tuple[ExcelMetricSpec, dict]] = []
    if not items:
        return out

    exact_map = {(s.topic or '').strip().lower(): s for s in (group_specs or [])}
    exact_pair = {((s.topic or '').strip().lower(), (s.display_subtopic() or '').strip().lower()): s for s in (group_specs or [])}

    for it in items:
        if not isinstance(it, dict):
            continue

        topic = str(it.get('Topic') or '').strip()
        sub = str(it.get('Sub-topic') or '').strip()
        if topic:
            k = (topic.lower(), (sub or topic).lower())
            if k in exact_pair:
                out.append((exact_pair[k], it))
                continue
            if topic.lower() in exact_map and not sub:
                out.append((exact_map[topic.lower()], it))
                continue

        label = str(it.get('label') or it.get('Label') or '').strip()
        if not label:
            # fallback: synthesize a label from legacy fields
            if topic:
                label = (topic + ' ' + (sub or '')).strip()
            else:
                continue

        detail = str(it.get('detail') or '').strip()
        spec = _match_spec_for_item(
            label=label,
            detail=detail,
            primary=primary,
            secondary=secondary,
            group_specs=group_specs,
            all_specs=all_specs,
            catalog_path=catalog_path,
        )
        if spec is None:
            continue

        it2 = dict(it)
        it2['label'] = label
        out.append((spec, it2))

    return out


def _augment_evidence_for_pages(
    file_id: str,
    evidence_segments: List[Tuple[Dict, float]],
    pages_needed: List[int],
) -> List[Tuple[Dict, float]]:
    """Ensure evidence contains segments for referenced pages.

    _post_filter_and_normalize only looks at the first ~28 evidence segments
    when building page->text map. If LLM references a page not present in the
    top-N candidates, we prepend all segments from that page to avoid false
    negatives in the anti-hallucination gate.
    """
    pages = sorted({int(p) for p in (pages_needed or []) if int(p) > 0})
    if not pages:
        return evidence_segments

    try:
        art = load_artifacts(file_id)
    except Exception:
        return evidence_segments

    # Collect segments for needed pages
    page_segs: List[Dict] = []
    for seg in art.segments:
        try:
            pn = int(seg.get('page_number') or 0)
        except Exception:
            pn = 0
        if pn in pages:
            page_segs.append(seg)

    if not page_segs:
        return evidence_segments

    # Dedup by segment_id and prepend (so they are included in page_to_text build)
    seen = set()
    merged: List[Tuple[Dict, float]] = []

    for seg in page_segs:
        sid = seg.get('segment_id')
        if not sid or sid in seen:
            continue
        seen.add(sid)
        merged.append((seg, 0.0))
        if len(merged) >= 40:
            break

    for seg, sc in (evidence_segments or []):
        sid = (seg or {}).get('segment_id')
        if not sid or sid in seen:
            continue
        seen.add(sid)
        merged.append((seg, float(sc) if sc is not None else 0.0))
        if len(merged) >= max(60, len(evidence_segments or [])):
            break

    return merged


def _normalize_matched_items(
    *,
    file_id: str,
    name: str,
    matched: List[Tuple[ExcelMetricSpec, dict]],
    evidence_segments: List[Tuple[Dict, float]],
) -> List[dict]:
    """Normalize matched items by delegating to the existing strict normalizer."""
    if not matched:
        return []

    # Collect referenced pages
    pages: List[int] = []
    for sp, it in matched:
        try:
            pages.append(int(it.get('page') or 0))
        except Exception:
            pass

    evidence_aug = _augment_evidence_for_pages(file_id, evidence_segments, pages)

    buckets: dict = {}
    for sp, it in matched:
        k = _spec_key(sp)
        it2 = dict(it)
        # Force canonical labels; LLM label differences are handled in embedding match.
        it2['Topic'] = sp.topic
        it2['Sub-topic'] = sp.display_subtopic()
        buckets.setdefault(k, {'spec': sp, 'items': []})['items'].append(it2)

    out: List[dict] = []
    for k, obj in buckets.items():
        sp = obj['spec']
        items = obj['items']
        out.extend(
            _post_filter_and_normalize(
                file_id=file_id,
                name=name,
                primary=sp.primary,
                secondary=sp.secondary,
                specs=[sp],
                extracted=items,
                evidence_segments=evidence_aug,
            )
        )

    return out

def _llm_extract_group(
    *,
    primary: str,
    secondary: str,
    specs: List[ExcelMetricSpec],
    segments: List[Tuple[Dict, float]],
    max_items: int = 120,
) -> List[dict]:
    """LLM extraction for a group. Returns items with Topic/Sub-topic + year/data/unit/page/detail."""

    if not _env_flag("EXCEL_METRICS_LLM_EXTRACT_ENABLED", "1"):
        return []

    client = _get_llm_client()
    cfg = _get_processing_config()
    if client is None:
        return []

    model = cfg.llm_model or "qwen-plus"

    def _strip_code_fences(s: str) -> str:
        s = (s or "").strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*", "", s)
            s = re.sub(r"\s*```$", "", s)
        return s.strip()

    def _extract_json_candidate(s: str) -> str:
        s = _strip_code_fences(s)
        # Prefer object form, then array form.
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            return m.group(0)
        m = re.search(r"\[[\s\S]*\]", s)
        if m:
            return m.group(0)
        return s

    def _safe_json_load(s: str):
        s = _extract_json_candidate(s)
        # Common LLM JSON mistakes
        s = s.replace("NaN", "null").replace("Infinity", "null").replace("-Infinity", "null")
        # Remove trailing commas
        for _ in range(4):
            s2 = re.sub(r",\s*([}\]])", r"\1", s)
            if s2 == s:
                break
            s = s2
        return json.loads(s)

    # Provide compact evidence (top segments)
    segs = segments[: min(len(segments), int(os.getenv("EXCEL_METRICS_EVIDENCE_K", "24") or "24"))]
    ctx_lines: List[str] = []
    for seg, _s in segs:
        # PDF pages are 1-indexed in our UI; default to 1 when missing.
        page = int(seg.get("page_number") or 0) or 1
        txt = _clean_snippet_for_llm(seg.get("content") or "")
        if not txt:
            continue
        ctx_lines.append(f"[p{page}] {txt[:1100]}")
    context = "\n\n".join(ctx_lines)[:9500]

    # Provide catalog constraints to improve unit extraction and reduce metric confusion.
    allowed_json = json.dumps(
        [
            {
                "Topic": s.topic,
                "Sub-topic": s.display_subtopic(),
                "Expected Unit": _canonical_unit(s) or (s.unit_items[0] if s.unit_items else None),
                "Unit Synonyms": list(s.unit_items or ())[:8],
                "Definition": (s.definition or "")[:220],
            }
            for s in specs
        ],
        ensure_ascii=False,
    )

    sys = (
        "You are a strict ESG disclosure extraction engine. "
        "Extract ONLY values explicitly stated in the evidence. "
        "Never invent, never calculate, never guess. "
        "If uncertain, omit."
    )

    max_items = int(os.getenv("EXCEL_METRICS_MAX_ITEMS", str(min(80, max_items))) or str(min(80, max_items)))

    usr = f"""Task: Extract ESG numeric metrics for Cross Analysis.

Primary Navigation: {primary}
Secondary Navigation: {secondary}

Important:
- The report may use wording that differs from the catalog Topic/Sub-topic strings.
- Your goal is to extract accurate numbers with accurate pages/years/units, and provide the metric label as it appears in the evidence.
- Do NOT invent, compute, or infer missing numbers.

Catalog hints (JSON). Use this to understand definitions and expected units, but you do NOT need to copy the exact Topic/Sub-topic strings:
{allowed_json}

Output requirements:
- You must output a JSON object: {{"items": [ ... ]}}.
- "items" MUST contain at most {max_items} objects.
- Prefer the most complete rows (valid page+year+unit+value). Avoid duplicates.
- Each item MUST include at least these keys:
  label (string; the metric label/row/column header wording from the evidence),
  page (integer page number; must match the [pN] evidence tag),
  year (YYYY string; if multiple years exist, output multiple items),
  data (string or number; preserve decimals; do not change magnitude; keep sign),
  unit (string or null),
  detail (string; <= 25 words; explain what the value represents using evidence wording only).
- Optionally, you MAY include Topic and Sub-topic if you can map confidently to the catalog.
- The numeric value MUST appear in the evidence on the referenced page.
- Prefer table KPI values.

Evidence:
{context}
"""

    try:

        # Prefer strict JSON object mode (supported by OpenAI-style clients; fallback if unsupported).
        try:
            resp = _llm_call_with_retry(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                ),
                max_attempts=_env_int("EXCEL_METRICS_LLM_RETRY", 3),
                base_sleep=float(os.getenv("EXCEL_METRICS_LLM_RETRY_SLEEP", "0.8") or "0.8"),
            )
        except Exception:
            resp = _llm_call_with_retry(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                    temperature=0.0,
                ),
                max_attempts=_env_int("EXCEL_METRICS_LLM_RETRY", 3),
                base_sleep=float(os.getenv("EXCEL_METRICS_LLM_RETRY_SLEEP", "0.8") or "0.8"),
            )

        content = (resp.choices[0].message.content or "").strip()

        def _load_items(text: str) -> List[dict]:
            payload = _safe_json_load(text)
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                raw_items = payload.get("items") or []
            elif isinstance(payload, list):
                raw_items = payload
            else:
                return []
            out: List[dict] = []
            for it in raw_items[:max_items]:
                if isinstance(it, dict):
                    out.append(it)
            return out

        items = _load_items(content)

        # One repair retry when the model returns non-parseable JSON.
        if not items:
            try:
                repair_usr = (
                    "Your previous output was not valid JSON or did not match the required schema. "
                    "Return ONLY a valid JSON object in the exact shape {\"items\": [...]} with at most 25 items. "
                    "Use double quotes for all keys and string values."
                )
                try:
                    resp2 = _llm_call_with_retry(
                        lambda: client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": sys},
                                {"role": "user", "content": usr},
                                {"role": "assistant", "content": content},
                                {"role": "user", "content": repair_usr},
                            ],
                            temperature=0.0,
                            response_format={"type": "json_object"},
                        ),
                        max_attempts=_env_int("EXCEL_METRICS_LLM_RETRY", 3),
                        base_sleep=float(os.getenv("EXCEL_METRICS_LLM_RETRY_SLEEP", "0.8") or "0.8"),
                    )
                except Exception:
                    resp2 = _llm_call_with_retry(
                        lambda: client.chat.completions.create(
                            model=model,
                            messages=[
                                {"role": "system", "content": sys},
                                {"role": "user", "content": usr},
                                {"role": "assistant", "content": content},
                                {"role": "user", "content": repair_usr},
                            ],
                            temperature=0.0,
                        ),
                        max_attempts=_env_int("EXCEL_METRICS_LLM_RETRY", 3),
                        base_sleep=float(os.getenv("EXCEL_METRICS_LLM_RETRY_SLEEP", "0.8") or "0.8"),
                    )
                content2 = (resp2.choices[0].message.content or "").strip()
                items = _load_items(content2)
            except Exception:
                pass

        return items
    except Exception as e:
        logger.warning(f"[ExcelMetrics] LLM extract failed; return empty. err={e}")
        return []


def _llm_extract_spec(
    *,
    primary: str,
    secondary: str,
    spec: ExcelMetricSpec,
    segments: List[Tuple[Dict, float]],
    max_items: int = 8,
) -> List[dict]:
    """Targeted LLM extraction for a single metric spec.

    Used as a recall backstop when group extraction misses a metric.
    """

    if not _env_flag("EXCEL_METRICS_LLM_EXTRACT_ENABLED", "1"):
        return []

    client = _get_llm_client()
    cfg = _get_processing_config()
    if client is None:
        return []

    model = cfg.llm_model or "qwen-plus"

    # Evidence: keep it tight for targeted extraction
    segs = segments[: min(len(segments), int(os.getenv("EXCEL_METRICS_SPEC_EVIDENCE_K", "16") or "16"))]
    ctx_lines: List[str] = []
    for seg, _s in segs:
        page = int(seg.get("page_number") or 0) or 1
        txt = _clean_snippet_for_llm(seg.get("content") or "")
        if not txt:
            continue
        ctx_lines.append(f"[p{page}] {txt[:1200]}")
    context = "\n\n".join(ctx_lines)[:9000]

    exp_unit = _canonical_unit(spec) or (spec.unit_items[0] if spec.unit_items else None)
    unit_syn = list(spec.unit_items or ())[:12]
    definition = (spec.definition or "").strip()[:900]

    sys = (
        "You are a strict ESG disclosure extraction engine. "
        "Extract ONLY values explicitly stated in the evidence. "
        "Never invent, never calculate, never guess. "
        "If uncertain, omit."
    )

    max_items = int(os.getenv("EXCEL_METRICS_SPEC_MAX_ITEMS", str(min(max_items, 8))) or str(min(max_items, 8)))

    usr = f"""Task: Extract the requested ESG metric for Cross Analysis.

Primary Navigation: {primary}
Secondary Navigation: {secondary}

Metric:
- Topic: {spec.topic}
- Sub-topic: {spec.display_subtopic()}
- Definition (guidance only; do not invent): {definition}

Unit guidance:
- Expected unit: {exp_unit}
- Unit synonyms you may see: {unit_syn}
- Prefer the unit explicitly shown in the evidence (table header/label). If the unit is not stated anywhere, set unit=null.

Output requirements:
- Return ONLY a JSON object in the exact shape: {{"items": [ ... ]}}.
- The "items" array MUST contain at most {max_items} objects.
- Each item must include keys exactly:
  Topic, Sub-topic, page, year, data, unit, detail
- page must match the [pN] evidence tag.
- year must be YYYY.
- data MUST be a number/string that appears in the evidence. Do not compute or derive.
- detail <= 25 words and must reflect evidence wording.

Evidence:
{context}
"""

    try:
        try:
            resp = _llm_call_with_retry(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                ),
                max_attempts=_env_int("EXCEL_METRICS_LLM_RETRY", 3),
                base_sleep=float(os.getenv("EXCEL_METRICS_LLM_RETRY_SLEEP", "0.8") or "0.8"),
            )
        except Exception:
            resp = _llm_call_with_retry(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
                    temperature=0.0,
                ),
                max_attempts=_env_int("EXCEL_METRICS_LLM_RETRY", 3),
                base_sleep=float(os.getenv("EXCEL_METRICS_LLM_RETRY_SLEEP", "0.8") or "0.8"),
            )
        content = (resp.choices[0].message.content or "").strip()

        # Reuse the existing safe loader from group extractor (minimal duplicate logic)
        def _strip_code_fences(s: str) -> str:
            s = (s or "").strip()
            if s.startswith("```"):
                s = re.sub(r"^```(?:json)?\s*", "", s)
                s = re.sub(r"\s*```$", "", s)
            return s.strip()

        s = _strip_code_fences(content)
        m = re.search(r"\{[\s\S]*\}", s)
        if m:
            s = m.group(0)
        s = s.replace("NaN", "null").replace("Infinity", "null").replace("-Infinity", "null")
        for _ in range(4):
            s2 = re.sub(r",\s*([}\]])", r"\1", s)
            if s2 == s:
                break
            s = s2

        payload = json.loads(s)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            items = [x for x in payload.get("items")[:max_items] if isinstance(x, dict)]
        elif isinstance(payload, list):
            items = [x for x in payload[:max_items] if isinstance(x, dict)]
        else:
            items = []
        return items
    except Exception as e:
        logger.warning(f"[ExcelMetrics] LLM spec extract failed; err={e}")
        return []


def _rules_extract_group(
    *,
    primary: str,
    secondary: str,
    specs: List[ExcelMetricSpec],
    segments: List[Tuple[Dict, float]],
    max_items: int = 80,
) -> List[dict]:
    """Conservative regex-based fallback extraction.

    Only used when LLM is unavailable or returns empty.
    This fallback prioritizes explicit year + unit-attached values.
    """
    out: List[dict] = []
    # cap segments
    segs = segments[: min(len(segments), 40)]

    def pick_terms(s: ExcelMetricSpec) -> List[str]:
        # Prefer very specific tokens (Scope 1 / TRIR / LTIFR / etc.)
        terms = [t for t in s.query_pack_en if any(c.isdigit() for c in t) or t.isupper()]
        if not terms:
            terms = [s.topic, s.display_subtopic()]
        # de-dup
        uniq: List[str] = []
        seen = set()
        for t in terms:
            tl = t.lower()
            if tl in seen:
                continue
            seen.add(tl)
            uniq.append(t)
        return uniq[:6]

    for spec in specs:
        terms = pick_terms(spec)
        topic = spec.topic
        sub = spec.display_subtopic()

        for seg, _s in segs:
            txt = str(seg.get("content") or "")
            if not txt:
                continue
            ltxt = txt.lower()
            if not any(t.lower() in ltxt for t in terms):
                continue
            try:
                page_i = int(seg.get("page_number") or 0) or 1
            except Exception:
                page_i = 1

            years = list(dict.fromkeys(_YEAR_RE.findall(txt)))  # preserve order, de-dup
            if not years:
                continue

            # Prefer explicit value+unit
            best_val = None
            best_unit = None
            m = _NUM_UNIT_RE.search(txt)
            if m:
                best_val = m.group("val")
                best_unit = m.group("unit")
            else:
                mp = _PERCENT_RE.search(txt)
                if mp:
                    best_val = mp.group("val") or mp.group("val2")
                    best_unit = "%"
                else:
                    mc = _COUNT_RE.search(txt)
                    if mc:
                        best_val = mc.group("val")
                        best_unit = mc.group("unit")

            if not best_val:
                continue

            # Choose the most recent year mentioned on that segment
            year = years[0]
            # Keep short interpretation from sentence prefix
            detail = _clean_snippet_for_llm(txt)[:180]
            out.append(
                {
                    "Topic": topic,
                    "Sub-topic": sub,
                    "page": page_i,
                    "year": year,
                    "data": best_val,
                    "unit": best_unit,
                    "detail": detail,
                }
            )
            if len(out) >= max_items:
                return out
            # Only one hit per spec for fallback (avoid noisy duplicates)
            break

    return out


def _post_filter_and_normalize(
    *,
    file_id: str,
    name: str,
    primary: str,
    secondary: str,
    specs: List[ExcelMetricSpec],
    extracted: List[dict],
    evidence_segments: List[Tuple[Dict, float]],
) -> List[dict]:
    """Apply hard gates and normalize fields. Returns final records (dicts)."""

    # Build evidence text map by page for value-in-context check
    page_to_text: Dict[int, str] = {}
    for seg, _s in evidence_segments[: min(len(evidence_segments), 28)]:
        try:
            p = int(seg.get("page_number") or 0) or 0
        except Exception:
            continue
        txt = str(seg.get("content") or "")
        if not txt:
            continue
        page_to_text[p] = (page_to_text.get(p, "") + "\n" + txt)[:6000]

    allowed = {(s.topic, s.display_subtopic()): s for s in specs}

    def _infer_unit_from_page(spec: ExcelMetricSpec, page_i: int) -> Optional[str]:
        """Infer unit from the page evidence by scanning for catalog unit tokens."""
        txt = (page_to_text.get(page_i) or "")
        if not txt:
            return None
        ltxt = txt.lower()
        for tok in _unit_tokens(spec):
            if tok and tok in ltxt:
                return tok
        return None

    def _unit_family(u: Optional[str]) -> str:
        ul = (u or "").lower()
        if not ul:
            return "unknown"
        if "co2" in ul:
            return "emissions"
        if any(x in ul for x in ["kwh", "mwh", "gj", "tj", "pj", "mmbtu", "toe", "therm", "gwh"]):
            return "energy"
        if any(x in ul for x in ["m3", "m³", "lit", "l "]):
            return "water"
        if ul.strip() in ("%", "percent", "percentage", "fraction", "ratio"):
            return "percent"
        if any(x in ul for x in ["usd", "eur", "cny", "rmb", "hkd", "sgd", "$"]):
            return "money"
        if any(x in ul for x in ["cases", "incidents", "events", "complaints", "recalls", "次", "起", "件", "例", "人", "项", "个"]):
            return "count"
        return "other"

    def _expected_family(spec: ExcelMetricSpec) -> str:
        cu = (_canonical_unit(spec) or "").lower()
        if not cu:
            # Fall back to units_allow tokens.
            for t in _unit_tokens(spec):
                fam = _unit_family(t)
                if fam not in ("unknown", "other"):
                    return fam
            return "unknown"
        return _unit_family(cu)

    def _split_scaled_unit(unit_phrase: Optional[str]) -> Tuple[Optional[str], float]:
        """Handle explicit scaling words inside unit phrases (e.g., 'million kWh')."""
        if not unit_phrase:
            return None, 1.0
        up = (unit_phrase or "").strip().lower()
        up = up.replace("mn.", "mn").replace("bn.", "bn")
        scale = 1.0
        base = up

        if base.startswith("million "):
            scale *= 1_000_000.0
            base = base[len("million ") :].strip()
        elif base.startswith("mn "):
            scale *= 1_000_000.0
            base = base[len("mn ") :].strip()
        elif base.startswith("billion "):
            scale *= 1_000_000_000.0
            base = base[len("billion ") :].strip()
        elif base.startswith("bn "):
            scale *= 1_000_000_000.0
            base = base[len("bn ") :].strip()

        # Special: ktoe as a unit token.
        if base == "ktoe":
            base = "toe"
            scale *= 1000.0

        return base, scale

    out: List[dict] = []
    seen = set()
    for it in extracted:
        if not isinstance(it, dict):
            continue
        topic = str(it.get("Topic") or "").strip()
        sub = str(it.get("Sub-topic") or "").strip()
        if not sub:
            sub = topic
        spec = allowed.get((topic, sub))
        if spec is None:
            continue

        # page
        page = it.get("page")
        try:
            page_i = int(page)
        except Exception:
            continue
        if page_i < 0:
            continue

        year = str(it.get("year") or "").strip()
        if not re.fullmatch(r"20\d{2}", year):
            # fallback: attempt to find first year in evidence on that page
            txt = page_to_text.get(page_i, "")
            mm = _YEAR_RE.search(txt)
            if mm:
                year = mm.group(1)
        if not re.fullmatch(r"20\d{2}", year):
            continue

        data_str = _to_data_str(it.get("data", None))
        if not data_str or not str(data_str).strip():
            continue
        data_str = str(data_str).strip()

        # Keep a raw copy for anti-hallucination checks (conversion may change magnitude).
        data_raw = data_str

        # Unit extraction:
        # 1) take model output
        # 2) split from data like '25%'
        # 3) infer from the page using catalog units
        unit_raw = it.get("unit")
        unit = _norm_unit_phrase(unit_raw)
        if not unit:
            u_guess, data_str2 = _split_unit_from_data(data_str)
            if u_guess:
                unit = _norm_unit_phrase(u_guess)
                data_str = data_str2
                data_raw = data_str
        if not unit:
            unit = _infer_unit_from_page(spec, page_i)
            unit = _norm_unit_phrase(unit)

        # Anti-hallucination: the extracted numeric string must appear in the evidence.
        ev_txt = page_to_text.get(page_i, "")
        if ev_txt and not _value_in_context(data_str=data_raw, context=ev_txt):
            continue

        # Normalize + convert to canonical unit when possible.
        canon = _canonical_unit(spec)
        if canon:
            # If the unit is a phrase with scaling (e.g., 'million kWh'), apply scaling and reduce to base.
            base_u, scale = _split_scaled_unit(unit)
            base_u = _norm_unit_phrase(base_u)
            # Parse numeric
            fv = _parse_float(data_str)
            if fv is not None:
                fv = fv * float(scale)
                if base_u:
                    conv = _convert_value(fv, from_unit=base_u, to_unit=canon)
                    if conv is not None:
                        data_str = _format_number(conv)
                        unit = canon
                    else:
                        # If base unit equals canonical (normalized), keep.
                        if base_u.lower().replace(" ", "") == canon.lower().replace(" ", ""):
                            unit = canon
                else:
                    unit = canon

        # Soft gate: if unit exists and looks incompatible with expected family, drop.
        exp_fam = _expected_family(spec)
        got_fam = _unit_family(unit)
        if unit and exp_fam not in ("unknown",) and got_fam not in ("unknown", "other") and got_fam != exp_fam:
            continue

        detail = str(it.get("detail") or "").strip() or None
        if detail:
            # keep it concise
            if len(detail) > 260:
                detail = detail[:260]

        rec = {
            "id": file_id,
            "name": name,
            "Primary Navigation": primary,
            "Secondary Navigation": secondary,
            "Topic": topic,
            "Sub-topic": sub,
            "page": page_i,
            "data": data_str,
            "year": year,
            "unit": _norm_unit_phrase(unit),
            "detail": detail,
        }
        k = (file_id, secondary, topic, sub, year, data_str, unit or "", detail or "")
        if k in seen:
            continue
        seen.add(k)
        out.append(rec)

    return out




def _resolve_conflicts(records: List[dict]) -> List[dict]:
    """Resolve conflicts within the same (id, Secondary, Topic, Sub-topic, year).

    If multiple candidates exist for the same key, keep the best one.
    Scoring is deterministic and based on evidence completeness (unit/detail/page).
    """

    def score(r: dict) -> tuple:
        # Higher is better. tuple ensures stable tie-breaking.
        unit = r.get("unit")
        detail = r.get("detail")
        page = r.get("page")
        data = str(r.get("data") or "")
        try:
            page_i = int(page)
        except Exception:
            page_i = -1
        return (
            1 if unit else 0,
            1 if detail else 0,
            1 if page_i > 0 else 0,
            len(data),
            -page_i,  # prefer smaller page when otherwise equal
        )

    buckets = {}
    order = []
    for r in records:
        if not isinstance(r, dict):
            continue
        k = (
            str(r.get("id") or "").strip(),
            r.get("Secondary Navigation"),
            r.get("Topic"),
            r.get("Sub-topic"),
            r.get("year"),
        )
        if k not in buckets:
            buckets[k] = r
            order.append(k)
        else:
            if score(r) > score(buckets[k]):
                buckets[k] = r

    return [buckets[k] for k in order if k in buckets]


def extract_excel_metrics_for_files(
    *,
    file_ids: List[str],
    catalog_path: Optional[str] = None,
    top_n_candidates: int = 420,
    persist_output: bool = True,
) -> List[dict]:
    """Extract ESG metrics for multiple reports based on the Excel catalog."""

    # -----------------------------
    # Cache-first: if output/all_records.json exists, reuse it.
    # -----------------------------
    output_dir = CROSS_CACHE_DIR / "output"
    processed_ids_path = output_dir / "processed_ids.json"
    # Backward compatibility: older builds wrote to excel_output/all_records.json
    cache_candidates = [
        output_dir / "all_records.json",
        (CROSS_CACHE_DIR / "excel_output" / "all_records.json"),
    ]
    cache_file = next((p for p in cache_candidates if p.exists()), cache_candidates[0])
    cached_all: List[dict] = []
    processed_ids: set[str] = set()
    if persist_output and cache_file.exists():
        try:
            cached_all = json.loads(cache_file.read_text(encoding="utf-8"))
            if not isinstance(cached_all, list):
                cached_all = []
        except Exception:
            cached_all = []

    # If we loaded from legacy excel_output, mirror it into the canonical output/... path
    if persist_output and cached_all and cache_file.parent.name == "excel_output":
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "all_records.json").write_text(
                json.dumps(cached_all, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # Track completion by file_id even if a report yields 0 extracted rows.
    # Without this, a legitimate "no match" report is treated as "missing" forever
    # and will be repeatedly re-extracted (costly and causes frontend loops).
    if persist_output:
        processed_candidates = [
            processed_ids_path,
            (CROSS_CACHE_DIR / "excel_output" / "processed_ids.json"),
        ]
        processed_file = next((p for p in processed_candidates if p.exists()), processed_candidates[0])
        if processed_file.exists():
            try:
                raw = json.loads(processed_file.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    processed_ids = {str(x).strip() for x in raw if str(x).strip()}
            except Exception:
                processed_ids = set()
        # Legacy fallback: derive processed_ids from cached records (best effort).
        if not processed_ids and cached_all:
            processed_ids = {
                str(r.get("id") or "").strip()
                for r in cached_all
                if isinstance(r, dict) and str(r.get("id") or "").strip()
            }
        # If we loaded from legacy excel_output, mirror processed_ids into canonical output/... path.
        if processed_ids and processed_file.parent.name == "excel_output":
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                processed_ids_path.write_text(
                    json.dumps(sorted(processed_ids), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

    if persist_output and cache_file.exists() and processed_ids:
        want_ids = {str(x).strip() for x in (file_ids or []) if str(x).strip()}
        missing_ids = sorted([x for x in want_ids if x not in processed_ids])

        # If we already have cached records for all requested ids, return them directly.
        # This ensures that when users delete derived JSON elsewhere (or just want to reuse cache),
        # Cross Analysis still works without re-extraction.
        if not missing_ids:
            return [r for r in cached_all if str(r.get("id") or "").strip() in want_ids]

    # resolve catalog path
    if catalog_path:
        cat_path = Path(catalog_path)
    else:
        # allow env override; default ships with package under catalog/ESGMetrics.xlsx
        envp = os.getenv("ESG_METRICS_CATALOG_PATH", "")
        if envp.strip():
            cat_path = Path(envp)
        else:
            cat_path = Path(__file__).parent / "catalog" / "ESGMetrics.xlsx"

    specs = load_excel_catalog(cat_path)
    groups = group_specs(specs)

    # Warm the catalog embedding cache (process + disk) once per call.
    # This prevents per-group lazy initialization from competing with report threads.
    try:
        _get_spec_vectors(specs, catalog_path=cat_path)
    except Exception as e:
        logger.warning(f"[ExcelMetrics] Catalog spec vector cache warmup failed (ignored): {e}")

    # report labels
    reports = get_reports_info(file_ids)
    short_map = {r.file_id: (r.short_name or r.display_name or r.file_id) for r in reports}

    # Start from existing cache (if any) and only extract missing file_ids.
    all_records: List[dict] = [r for r in cached_all if isinstance(r, dict)]
    # If cache exists but is partial, only process the missing ones.
    file_ids_to_process = [
        fid
        for fid in (file_ids or [])
        if str(fid).strip() and str(fid).strip() not in processed_ids
    ]

    # Progress logging
    try:
        total_req = len({str(x).strip() for x in (file_ids or []) if str(x).strip()})
        to_proc = len(file_ids_to_process)
        logger.info(f"[ExcelMetrics] requested={total_req} to_process={to_proc} cached={max(total_req-to_proc,0)} catalog={str(cat_path)} topN={top_n_candidates}")
        if to_proc:
            logger.info(f"[ExcelMetrics] missing_ids={file_ids_to_process}")
    except Exception:
        pass

    def _dedup_records(items: List[dict]) -> List[dict]:
        uniq: List[dict] = []
        seen = set()
        for x in items or []:
            if not isinstance(x, dict):
                continue
            k = (
                str(x.get("id") or "").strip(),
                x.get("Primary Navigation"),
                x.get("Secondary Navigation"),
                x.get("Topic"),
                x.get("Sub-topic"),
                x.get("year"),
                x.get("data"),
                x.get("unit"),
                x.get("detail"),
                x.get("page"),
            )
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        return uniq

    def _write_report_outputs(fid: str, report_records: List[dict]):
        """Persist per-report outputs + update processed_ids.

        Runs in the main thread (even when extraction is parallel) to avoid
        shared-state races and keep behavior stable.
        """
        if not persist_output:
            return

        out_dir = CROSS_CACHE_DIR / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{fid}.json"

        # Merge with any existing per-report output (useful when jobs restart)
        existing: List[dict] = []
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []

        merged = _dedup_records(existing + (report_records or []))
        merged = _resolve_conflicts(merged)
        p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

        legacy_dir = CROSS_CACHE_DIR / "excel_output"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / f"{fid}.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

        processed_ids.add(str(fid).strip())
        try:
            processed_ids_path.write_text(
                json.dumps(sorted(processed_ids), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        try:
            (legacy_dir / "processed_ids.json").write_text(
                json.dumps(sorted(processed_ids), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _extract_one_report(fid: str, idx: int, n_total: int) -> List[dict]:
        rep_name = short_map.get(fid, fid)
        try:
            logger.info(f"[ExcelMetrics] ({idx}/{n_total}) Processing report id={fid} name={rep_name}")
        except Exception:
            pass

        report_records: List[dict] = []
        _g_total = len(groups) or 1
        for _g_i, ((primary, secondary), gspecs) in enumerate(groups.items(), start=1):
            try:
                logger.debug(f"[ExcelMetrics]   ({idx}/{n_total}) ({_g_i}/{_g_total}) {primary} / {secondary} specs={len(gspecs)}")
            except Exception:
                pass

            q_pack, q_vec, q_text = _build_group_query(gspecs)
            segs = _with_retrieval_lock(
                topn_segments,
                fid,
                query_pack=q_pack,
                query_text=q_text,
                top_n=top_n_candidates,
                query_pack_vec=q_vec,
            )
            if not segs:
                continue

            raw = _llm_extract_group(primary=primary, secondary=secondary, specs=gspecs, segments=segs)
            if not raw:
                raw = _rules_extract_group(primary=primary, secondary=secondary, specs=gspecs, segments=segs)

            matched = _match_items_to_specs(
                primary=primary,
                secondary=secondary,
                group_specs=gspecs,
                items=raw,
                all_specs=specs,
                catalog_path=cat_path,
            )
            records = _normalize_matched_items(file_id=fid, name=rep_name, matched=matched, evidence_segments=segs)
            report_records.extend(records)
            try:
                logger.debug(f"[ExcelMetrics]     ({idx}/{n_total}) group_records={len(records)} candidates={len(segs)}")
            except Exception:
                pass

            if _env_flag("EXCEL_METRICS_SPEC_BACKSTOP_ENABLED", "1"):
                have = {(r.get("Topic"), r.get("Sub-topic")) for r in records}
                missing_specs = [s for s in gspecs if (s.topic, s.display_subtopic()) not in have]
                if missing_specs:
                    for ms in missing_specs:
                        q2: List[str] = []
                        q2.extend(list(ms.query_pack_en or ()))
                        q2.append(ms.display_subtopic())
                        q2.extend(_definition_terms(ms.definition or ""))
                        if ms.unit_items:
                            q2.extend(list(ms.unit_items)[:4])

                        q2d: List[str] = []
                        seen2 = set()
                        for x in q2:
                            xx = str(x or "").strip()
                            if not xx:
                                continue
                            xl = xx.lower()
                            if xl in seen2:
                                continue
                            seen2.add(xl)
                            q2d.append(xx)
                            if len(q2d) >= 32:
                                break
                        q_vec2 = q2d[:24]
                        q_text2 = " ; ".join(q2d[:18])

                        seg2 = _with_retrieval_lock(
                            topn_segments,
                            fid,
                            query_pack=q2d,
                            query_text=q_text2,
                            top_n=min(top_n_candidates, int(os.getenv("EXCEL_METRICS_SPEC_TOPN", "220") or "220")),
                            query_pack_vec=q_vec2,
                        )
                        seg_use = seg2 or segs

                        raw2 = _llm_extract_spec(primary=primary, secondary=secondary, spec=ms, segments=seg_use)
                        if not raw2:
                            raw2 = _rules_extract_group(primary=primary, secondary=secondary, specs=[ms], segments=seg_use, max_items=8)

                        rec2 = _post_filter_and_normalize(
                            file_id=fid,
                            name=rep_name,
                            primary=primary,
                            secondary=secondary,
                            specs=[ms],
                            extracted=raw2,
                            evidence_segments=seg_use,
                        )
                        if rec2:
                            report_records.extend(rec2)

        report_records = _resolve_conflicts(_dedup_records(report_records))
        try:
            logger.info(f"[ExcelMetrics] ({idx}/{n_total}) Report done: id={fid} records={len(report_records)}")
        except Exception:
            pass
        return report_records

    # -----------------------------
    # Extraction run (report-level parallelism)
    # -----------------------------
    results_by_id: Dict[str, List[dict]] = {}
    n_total = len(file_ids_to_process) or 1

    if file_ids_to_process:
        try:
            logger.info(
                f"[ExcelMetrics] report_workers={min(_REPORT_WORKERS, len(file_ids_to_process))} "
                f"llm_concurrency={_LLM_CONCURRENCY} gpu_lock={_USE_GPU_LOCK}"
            )
        except Exception:
            pass

    if len(file_ids_to_process) <= 1 or _REPORT_WORKERS <= 1:
        for i, fid in enumerate(file_ids_to_process, start=1):
            recs = _extract_one_report(fid, i, n_total)
            results_by_id[fid] = recs
            _write_report_outputs(fid, recs)
    else:
        workers = min(_REPORT_WORKERS, len(file_ids_to_process))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            fut_map = {}
            for i, fid in enumerate(file_ids_to_process, start=1):
                fut = ex.submit(_extract_one_report, fid, i, n_total)
                fut_map[fut] = (fid, i)

            for fut in as_completed(list(fut_map.keys())):
                fid, i = fut_map[fut]
                try:
                    recs = fut.result()
                except Exception as e:
                    logger.error(f"[ExcelMetrics] report failed once; retry serial. id={fid} err={e}")
                    try:
                        recs = _extract_one_report(fid, i, n_total)
                    except Exception as e2:
                        logger.error(f"[ExcelMetrics] report failed twice; skip. id={fid} err={e2}")
                        recs = []
                results_by_id[fid] = recs
                _write_report_outputs(fid, recs)

    # Merge in a stable order (keeps behavior consistent for downstream consumers)
    for fid in file_ids_to_process:
        all_records.extend(results_by_id.get(fid, []))

    # persist global
    if persist_output:
        # Global output for frontend consumption: uploads/outputs/cross_analysis/output/all_records.json
        out_dir = CROSS_CACHE_DIR / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "all_records.json"

        # Deduplicate (avoid accumulating duplicates across runs)
        uniq: List[dict] = []
        seen = set()
        for x in all_records:
            if not isinstance(x, dict):
                continue
            k = (
                str(x.get("id") or "").strip(),
                x.get("Primary Navigation"),
                x.get("Secondary Navigation"),
                x.get("Topic"),
                x.get("Sub-topic"),
                x.get("year"),
                x.get("data"),
                x.get("unit"),
                x.get("detail"),
                x.get("page"),
            )
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)

        uniq = _resolve_conflicts(uniq)

        p.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")

        # Backward compatibility
        try:
            logger.info(f"[ExcelMetrics] Global cache written: records={len(uniq)} processed_reports={len(processed_ids)}")
        except Exception:
            pass

        legacy_dir = CROSS_CACHE_DIR / "excel_output"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "all_records.json").write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
        return uniq
    return all_records
