"""Metric-centric retrieval profile utilities.

Runtime retrieval consumes generated SASB MetricRetrievalProfile files from
``backend/data/sasb_metric_profiles`` when available. The original SASB JSON
files under ``backend/data/sasb_metrics`` remain untouched and are not modified
by this module.

A profile is a retrieval-side view of one canonical metric. It feeds exact code
search, alias search, BM25, dense retrieval, RRF fusion, exact-metric reranking,
and lightweight document-to-metric mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..models import ESGMetric, SemanticExpansion

_GENERIC_TERMS = {
    "the", "and", "or", "of", "to", "in", "for", "with", "by", "from", "as", "on", "at",
    "metric", "metrics", "number", "amount", "total", "percentage", "description", "discussion",
    "approach", "including", "related", "associated", "data", "information", "report", "reported",
    "sustainability", "disclosure", "topic", "topics", "type", "category", "unit", "value",
}


@dataclass(frozen=True)
class MetricRetrievalProfile:
    """Canonical retrieval profile used by every retrieval channel."""

    metric_id: str
    metric_name: str
    metric_code: str
    canonical_label: str
    source: str = ""
    industry: str = ""
    topic: str = ""
    unit: str = ""
    definition: str = ""
    disclosure_type: str = "unknown"
    exact_code_patterns: List[re.Pattern] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    bm25_terms: List[str] = field(default_factory=list)
    dense_query: str = ""
    dense_queries: List[str] = field(default_factory=list)
    anchor_terms: List[str] = field(default_factory=list)
    negative_anchor_terms: List[str] = field(default_factory=list)
    preferred_chunk_types: List[str] = field(default_factory=list)
    evidence_hints: Dict[str, Any] = field(default_factory=dict)
    extraction_hints: Dict[str, Any] = field(default_factory=dict)
    similar_metric_warnings: List[Dict[str, Any]] = field(default_factory=list)
    rerank_instruction: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def normalize_metric_text(value: object) -> str:
    """Lowercase, punctuation-tolerant normalizer for matching metric phrases."""
    text = str(value or "").strip().lower()
    text = text.replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact_metric_text(value: object) -> str:
    return re.sub(r"[^a-z0-9%]+", "", normalize_metric_text(value))


def tokenize_metric_text(value: object) -> List[str]:
    text = normalize_metric_text(value)
    tokens = re.findall(r"[a-z][a-z0-9-]{2,}|\d+(?:\.\d+)?%?", text)
    return [t for t in tokens if t not in _GENERIC_TERMS and not t.isdigit()]


def _dedupe_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value or "").strip())
        if not cleaned:
            continue
        key = normalize_metric_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


def _code_regex(metric_code: str, extra_patterns: Optional[Sequence[str]] = None) -> List[re.Pattern]:
    code = str(metric_code or "").strip()
    patterns: List[str] = []
    if code:
        escaped = re.escape(code)
        flexible = re.escape(code).replace(r"\-", r"\s*[-–—]\s*").replace(r"\.", r"\s*\.\s*")
        patterns.append(escaped)
        if flexible != escaped:
            patterns.append(flexible)
        compact = re.sub(r"[^A-Za-z0-9]", "", code)
        if compact and compact.lower() != code.lower():
            patterns.append(re.escape(compact))
    for pattern in extra_patterns or []:
        pattern = str(pattern or "").strip()
        if pattern:
            patterns.append(pattern)

    compiled = []
    seen = set()
    for pattern in patterns:
        if pattern in seen:
            continue
        seen.add(pattern)
        try:
            compiled.append(re.compile(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", re.IGNORECASE))
        except re.error:
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue
    return compiled


def _metric_name_variants(metric_name: str) -> List[str]:
    name = re.sub(r"\s+", " ", str(metric_name or "").strip())
    if not name:
        return []
    variants = [name]
    cleaned = re.sub(r"\(\s*\d+\s*\)", "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:")
    if cleaned and cleaned != name:
        variants.append(cleaned)
    parts = re.split(r"\(\s*\d+\s*\)|;|,\s*(?=\(?\d|and\s+\(?\d)", name)
    for part in parts:
        part = re.sub(r"\band\b", " ", part, flags=re.IGNORECASE)
        part = re.sub(r"\s+", " ", part).strip(" ,;:-()")
        if len(part) >= 5:
            variants.append(part)
    no_parens = re.sub(r"\([^)]*\)", " ", name)
    no_parens = re.sub(r"\s+", " ", no_parens).strip(" ,;:")
    if no_parens and len(no_parens) >= 5:
        variants.append(no_parens)
    return _dedupe_keep_order(variants)


def _definition_phrases(definition: str, limit: int = 8) -> List[str]:
    text = str(definition or "").strip()
    if not text:
        return []
    text = re.sub(r"^[A-Z]{2,3}-[A-Z]{2,3}-\d+[a-z]?\.\d+\.?\s*", "", text)
    chunks = re.split(r"[\n;•]+|\.\s+(?=The entity|It shall|The scope|For the purposes|If relevant)", text)
    phrases: List[str] = []
    for chunk in chunks:
        chunk = re.sub(r"\s+", " ", chunk).strip(" .,:;-")
        if not chunk:
            continue
        words = chunk.split()
        if len(words) > 18:
            chunk = " ".join(words[:18])
        if len(chunk) >= 10:
            phrases.append(chunk)
        if len(phrases) >= limit:
            break
    return _dedupe_keep_order(phrases)


def _unit_aliases(unit: str) -> List[str]:
    normalized = normalize_metric_text(unit)
    if not normalized or normalized in {"n/a", "na", "none"}:
        return []
    aliases = {normalized, normalized.replace(" ", "")}
    if "percent" in normalized or normalized == "%":
        aliases.update({"%", "percent", "percentage"})
    if "gigajoule" in normalized or normalized == "gj":
        aliases.update({"gj", "gigajoules", "gigajoule"})
    if "metric tons co2e" in normalized or "co2e" in normalized:
        aliases.update({"co2e", "co₂e", "tco2e", "mtco2e", "metric tons co2e", "metric tonnes co2e"})
    if "mwh" in normalized or "megawatt" in normalized:
        aliases.update({"mwh", "megawatt hours", "megawatt-hours"})
    if "cubic" in normalized or normalized in {"m3", "m³"}:
        aliases.update({"m3", "m³", "cubic meters", "cubic metres"})
    return _dedupe_keep_order(aliases)


def _package_backend_root() -> Path:
    # .../backend/src/esg_encoding/retrieval/metric_profile.py -> backend
    return Path(__file__).resolve().parents[3]


def default_metric_profile_dir() -> Path:
    env_value = os.getenv("SASB_METRIC_PROFILE_DIR") or os.getenv("METRIC_PROFILE_DIR")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return _package_backend_root() / "data" / "sasb_metric_profiles"


def _profile_from_dict(data: Mapping[str, Any]) -> MetricRetrievalProfile:
    metric_code = str(data.get("metric_code") or data.get("code") or "").strip()
    metric_name = str(data.get("metric_name") or data.get("metric") or data.get("canonical_label") or "").strip()
    metric_id = str(data.get("metric_id") or metric_code or metric_name).strip()
    canonical_label = str(data.get("canonical_label") or metric_name or metric_code or metric_id).strip()
    unit = str(data.get("unit") or "").strip()
    topic = str(data.get("topic") or data.get("sasb_topic") or "").strip()
    definition = str(data.get("definition") or data.get("description") or "").strip()
    aliases = _dedupe_keep_order(
        _as_list(data.get("aliases"))
        + _metric_name_variants(metric_name)
        + _as_list(data.get("metric"))
        + ([metric_code] if metric_code else [])
        + ([topic] if topic else [])
        + _unit_aliases(unit)
    )
    bm25_terms = _dedupe_keep_order(_as_list(data.get("bm25_terms")) + aliases + _as_list(data.get("anchor_terms")))
    anchor_terms = _dedupe_keep_order(_as_list(data.get("anchor_terms")) + tokenize_metric_text(" ".join([metric_name, metric_code, topic])))[:48]
    dense_query = str(data.get("dense_query") or "").strip()
    dense_queries = _dedupe_keep_order(_as_list(data.get("dense_queries")))
    if not dense_query:
        dense_query = "\n".join(
            part
            for part in [
                f"Canonical metric code: {metric_code}" if metric_code else "",
                f"Canonical metric name: {metric_name}" if metric_name else "",
                f"Disclosure topic: {topic}" if topic else "",
                f"Expected unit: {unit}" if unit else "",
                f"Metric definition: {definition}" if definition else "",
                "Aliases and report expressions: " + "; ".join(aliases[:24]) if aliases else "",
            ]
            if part
        )
    return MetricRetrievalProfile(
        metric_id=metric_id,
        metric_name=metric_name,
        metric_code=metric_code,
        canonical_label=canonical_label,
        source=str(data.get("source") or "SASB"),
        industry=str(data.get("industry") or ""),
        topic=topic,
        unit=unit,
        definition=definition,
        disclosure_type=str(data.get("disclosure_type") or data.get("category") or "unknown").strip().lower(),
        exact_code_patterns=_code_regex(metric_code, _as_list(data.get("exact_code_patterns"))),
        aliases=aliases,
        bm25_terms=bm25_terms,
        dense_query=dense_query,
        dense_queries=dense_queries,
        anchor_terms=anchor_terms,
        negative_anchor_terms=_dedupe_keep_order(_as_list(data.get("negative_anchor_terms"))),
        preferred_chunk_types=[x.lower() for x in _dedupe_keep_order(_as_list(data.get("preferred_chunk_types")))],
        evidence_hints=dict(data.get("evidence_hints") or {}),
        extraction_hints=dict(data.get("extraction_hints") or {}),
        similar_metric_warnings=list(data.get("similar_metric_warnings") or []),
        rerank_instruction=str(data.get("rerank_instruction") or "").strip(),
        raw=dict(data),
    )


@lru_cache(maxsize=4)
def load_all_metric_profiles(profile_dir: Optional[str] = None) -> List[MetricRetrievalProfile]:
    """Load generated ``*.profiles.json`` files from the SASB profile directory."""
    root = Path(profile_dir).expanduser().resolve() if profile_dir else default_metric_profile_dir()
    if not root.exists():
        return []
    profiles: List[MetricRetrievalProfile] = []
    for file_path in sorted(root.glob("*.profiles.json")):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in data.get("profiles", []) or []:
            if isinstance(item, Mapping):
                try:
                    profiles.append(_profile_from_dict(item))
                except Exception:
                    continue
    return profiles


@lru_cache(maxsize=4)
def build_profile_index(profile_dir: Optional[str] = None) -> Dict[str, Any]:
    profiles = load_all_metric_profiles(profile_dir)
    by_code: Dict[str, MetricRetrievalProfile] = {}
    by_id: Dict[str, MetricRetrievalProfile] = {}
    by_alias: Dict[str, List[MetricRetrievalProfile]] = {}
    for profile in profiles:
        for key in [profile.metric_code, compact_metric_text(profile.metric_code)]:
            if key:
                by_code[key.lower()] = profile
        for key in [profile.metric_id, compact_metric_text(profile.metric_id)]:
            if key:
                by_id[key.lower()] = profile
        for alias in profile.aliases:
            norm = normalize_metric_text(alias)
            if norm:
                by_alias.setdefault(norm, []).append(profile)
    return {"profiles": profiles, "by_code": by_code, "by_id": by_id, "by_alias": by_alias}


def find_metric_profile(metric: Any, profile_dir: Optional[str] = None) -> Optional[MetricRetrievalProfile]:
    """Find a generated profile matching an existing metric object or metric code string."""
    index = build_profile_index(profile_dir)
    if isinstance(metric, str):
        keys = [metric, compact_metric_text(metric)]
    else:
        keys = [
            getattr(metric, "metric_code", ""),
            getattr(metric, "metric_id", ""),
            compact_metric_text(getattr(metric, "metric_code", "")),
            compact_metric_text(getattr(metric, "metric_id", "")),
        ]
    for key in keys:
        key_norm = str(key or "").strip().lower()
        if not key_norm:
            continue
        if key_norm in index["by_code"]:
            return index["by_code"][key_norm]
        if key_norm in index["by_id"]:
            return index["by_id"][key_norm]
    if not isinstance(metric, str):
        name = normalize_metric_text(getattr(metric, "metric_name", ""))
        for profile in index["profiles"]:
            if name and name in {normalize_metric_text(profile.metric_name), normalize_metric_text(profile.canonical_label)}:
                return profile
    return None


def build_metric_retrieval_profile(metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None) -> MetricRetrievalProfile:
    """Return generated profile when available, otherwise build a safe fallback from ESGMetric."""
    generated = find_metric_profile(metric)
    if generated is not None:
        return generated

    metric_id = str(getattr(metric, "metric_id", "") or "")
    metric_name = str(getattr(metric, "metric_name", "") or "").strip()
    metric_code = str(getattr(metric, "metric_code", "") or "").strip()
    topic = str(getattr(metric, "sasb_topic", "") or "").strip()
    unit = str(getattr(metric, "unit", "") or "").strip()
    definition = str(getattr(metric, "definition", "") or getattr(metric, "description", "") or "").strip()
    source = str(getattr(getattr(metric, "source", ""), "value", getattr(metric, "source", "")) or "")
    aliases: List[str] = []
    aliases.extend(_metric_name_variants(metric_name))
    if metric_code:
        aliases.append(metric_code)
    if topic:
        aliases.append(topic)
    aliases.extend(str(k) for k in (getattr(metric, "keywords", None) or []) if str(k).strip())
    aliases.extend(_definition_phrases(definition, limit=5))
    aliases.extend(_unit_aliases(unit))
    if semantic_expansion is not None:
        if getattr(semantic_expansion, "semantic_description", None):
            aliases.extend(_definition_phrases(str(semantic_expansion.semantic_description), limit=3))
        aliases.extend(str(k) for k in (getattr(semantic_expansion, "expanded_keywords", None) or []) if str(k).strip())
    aliases = _dedupe_keep_order(aliases)
    anchor_seed = " ".join([metric_name, metric_code, topic, definition, " ".join(aliases)])
    anchor_terms = _dedupe_keep_order(tokenize_metric_text(anchor_seed))[:36]
    bm25_terms = _dedupe_keep_order(
        aliases + anchor_terms + [metric_name, metric_code, topic] + list(str(k) for k in (getattr(metric, "keywords", None) or []) if str(k).strip())
    )
    dense_parts = []
    if metric_code:
        dense_parts.append(f"Canonical metric code: {metric_code}")
    if metric_name:
        dense_parts.append(f"Canonical metric name: {metric_name}")
    if topic:
        dense_parts.append(f"Disclosure topic: {topic}")
    if unit:
        dense_parts.append(f"Expected unit: {unit}")
    if definition:
        dense_parts.append(f"Metric definition: {definition}")
    if semantic_expansion is not None and getattr(semantic_expansion, "semantic_description", None):
        dense_parts.append(f"Semantic expansion: {semantic_expansion.semantic_description}")
    if aliases:
        dense_parts.append("Aliases and report expressions: " + "; ".join(aliases[:24]))
    return MetricRetrievalProfile(
        metric_id=metric_id,
        metric_name=metric_name,
        metric_code=metric_code,
        canonical_label=metric_name or metric_code or metric_id,
        source=source,
        topic=topic,
        unit=unit,
        definition=definition,
        exact_code_patterns=_code_regex(metric_code),
        aliases=aliases,
        bm25_terms=bm25_terms,
        dense_query="\n".join(dense_parts).strip(),
        anchor_terms=anchor_terms,
        preferred_chunk_types=["table-row", "table", "paragraph", "index", "footnote"],
    )


def profile_to_metric_like(profile: MetricRetrievalProfile):
    """Create a minimal metric-like object for callers passing only a metric code."""
    class _ProfileMetric:
        metric_id = profile.metric_id or profile.metric_code or "profile_metric"
        metric_name = profile.metric_name or profile.canonical_label or profile.metric_code
        metric_code = profile.metric_code
        definition = profile.definition
        description = profile.definition
        keywords = profile.bm25_terms[:32]
        semantic_expansion = None
        unit = profile.unit
        sasb_category = profile.disclosure_type
        sasb_type = profile.disclosure_type
        sasb_topic = profile.topic
        source = profile.source or "SASB"

    return _ProfileMetric()


def content_contains_alias(content: str, alias: str) -> bool:
    """Phrase matcher used by exact-alias search and exact-metric rerank."""
    alias_norm = normalize_metric_text(alias)
    if not alias_norm or len(alias_norm) < 3:
        return False
    content_norm = normalize_metric_text(content)
    if alias_norm in content_norm:
        return True
    alias_compact = compact_metric_text(alias_norm)
    if len(alias_compact) >= 6 and alias_compact in compact_metric_text(content_norm):
        return True
    return False


def best_alias_matches(content: str, aliases: Sequence[str], limit: int = 12) -> List[str]:
    matches = []
    for alias in aliases:
        if content_contains_alias(content, alias):
            matches.append(str(alias))
        if len(matches) >= limit:
            break
    return _dedupe_keep_order(matches)


__all__ = [
    "MetricRetrievalProfile",
    "build_metric_retrieval_profile",
    "build_profile_index",
    "load_all_metric_profiles",
    "find_metric_profile",
    "profile_to_metric_like",
    "best_alias_matches",
    "content_contains_alias",
    "normalize_metric_text",
    "compact_metric_text",
    "tokenize_metric_text",
]
