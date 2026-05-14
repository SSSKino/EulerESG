from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover
    torch = None

from loguru import logger
from sentence_transformers import SentenceTransformer

from .hf_cache import prefer_local_model
from .embedding_settings import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    get_configured_embedding_local_path,
    get_configured_embedding_model_name,
    get_configured_embedding_model_dtype,
    get_configured_rerank_model_dtype,
    get_embedding_query_prompt,
    should_instruction_prompt_queries,
)

_lock = threading.Lock()
_cached_models: Dict[Tuple[str, str, str, str, bool], SentenceTransformer] = {}


def encode_query_texts(model: SentenceTransformer, texts, *, model_name_or_path: str | None = None, **kwargs):
    """Encode retrieval queries with the configured query instruction when needed.

    Document/passages should keep using model.encode(...) directly. This helper is
    only for query-side vectors so instruction-tuned embedding models keep their
    intended retrieval behavior without changing document embeddings.
    """
    if should_instruction_prompt_queries(model_name_or_path):
        prompt = get_embedding_query_prompt()
        try:
            return model.encode(texts, prompt=prompt, **kwargs)
        except TypeError:
            # Older sentence-transformers may not expose `prompt`; fall back to
            # the model-card prompt name when available, then to plain encode.
            try:
                return model.encode(texts, prompt_name=os.getenv("LOCAL_EMBEDDINGS_QUERY_PROMPT_NAME", "web_search_query"), **kwargs)
            except TypeError:
                return model.encode(texts, **kwargs)
    return model.encode(texts, **kwargs)


def get_default_embedding_device(preferred_device: str | None = None) -> str:
    if preferred_device:
        return str(preferred_device)
    env_device = os.getenv("LOCAL_EMBEDDINGS_DEVICE", "").strip()
    if env_device:
        return env_device
    if torch is not None and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _cleanup_corrupt_snapshot(err: Exception, hf_home: str) -> bool:
    import re
    import shutil

    msg = str(err)
    m = re.search(r"No such file or directory: '([^']+)'", msg)
    if not m:
        return False

    missing_path = Path(m.group(1))
    p = str(missing_path)
    if "/snapshots/" not in p:
        return False

    prefix, rest = p.split("/snapshots/", 1)
    sha = rest.split("/", 1)[0]
    snap_dir = Path(prefix) / "snapshots" / sha

    try:
        hf_root = Path(hf_home).resolve()
        snap_dir_resolved = snap_dir.resolve()
    except Exception:
        return False

    if not str(snap_dir_resolved).startswith(str(hf_root)):
        return False

    try:
        shutil.rmtree(snap_dir_resolved, ignore_errors=True)
        return True
    except Exception:
        return False




def _configured_dtype(dtype_env_key: str = "LOCAL_EMBEDDINGS_MODEL_DTYPE") -> str:
    """Return normalized runtime precision for the requested model path."""
    if dtype_env_key == "LOCAL_RERANKER_MODEL_DTYPE":
        return get_configured_rerank_model_dtype()
    return get_configured_embedding_model_dtype()


def _embedding_model_kwargs(dtype_env_key: str = "LOCAL_EMBEDDINGS_MODEL_DTYPE") -> dict:
    """Return model loading kwargs, with precision configurable from Docker/env."""
    dtype = _configured_dtype(dtype_env_key)
    if not dtype:
        return {}
    if dtype == "auto":
        return {"torch_dtype": "auto"}
    if torch is None:
        return {}
    dtype_map = {
        "float32": torch.float32,
        "bfloat16": getattr(torch, "bfloat16", torch.float32),
        "float16": torch.float16,
    }
    value = dtype_map.get(dtype)
    if value is None:
        return {}
    return {"torch_dtype": value}


def _build_sentence_transformer(
    model_name_or_path: str,
    *,
    device: str,
    cache_folder: str,
    trust_remote_code: bool,
    dtype_env_key: str = "LOCAL_EMBEDDINGS_MODEL_DTYPE",
) -> SentenceTransformer:
    """Load a SentenceTransformer with high-quality auto dtype when supported."""
    try:
        return SentenceTransformer(
            model_name_or_path,
            device=device,
            cache_folder=cache_folder,
            trust_remote_code=trust_remote_code,
            model_kwargs=_embedding_model_kwargs(dtype_env_key),
        )
    except TypeError:
        return SentenceTransformer(
            model_name_or_path,
            device=device,
            cache_folder=cache_folder,
            trust_remote_code=trust_remote_code,
        )

