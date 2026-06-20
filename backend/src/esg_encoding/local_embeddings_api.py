"""OpenAI-compatible embeddings endpoint backed by a local HF model path.

Why this exists:
- HippoRAG 2.0.0a4 may reject arbitrary `embedding_model_name` values (allowlist),
  so we route embeddings through `embedding_base_url` instead.
- You can point LOCAL_EMBEDDINGS_MODEL_PATH to a local snapshot, e.g.
  /root/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/<REVISION>

Env:
- EMBEDDING_MODEL / LOCAL_EMBEDDINGS_MODEL_ID: HF model id
- LOCAL_EMBEDDINGS_MODEL_PATH: optional local snapshot directory only
- LOCAL_EMBEDDINGS_DEVICE: 'cuda' | 'cpu' (default: auto)
"""

from __future__ import annotations

import asyncio
import os
from typing import List, Optional, Union, Any

from fastapi import APIRouter
from pydantic import BaseModel

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover
    torch = None

from sentence_transformers import SentenceTransformer  # type: ignore

from .hf_cache import get_hf_home
from .shared_embedding_model import get_shared_embedding_model
from .embedding_settings import get_configured_embedding_local_path, get_configured_embedding_model_name


router = APIRouter()

_model: Optional[SentenceTransformer] = None
_model_lock = asyncio.Lock()


def _default_device() -> str:
    if os.getenv("LOCAL_EMBEDDINGS_DEVICE"):
        return os.getenv("LOCAL_EMBEDDINGS_DEVICE")  # type: ignore
    if torch is not None and torch.cuda.is_available():
        return "cuda"
    return "cpu"


async def get_local_embedder() -> SentenceTransformer:
    global _model
    if _model is not None:
        return _model

    async with _model_lock:
        if _model is not None:
            return _model

        model_path = get_configured_embedding_model_name()
        device = _default_device()

        _model = get_shared_embedding_model(
            model_path,
            device=device,
            hf_home=get_hf_home(),
            explicit_local_path=get_configured_embedding_local_path(),
            trust_remote_code=True,
        )
        return _model


class EmbeddingsRequest(BaseModel):
    input: Union[str, List[str]]
    model: Optional[str] = None
    encoding_format: Optional[str] = None
    user: Optional[str] = None


@router.post("/v1/embeddings")
async def embeddings(req: EmbeddingsRequest) -> Any:
    embedder = await get_local_embedder()
    texts: List[str] = req.input if isinstance(req.input, list) else [req.input]

    # SentenceTransformer.encode is blocking → move to a thread.
    vecs = await asyncio.to_thread(
        embedder.encode,
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )

    vecs_list = vecs.tolist()  # type: ignore[attr-defined]
    data = []
    for i, v in enumerate(vecs_list):
        data.append(
            {
                "object": "embedding",
                "index": i,
                "embedding": [float(x) for x in v],
            }
        )

    return {
        "object": "list",
        "data": data,
        "model": req.model or get_configured_embedding_model_name(),
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
