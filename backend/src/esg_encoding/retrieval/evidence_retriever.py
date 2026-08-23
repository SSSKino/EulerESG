"""Unified metric-centric evidence retrieval interface.

All ESG tasks should call retrieve_evidence() rather than implementing their own
keyword/semantic/rerank pipeline. The implementation is metric-centric and
supports both standard metric -> document evidence retrieval and lightweight
report phrase -> canonical metric mapping.
"""

from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from ..content_extractor import enrich_document_with_pdf_links
from ..content_revision import document_content_revision
from ..models import ProcessingConfig, ReportContent
from .dual_channel import DualChannelRetriever
from .metric_corpus import metric_embeddings, resolve_metric_retrieval_corpus
from .metric_profile import (
    MetricRetrievalProfile,
    best_alias_matches,
    build_metric_retrieval_profile,
    find_metric_profile,
    load_all_metric_profiles,
    profile_to_metric_like,
    tokenize_metric_text,
)


def _has_semantic_corpus(report_content: ReportContent) -> bool:
    metric_corpus = getattr(report_content, "_metric_retrieval_corpus", None)
    if metric_corpus is not None and metric_embeddings(metric_corpus) is not None:
        return True
    matrix = getattr(report_content, "_embedding_matrix", None)
    if getattr(matrix, "ndim", 0) == 2 and int(matrix.shape[0]) > 0:
        return True
    semantic_cache = getattr(report_content, "_semantic_retrieval_embedding_cache", None)
    if isinstance(semantic_cache, tuple) and len(semantic_cache) >= 2:
        cached_matrix = semantic_cache[-1]
        if getattr(cached_matrix, "ndim", 0) == 2 and int(cached_matrix.shape[0]) > 0:
            return True
    return bool(report_content.embeddings)


def _get_default_config(top_k: int) -> ProcessingConfig:
    config = ProcessingConfig()
    config.top_k = int(top_k or getattr(config, "top_k", 50) or 50)
    return config


def _metric_from_spec(metric_spec: Optional[Any], query: str):
    if isinstance(metric_spec, str):
        profile = find_metric_profile(metric_spec)
        if profile is not None:
            return profile_to_metric_like(profile)
        metric_code = metric_spec.strip()
        return SimpleNamespace(
            metric_id=metric_code or "ad_hoc_query",
            metric_name=query or metric_code or "Ad hoc query",
            metric_code=metric_code,
            definition=query or metric_code,
            description=query or metric_code,
            keywords=[],
            semantic_expansion=None,
            unit="",
            sasb_category="",
            sasb_type="",
            sasb_topic="",
            source="",
        )

    if metric_spec is not None:
        return metric_spec

    return SimpleNamespace(
        metric_id="ad_hoc_query",
        metric_name=query,
        metric_code="",
        definition=query,
        description=query,
        keywords=[],
        semantic_expansion=None,
        unit="",
        sasb_category="",
        sasb_type="",
        sasb_topic="",
        source="",
    )


def retrieve_evidence(
    query: str,
    report_content: ReportContent,
    metric_spec: Optional[Any] = None,
    top_k: int = 50,
    use_keyword: bool = True,
    use_semantic: bool = True,
    use_rerank: bool = True,
    config: Optional[ProcessingConfig] = None,
):
    """Retrieve evidence from one report through a single stable interface."""
    enrich_document_with_pdf_links(report_content.document_content)
    config = config or _get_default_config(top_k)
    config.top_k = int(top_k or config.top_k or 50)
    config.use_keyword_retrieval = bool(use_keyword)
    config.use_semantic_retrieval = bool(use_semantic)
    config.use_reranker = bool(use_rerank)

    metric = _metric_from_spec(metric_spec, query)
    retriever = DualChannelRetriever(config)
    return retriever.retrieve_for_metric(report_content, metric)


