from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, List, Tuple

from .hipporag_settings import HippoRAGSettings
from .hf_cache import prefer_local_model

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cached_key: tuple[str, str, bool] | None = None
_cached_reranker = None


def _load_flag_reranker(model_name_or_path: str, device: str, use_fp16: bool):
    """Lazy-load FlagEmbedding reranker, preferring local HF cache."""
    try:
        from FlagEmbedding import FlagReranker  # type: ignore
    except Exception as e:
        raise RuntimeError("FlagEmbedding is not installed. Add FlagEmbedding to requirements.") from e

    # Prefer local snapshot path if model_name_or_path looks like a HF repo id.
    if "/" in model_name_or_path and not model_name_or_path.startswith("/"):
        ref = prefer_local_model(model_name_or_path, explicit_local_path=os.getenv("LOCAL_RERANKER_MODEL_PATH"))
        preferred = ref.local_path or model_name_or_path
    else:
        preferred = model_name_or_path

    return FlagReranker(preferred, use_fp16=use_fp16, devices=[device])


def get_reranker(settings: HippoRAGSettings):
    global _cached_key, _cached_reranker

    if not getattr(settings, "rerank_enabled", False):
        return None

    key = (settings.rerank_model_name_or_path, settings.rerank_device, bool(settings.rerank_use_fp16))

    if _cached_reranker is not None and _cached_key == key:
        return _cached_reranker

    with _lock:
        if _cached_reranker is not None and _cached_key == key:
            return _cached_reranker

        t0 = time.time()
        rr = _load_flag_reranker(settings.rerank_model_name_or_path, settings.rerank_device, settings.rerank_use_fp16)
        _cached_reranker = rr
        _cached_key = key
        logger.info(f"[Rerank] loaded model={key[0]} device={key[1]} fp16={key[2]} in {time.time()-t0:.2f}s")
        return rr


def rerank_segment_ids(
    query: str,
    scored: List[Tuple[str, float]],
    get_passage: Callable[[str], str],
    settings: HippoRAGSettings,
) -> List[Tuple[str, float]]:
    """Rerank topK segment IDs using FlagEmbedding cross-encoder.

    Input `scored` must already be sorted by fused_score desc.
    Output keeps tail order; only reorders topK.
    """
    if not getattr(settings, "rerank_enabled", False) or not scored:
        return scored

    try:
        rr = get_reranker(settings)
    except Exception as e:
        logger.warning(f"[Rerank] unavailable, skip. {e}")
        return scored

    if rr is None:
        return scored

    k = min(int(settings.rerank_top_k), len(scored))
    head = scored[:k]
    tail = scored[k:]

    max_chars = int(getattr(settings, "rerank_max_chars_per_passage", 2000))
    passages: List[str] = []
    for sid, _ in head:
        p = get_passage(sid) or ""
        if max_chars > 0 and len(p) > max_chars:
            p = p[:max_chars]
        passages.append(p)

    pairs = [[query, p] for p in passages]

    bs = max(1, int(settings.rerank_batch_size))
    t0 = time.time()

    scores: List[float] = []
    for i in range(0, len(pairs), bs):
        chunk = pairs[i : i + bs]
        try:
            s = rr.compute_score(chunk, normalize=False)
        except TypeError:
            s = rr.compute_score(chunk)
        scores.extend([float(x) for x in s])

    reranked = [(sid, float(rs)) for (sid, _), rs in zip(head, scores)]
    reranked.sort(key=lambda x: x[1], reverse=True)

    logger.info(f"[Rerank] topK={k} bs={bs} took {time.time()-t0:.2f}s")
    return reranked + tail
