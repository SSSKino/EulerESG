"""Metric-centric dual retrieval orchestration.

The retriever follows the project RAG plan:
- exact code index
- exact alias index
- BM25 keyword index
- dense vector index
- weighted RRF fusion
- exact-metric rerank
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Sequence

from .scoring import *  # noqa: F401,F403
from .fusion import exact_metric_rerank, rrf_fuse


def _is_exact_code_result(result: RetrievalResult) -> bool:
    return "exact_code" in str(getattr(result, "retrieval_type", "") or "")


def _code_first_sort_key(result: RetrievalResult):
    return (1 if _is_exact_code_result(result) else 0, float(getattr(result, "score", 0.0) or 0.0))
from .keyword import KeywordRetriever
from .metric_profile import build_metric_retrieval_profile
from .semantic import SemanticRetriever


class DualChannelRetriever:
    """Metric-centric dual retriever."""

    def __init__(self, config: ProcessingConfig):
        self.config = config
        self.keyword_retriever = KeywordRetriever(config)
        self.semantic_retriever = SemanticRetriever(config)

    def retrieve_for_metric(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        semantic_expansion: Optional[SemanticExpansion] = None,
    ) -> MetricRetrievalResult:
        """Retrieve evidence for one canonical metric."""
        try:
            metric_name = getattr(metric, "metric_name", "") or getattr(metric, "metric_id", "")
            logger.info(f"Starting metric-centric retrieval for metric {metric_name}")
            profile = build_metric_retrieval_profile(metric, semantic_expansion)

            exact_code_results: List[RetrievalResult] = []
            exact_alias_results: List[RetrievalResult] = []
            bm25_results: List[RetrievalResult] = []
            if getattr(self.config, "use_keyword_retrieval", True):
                exact_code_results = self.keyword_retriever.search_exact_code(report_content, metric, profile)
                exact_alias_results = self.keyword_retriever.search_exact_alias(report_content, metric, profile)
                bm25_results = self.keyword_retriever.search_bm25(report_content, metric, profile)

            semantic_results: List[RetrievalResult] = []
            if getattr(self.config, "use_semantic_retrieval", True) and profile.dense_query:
                semantic_results = self.semantic_retriever.search_by_semantic(report_content, metric, semantic_expansion)

            combined_results = self._combine_results(
                keyword_results=exact_code_results + exact_alias_results + bm25_results,
                semantic_results=semantic_results,
                metric=metric,
                report_content=report_content,
                channel_results={
                    "exact_code": exact_code_results,
                    "exact_alias": exact_alias_results,
                    "bm25": bm25_results,
                    "semantic": semantic_results,
                },
                profile=profile,
            )

            result = MetricRetrievalResult(
                metric_id=getattr(metric, "metric_id", profile.metric_id),
                metric_name=getattr(metric, "metric_name", profile.metric_name),
                metric_code=getattr(metric, "metric_code", profile.metric_code),
                keyword_results=exact_code_results + exact_alias_results + bm25_results,
                semantic_results=semantic_results,
                combined_results=combined_results,
                total_matches=len(combined_results),
            )
            logger.info(f"Metric-centric retrieval completed for {metric_name}, found {len(combined_results)} results")
            return result
        except Exception as exc:
            logger.error(f"Metric retrieval failed: {str(exc)}")
            raise ESGEncodingError(f"Metric retrieval failed: {str(exc)}") from exc

    def _combine_results(
        self,
        keyword_results: List[RetrievalResult],
        semantic_results: List[RetrievalResult],
        metric: Optional[ESGMetric] = None,
        report_content: Optional[ReportContent] = None,
        channel_results: Optional[Dict[str, Sequence[RetrievalResult]]] = None,
        profile=None,
    ) -> List[RetrievalResult]:
        """Fuse retrieval channels and run exact-metric reranking.

        The method keeps the old signature while supporting explicit channel
        groups for weighted RRF.  If called by older code without channel_groups,
        it still fuses keyword and semantic results.
        """
        if channel_results is None:
            channel_results = {
                "keyword": keyword_results,
                "semantic": semantic_results,
            }

        fused = rrf_fuse(channel_results)
        if metric is not None and report_content is not None:
            anchors = profile.anchor_terms if profile is not None else _extract_metric_anchor_terms(metric)
            fused.extend(_synthesize_qualitative_clusters(report_content, metric, fused, anchors))
            deduped: Dict[str, RetrievalResult] = {}
            for result in fused:
                current = deduped.get(result.segment_id)
                if current is None or float(result.score or 0.0) > float(current.score or 0.0):
                    deduped[result.segment_id] = result
            fused = list(deduped.values())

        fused.sort(key=lambda item: item.score, reverse=True)
        if metric is None:
            return fused[: max(1, int(getattr(self.config, "top_k", 10) or 10))]

        final_k = _target_window_size(self.config, metric, observed_matches=len(fused))
        final_k = max(11, final_k)
        if profile is None:
            profile = build_metric_retrieval_profile(metric)
        reranked = exact_metric_rerank(
            fused,
            metric=metric,
            profile=profile,
            report_content=report_content,
            top_k=final_k,
        )
        if metric is not None:
            reranked = _rebalance_qualitative_results(metric, reranked, report_content=report_content)
            # Preserve canonical code hits as the highest-priority evidence even
            # after qualitative/narrative rebalancing.  This keeps exact Code
            # search ahead of alias, BM25 and dense retrieval results.
            reranked.sort(key=_code_first_sort_key, reverse=True)
        return reranked[:final_k]

    def retrieve_for_collection(
        self,
        report_content: ReportContent,
        metric_collection: MetricCollection,
    ) -> List[MetricRetrievalResult]:
        """Retrieve evidence for all metrics in a collection."""
        results: List[MetricRetrievalResult] = []
        expansions_map = {exp.metric_id: exp for exp in metric_collection.semantic_expansions}
        for metric in metric_collection.metrics:
            logger.info(f"Retrieving metric: {metric.metric_name}")
            results.append(
                self.retrieve_for_metric(
                    report_content,
                    metric,
                    expansions_map.get(metric.metric_id),
                )
            )
        logger.info(f"Completed metric collection retrieval, processed {len(results)} metrics")
        return results

    def generate_retrieval_report(self, retrieval_results: List[MetricRetrievalResult]) -> str:
        """Generate a Markdown retrieval report."""
        report_lines = [
            "# ESG Metric Retrieval Report\n",
            f"**Generated Time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"**Number of Retrieved Metrics**: {len(retrieval_results)}\n",
            f"**Total Matched Segments**: {sum(r.total_matches for r in retrieval_results)}\n",
            "---\n",
        ]
        for result in retrieval_results:
            report_lines.extend([
                f"## {result.metric_name} ({result.metric_id})\n",
                f"**Total Matches**: {result.total_matches}\n",
                f"**Lexical Matches**: {len(result.keyword_results)}\n",
                f"**Semantic Matches**: {len(result.semantic_results)}\n",
                "",
            ])
            if result.combined_results:
                report_lines.append("### Best Matching Segments\n")
                for i, match in enumerate(result.combined_results[:5], 1):
                    report_lines.extend([
                        f"**{i}. Segment {match.segment_id}** (Page: {match.page_number}, Score: {match.score:.3f})\n",
                        f"*Retrieval Type: {match.retrieval_type}*\n",
                        f"*Matched Keywords: {', '.join(match.matched_keywords) if match.matched_keywords else 'None'}*\n",
                        f"```\n{match.content[:200]}...\n```\n",
                        "",
                    ])
            report_lines.append("---\n")
        return "\n".join(report_lines)