def retrieve_metric_collection(
    report_content: ReportContent,
    metric_collection: Any,
    config: Optional[ProcessingConfig] = None,
):
    """Retrieve evidence for every metric in a MetricCollection."""
    enrich_document_with_pdf_links(report_content.document_content)
    config = config or _get_default_config(getattr(ProcessingConfig(), "top_k", 50))
    retriever = DualChannelRetriever(config)
    cache = getattr(report_content, "_metric_retrieval_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        object.__setattr__(report_content, "_metric_retrieval_cache", cache)
    expansions = {item.metric_id: item for item in (getattr(metric_collection, "semantic_expansions", None) or [])}
    config_payload = config.model_dump(mode="json") if hasattr(config, "model_dump") else vars(config)
    model_versions = {
        "embedding": os.getenv("EMBEDDING_MODEL", ""),
        "reranker": os.getenv("RERANK_MODEL", ""),
    }
    revision = document_content_revision(report_content)
    metric_corpus_signature = "legacy"
    if getattr(config, "use_metric_retrieval_corpus", True):
        try:
            metric_corpus_signature = resolve_metric_retrieval_corpus(
                report_content
            ).corpus_signature
        except Exception:
            metric_corpus_signature = "legacy"
    cached_revision = getattr(report_content, "_metric_retrieval_cache_revision", None)
    if cached_revision != revision:
        cache.clear()
        object.__setattr__(
            report_content,
            "_metric_retrieval_cache_revision",
            revision,
        )
    plans = []
    for metric in list(getattr(metric_collection, "metrics", None) or []):
        expansion = expansions.get(getattr(metric, "metric_id", ""))
        payload = {
            "document": report_content.document_id,
            "document_revision": revision,
            "metric_corpus_signature": metric_corpus_signature,
            "metric": metric.model_dump(mode="json") if hasattr(metric, "model_dump") else vars(metric),
            "expansion": expansion.model_dump(mode="json") if hasattr(expansion, "model_dump") else (vars(expansion) if expansion else None),
            "config": config_payload,
            "models": model_versions,
        }
        key = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":")).encode()).hexdigest()
        plans.append((metric, expansion, key, cache.get(key)))

    # Encode all currently uncached dense metric queries in one model call.
    # SemanticRetriever keeps the resulting rows by query text, so whole-report
    # and linked-page searches for the same metric reuse the same vector.
    uncached_pairs = [
        (metric, expansion)
        for metric, expansion, _key, result in plans
        if result is None
    ]
    if (
        getattr(config, "use_semantic_retrieval", True)
        and uncached_pairs
        and _has_semantic_corpus(report_content)
    ):
        retriever.semantic_retriever.prepare_metric_queries(uncached_pairs)

    results = []
    for metric, expansion, key, result in plans:
        if result is None:
            result = retriever.retrieve_for_metric(report_content, metric, expansion)
            cache[key] = result
        results.append(result)
    return results


def _profiles_for_mapping(metric_collection: Optional[Any] = None) -> List[MetricRetrievalProfile]:
    metrics = list(getattr(metric_collection, "metrics", []) or []) if metric_collection is not None else []
    if metrics:
        return [build_metric_retrieval_profile(metric) for metric in metrics]
    return load_all_metric_profiles()


def map_document_metrics(
    report_content: ReportContent,
    metric_collection: Optional[Any] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Lightweight document-to-metric mapping.

    This mines report chunks that look like metric mentions and maps them back to
    canonical SASB profile ids by exact alias/code overlap and token overlap. It
    is a low-cost candidate generator for report-specific or non-standard metric
    wording; it does not replace evidence retrieval.
    """
    profiles = _profiles_for_mapping(metric_collection)
    if not profiles:
        return []

    candidates: List[Dict[str, Any]] = []
    for segment in report_content.document_content.segments:
        content = getattr(segment, "content", "") or ""
        seg_type = str(getattr(segment, "segment_type", "") or "").lower()
        if seg_type not in {"table", "table_row", "table_cell", "heading", "text", "body_text", "paragraph_cluster", "list_item", "footnote", "caption", "index"}:
            continue
        tokens = set(tokenize_metric_text(content))
        if not tokens and seg_type not in {"table_row", "table_cell"}:
            continue
        for profile in profiles:
            alias_hits = best_alias_matches(content, profile.aliases, limit=6)
            token_hits = len(tokens.intersection(set(profile.anchor_terms)))
            if not alias_hits and token_hits < 2:
                continue
            score = min(1.0, 0.18 * len(alias_hits) + 0.035 * token_hits)
            candidates.append(
                {
                    "metric_id": profile.metric_id,
                    "metric_code": profile.metric_code,
                    "metric_name": profile.metric_name,
                    "segment_id": getattr(segment, "segment_id", ""),
                    "page_number": getattr(segment, "page_number", None),
                    "chunk_type": seg_type,
                    "score": round(score, 4),
                    "matched_aliases": alias_hits,
                    "candidate_phrase": content[:500],
                }
            )
    candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    return candidates[: max(1, int(top_k or 5))]


__all__ = [
    "retrieve_evidence",
    "retrieve_metric_collection",
    "map_document_metrics",
    "DualChannelRetriever",
]
