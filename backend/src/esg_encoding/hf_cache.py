"""
HuggingFace cache resolver.

Goal: Prefer loading models from a mounted HF cache volume (e.g. /root/.cache/huggingface)
without hitting the network. Only fall back to remote download when cache is missing.

Why this exists:
- HuggingFace cache layouts can differ across environments:
  - <HF_HOME>/hub/models--ORG--REPO/snapshots/<sha>/
  - <HF_HOME>/models--ORG--REPO/snapshots/<sha>/
  - <HF_HOME>/ORG--REPO/   (a manually-copied local model folder)
This resolver searches across common layouts and also performs light integrity checks
for sentence-transformers style models (modules.json + per-module config.json).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Iterable
import json
import os


DEFAULT_HF_HOME = os.getenv("HF_HOME", "/root/.cache/huggingface")


@dataclass(frozen=True)
class LocalModelRef:
    repo_id: str
    local_path: Optional[str]
    used_fallback: bool


def _split_repo(repo_id: str) -> Tuple[str, str]:
    if "/" in repo_id:
        org, name = repo_id.split("/", 1)
        return org, name
    return "", repo_id


def _candidate_base_dirs(repo_id: str, hf_home: str) -> Iterable[Path]:
    """
    Yield possible cache base directories for a repo id.

    Supports common layouts:
      - <hf_home>/hub/models--ORG--REPO
      - <hf_home>/models--ORG--REPO
      - <hf_home>/ORG--REPO        (user-provided / manually staged)
    """
    org, name = _split_repo(repo_id)
    if org:
        yield Path(hf_home) / "hub" / f"models--{org}--{name}"
        yield Path(hf_home) / f"models--{org}--{name}"
        yield Path(hf_home) / f"{org}--{name}"
    else:
        yield Path(hf_home) / "hub" / f"models--{name}"
        yield Path(hf_home) / f"models--{name}"
        yield Path(hf_home) / f"{name}"


def _looks_like_sentence_transformers_model(p: Path) -> bool:
    """
    Light integrity check for a sentence-transformers model directory.

    We check:
      - If modules.json exists, each module folder exists, and Pooling/Transformer modules
        have config.json (this avoids the common '1_Pooling/config.json missing' crash).
      - Else, accept if one of the common root configs exists.
    """
    if not p.exists() or not p.is_dir():
        return False

    modules = p / "modules.json"
    if modules.exists():
        try:
            data = json.loads(modules.read_text(encoding="utf-8"))
        except Exception:
            return False

        if not isinstance(data, list) or not data:
            return False

        for m in data:
            if not isinstance(m, dict):
                return False
            rel = m.get("path") or m.get("name")  # path is the canonical field
            if not rel or not isinstance(rel, str):
                continue

            mp = p / rel
            if not mp.exists() or not mp.is_dir():
                return False

            mtype = str(m.get("type") or "")
            # For the modules that sentence-transformers expects to have a config.json
            # (Pooling and Transformer are the typical ones)
            if ("Pooling" in mtype) or ("Transformer" in mtype) or ("Pooling" in rel) or ("Transformer" in rel):
                if not (mp / "config.json").exists():
                    return False

        return True

    # fallback: some models may not be sentence-transformers packaged
    if (p / "config.json").exists():
        return True
    if (p / "sentence_bert_config.json").exists():
        return True

    return False


def find_best_snapshot_path(repo_id: str, hf_home: str = DEFAULT_HF_HOME) -> Optional[str]:
    """
    Return the newest valid snapshot directory if it exists and seems complete enough.
    """
    candidates: list[Tuple[float, Path]] = []

    for base in _candidate_base_dirs(repo_id, hf_home):
        snaps = base / "snapshots"
        if not snaps.exists() or not snaps.is_dir():
            continue

        for snap in snaps.iterdir():
            if not snap.is_dir():
                continue
            if _looks_like_sentence_transformers_model(snap):
                candidates.append((snap.stat().st_mtime, snap))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return str(candidates[0][1])


def find_best_local_dir(repo_id: str, hf_home: str = DEFAULT_HF_HOME) -> Optional[str]:
    """
    Return a local model directory if one exists outside of 'snapshots/' layout.
    Useful for manually staged folders like <hf_home>/BAAI--bge-m3.
    """
    candidates: list[Tuple[float, Path]] = []
    for base in _candidate_base_dirs(repo_id, hf_home):
        if _looks_like_sentence_transformers_model(base):
            candidates.append((base.stat().st_mtime, base))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return str(candidates[0][1])


def prefer_local_model(repo_id: str, explicit_local_path: Optional[str] = None, hf_home: str = DEFAULT_HF_HOME) -> LocalModelRef:
    """
    Prefer explicit local path (env-provided), then a directly-staged local dir,
    then a cache snapshot. Otherwise return fallback (None) so callers can use repo_id.
    """
    if explicit_local_path:
        p = Path(explicit_local_path)
        if _looks_like_sentence_transformers_model(p):
            return LocalModelRef(repo_id=repo_id, local_path=str(p), used_fallback=False)

    staged = find_best_local_dir(repo_id, hf_home=hf_home)
    if staged:
        return LocalModelRef(repo_id=repo_id, local_path=staged, used_fallback=False)

    snap = find_best_snapshot_path(repo_id, hf_home=hf_home)
    if snap:
        return LocalModelRef(repo_id=repo_id, local_path=snap, used_fallback=False)

    return LocalModelRef(repo_id=repo_id, local_path=None, used_fallback=True)
