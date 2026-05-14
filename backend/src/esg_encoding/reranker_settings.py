"""Reranker model configuration helpers.

Keep reranker model selection env-driven so Docker can switch models without code edits.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


DEFAULT_RERANKER_MODEL_NAME = "microsoft/harrier-oss-v1-0.6b"

_RERANKER_MODEL_ENV_KEYS = (
    "LOCAL_RERANKER_MODEL_PATH",
    "LOCAL_RERANKER_MODEL_NAME",
    "LOCAL_RERANKER_REPO_ID",
    "RERANKER_MODEL_NAME",
    "RERANKER_MODEL",
    "HIPPO_RERANKER_MODEL_NAME",
)


def get_configured_reranker_model_name(default: str = DEFAULT_RERANKER_MODEL_NAME) -> str:
    """Return the reranker model id/path configured from Docker/env.

    LOCAL_RERANKER_MODEL_PATH intentionally stays first for compatibility with
    the existing docker-compose setting, where it may be either a local directory
    or a Hugging Face model id.
    """
    for key in _RERANKER_MODEL_ENV_KEYS:
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
    return default


def get_configured_reranker_local_path() -> Optional[str]:
    """Return LOCAL_RERANKER_MODEL_PATH only when it points to a real local directory."""
    value = str(os.getenv("LOCAL_RERANKER_MODEL_PATH", "") or "").strip()
    if not value:
        return None

    path = Path(value).expanduser()
    if path.exists() and path.is_dir():
        return str(path)
    return None
