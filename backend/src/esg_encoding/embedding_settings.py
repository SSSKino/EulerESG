"""Embedding and reranker model configuration helpers.

Keep model selection env-driven so Docker can switch models without code edits.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


DEFAULT_EMBEDDING_MODEL_NAME = "microsoft/harrier-oss-v1-0.6b"
DEFAULT_RERANK_MODEL_NAME = "microsoft/harrier-oss-v1-0.6b"

_EMBEDDING_MODEL_ENV_KEYS = (
    "LOCAL_EMBEDDINGS_MODEL_PATH",
    "EMBEDDING_MODEL",
    "LOCAL_EMBEDDINGS_MODEL_ID",
    "LOCAL_EMBEDDINGS_REPO_ID",
    "EMBEDDING_MODEL_NAME",
    "HIPPO_EMBEDDING_MODEL_NAME",
)

_RERANK_MODEL_ENV_KEYS = (
    "LOCAL_RERANKER_MODEL_PATH",
    "RERANK_MODEL",
    "RERANKER_MODEL",
    "LOCAL_RERANKER_MODEL_ID",
    "LOCAL_RERANKER_REPO_ID",
    "LOCAL_RERANKER_MODEL_NAME",
    "HIPPO_RERANKER_MODEL_NAME",
)

DEFAULT_EMBEDDING_QUERY_PROMPT = (
    "Instruct: Retrieve the most relevant evidence passages from the ESG report for the given SASB/ESG disclosure metric, "
    "including directly matching values, units, time periods, scope, methodology, and surrounding evidence.\n"
    "Query: "
)


_MODEL_DTYPE_ALIASES = {
    "float32": "float32",
    "fp32": "float32",
    "full": "float32",
    "float": "float32",
    "bfloat16": "bfloat16",
    "bf16": "bfloat16",
    "float16": "float16",
    "fp16": "float16",
    "half": "float16",
    "auto": "auto",
}


def normalize_model_dtype(value: str | None, default: str = "float32") -> str:
    """Normalize Docker/env precision strings without changing model behavior elsewhere."""
    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(default or "float32").strip().lower()
    return _MODEL_DTYPE_ALIASES.get(raw, _MODEL_DTYPE_ALIASES.get(str(default).strip().lower(), "float32"))


def get_configured_embedding_model_dtype(default: str = "float32") -> str:
    """Return embedding model runtime precision configured from Docker/env."""
    return normalize_model_dtype(os.getenv("LOCAL_EMBEDDINGS_MODEL_DTYPE"), default)


def get_configured_rerank_model_dtype(default: str = "float32") -> str:
    """Return reranker model runtime precision configured from Docker/env.

    LOCAL_RERANKER_USE_FP16 is kept only as a backward-compatible fallback.
    """
    configured = str(os.getenv("LOCAL_RERANKER_MODEL_DTYPE", "") or "").strip()
    if configured:
        return normalize_model_dtype(configured, default)
    legacy_fp16 = str(os.getenv("LOCAL_RERANKER_USE_FP16", "") or "").strip().lower()
    if legacy_fp16 in ("1", "true", "yes", "y", "on"):
        return "float16"
    return normalize_model_dtype(default, "float32")


def _first_env_value(keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
    return None


def _real_local_dir(value: str) -> Optional[str]:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.exists() and path.is_dir():
        return str(path)
    return None


def get_configured_embedding_model_name(default: str = DEFAULT_EMBEDDING_MODEL_NAME) -> str:
    """Return the embedding model id/path configured from Docker/env.

    LOCAL_EMBEDDINGS_MODEL_PATH is kept as the primary Docker switch for compatibility
    with the original compose style. It may be either a HuggingFace repo id or a real
    local snapshot directory.
    """
    return _first_env_value(_EMBEDDING_MODEL_ENV_KEYS) or default


def get_configured_embedding_local_path() -> Optional[str]:
    """Return LOCAL_EMBEDDINGS_MODEL_PATH only when it points to a real local directory."""
    return _real_local_dir(str(os.getenv("LOCAL_EMBEDDINGS_MODEL_PATH", "") or "").strip())


def get_configured_rerank_model_name(default: str = DEFAULT_RERANK_MODEL_NAME) -> str:
    """Return the reranker model id/path configured from Docker/env.

    LOCAL_RERANKER_MODEL_PATH is kept as the primary Docker switch for compatibility
    with the original compose style. It may be either a HuggingFace repo id or a real
    local snapshot directory.
    """
    return _first_env_value(_RERANK_MODEL_ENV_KEYS) or default


def get_configured_rerank_local_path() -> Optional[str]:
    """Return LOCAL_RERANKER_MODEL_PATH only when it points to a real local directory."""
    return _real_local_dir(str(os.getenv("LOCAL_RERANKER_MODEL_PATH", "") or "").strip())


def get_embedding_query_prompt() -> str:
    """Return the query-side instruction prompt used by instruction-tuned embedding models."""
    return str(os.getenv("LOCAL_EMBEDDINGS_QUERY_PROMPT", "") or DEFAULT_EMBEDDING_QUERY_PROMPT)


def should_instruction_prompt_queries(model_name_or_path: str | None = None) -> bool:
    """Whether query embeddings should use an instruction prompt.

    Harrier is trained to use an instruction on query-side embeddings. The env flag
    can be disabled only if a future embedding model should not receive prompts.
    """
    flag = str(os.getenv("LOCAL_EMBEDDINGS_QUERY_PROMPT_ENABLED", "1") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    model = str(model_name_or_path or get_configured_embedding_model_name() or "").lower()
    return "harrier-oss" in model or str(os.getenv("LOCAL_EMBEDDINGS_FORCE_QUERY_PROMPT", "") or "").strip().lower() in ("1", "true", "yes", "on")