def resolve_embedding_model_path(
    model_name_or_path: str | None = None,
    *,
    explicit_local_path: str | None = None,
    hf_home: str | None = None,
) -> tuple[str, str, str]:
    repo_id = str(model_name_or_path or get_configured_embedding_model_name(DEFAULT_EMBEDDING_MODEL_NAME)).strip()
    if not repo_id:
        repo_id = DEFAULT_EMBEDDING_MODEL_NAME
    hf_home = hf_home or os.getenv("HF_HOME", "/root/.cache/huggingface")
    explicit_local_path = explicit_local_path or get_configured_embedding_local_path()
    ref = prefer_local_model(repo_id, explicit_local_path=explicit_local_path, hf_home=hf_home)
    resolved_model = ref.local_path or repo_id
    return repo_id, resolved_model, hf_home


def _embedding_cache_key(
    resolved_model: str,
    device: str,
    hf_home: str,
    dtype_env_key: str,
    trust_remote_code: bool,
) -> Tuple[str, str, str, str, bool]:
    """Build a stable model-instance key independent of the env var name.

    Embedding and dense-rerank can point at the same SentenceTransformer model.
    The cache key therefore uses the effective dtype value, not whether it came
    from LOCAL_EMBEDDINGS_MODEL_DTYPE or LOCAL_RERANKER_MODEL_DTYPE, so identical
    model/device/precision requests reuse the same loaded instance.
    """
    return (
        str(resolved_model),
        str(device),
        str(hf_home),
        _configured_dtype(dtype_env_key),
        bool(trust_remote_code),
    )


def get_shared_embedding_model(
    model_name_or_path: str | None = None,
    *,
    device: str | None = None,
    hf_home: str | None = None,
    explicit_local_path: str | None = None,
    trust_remote_code: bool = True,
    dtype_env_key: str = "LOCAL_EMBEDDINGS_MODEL_DTYPE",
) -> SentenceTransformer:
    device = get_default_embedding_device(device)
    repo_id, resolved_model, hf_home = resolve_embedding_model_path(
        model_name_or_path,
        explicit_local_path=explicit_local_path,
        hf_home=hf_home,
    )
    key = _embedding_cache_key(resolved_model, device, hf_home, dtype_env_key, trust_remote_code)

    cached = _cached_models.get(key)
    if cached is not None:
        return cached

    with _lock:
        cached = _cached_models.get(key)
        if cached is not None:
            return cached

        allow_remote = os.getenv("HF_ALLOW_ONLINE", "1") != "0"
        using_local = resolved_model != repo_id
        if using_local:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        else:
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)

        try:
            model = _build_sentence_transformer(
                resolved_model,
                device=device,
                cache_folder=hf_home,
                trust_remote_code=trust_remote_code,
                dtype_env_key=dtype_env_key,
            )
        except FileNotFoundError as e:
            cleaned = _cleanup_corrupt_snapshot(e, hf_home=hf_home)
            if cleaned:
                _, resolved_model2, _ = resolve_embedding_model_path(
                    repo_id,
                    explicit_local_path=explicit_local_path,
                    hf_home=hf_home,
                )
                using_local2 = resolved_model2 != repo_id
                if using_local2:
                    os.environ.setdefault("HF_HUB_OFFLINE", "1")
                    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
                    model = _build_sentence_transformer(
                        resolved_model2,
                        device=device,
                        cache_folder=hf_home,
                        trust_remote_code=trust_remote_code,
                        dtype_env_key=dtype_env_key,
                    )
                    resolved_model = resolved_model2
                elif allow_remote:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                    os.environ.pop("TRANSFORMERS_OFFLINE", None)
                    model = _build_sentence_transformer(
                        repo_id,
                        device=device,
                        cache_folder=hf_home,
                        trust_remote_code=trust_remote_code,
                        dtype_env_key=dtype_env_key,
                    )
                    resolved_model = repo_id
                else:
                    raise FileNotFoundError(
                        f"本地缓存缺失/损坏且已禁用远端下载（HF_ALLOW_ONLINE=0）。请检查 {hf_home} 下是否包含 {repo_id} 的完整模型。"
                    )
            else:
                raise

        key = _embedding_cache_key(resolved_model, device, hf_home, dtype_env_key, trust_remote_code)
        _cached_models[key] = model
        logger.info(f"[SharedEmbedding] loaded model={repo_id} resolved={resolved_model} device={device} dtype={key[3]}")
        return model
