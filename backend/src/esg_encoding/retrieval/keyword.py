"""Metric-centric lexical evidence retrieval.

This module separates exact code search, exact alias search and BM25-style
keyword search.  The channels are fused later by weighted RRF so exact SASB/GRI
codes and canonical metric aliases cannot be drowned out by dense similarity.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from .metric_profile import MetricRetrievalProfile, best_alias_matches, build_metric_retrieval_profile, tokenize_metric_text
from .scoring import *  # noqa: F401,F403


class KeywordRetriever:
    """Metric-centric lexical retriever."""

    def __init__(self, config: ProcessingConfig):
        self.config = config

    def search_in_report(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        semantic_expansion: Optional[SemanticExpansion] = None,
    ) -> List[RetrievalResult]:
        """Backward-compatible lexical retrieval entry point."""
        profile = build_metric_retrieval_profile(metric, semantic_expansion)
        return self.search_metric_profile(report_content, metric, profile)

    def search_metric_profile(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: MetricRetrievalProfile,
    ) -> List[RetrievalResult]:
        channels = []
        channels.extend(self.search_exact_code(report_content, metric, profile))
        channels.extend(self.search_exact_alias(report_content, metric, profile))
        channels.extend(self.search_bm25(report_content, metric, profile))
        deduped: Dict[str, RetrievalResult] = {}
        for item in channels:
            prev = deduped.get(item.segment_id)
            if prev is None or float(item.score or 0.0) > float(prev.score or 0.0):
                deduped[item.segment_id] = item
        results = list(deduped.values())
        results.sort(key=lambda item: item.score, reverse=True)
        observed_matches = len(results)
        logger.info(f"Lexical metric retrieval for {getattr(metric, 'metric_id', 'unknown')} found {observed_matches} results")
        return results[:_internal_pool_size(self.config, metric, observed_matches=observed_matches, channel="keyword")]

    def search_exact_code(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[RetrievalResult]:
        """Exact standard-code index over report chunks."""
        profile = profile or build_metric_retrieval_profile(metric)
        if not profile.exact_code_patterns:
            return []
        results: List[RetrievalResult] = []
        for segment in report_content.document_content.segments:
            content = getattr(segment, "content", "") or ""
            if not any(pattern.search(content) for pattern in profile.exact_code_patterns):
                continue
            score = self._score_exact_segment(segment, metric, profile, base=1.00)
            results.append(
                RetrievalResult(
                    segment_id=segment.segment_id,
                    content=content,
                    page_number=segment.page_number,
                    score=score,
                    retrieval_type="exact_code",
                    matched_keywords=[profile.metric_code],
                    metric_id=profile.metric_id or getattr(metric, "metric_id", ""),
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:_internal_pool_size(self.config, metric, observed_matches=len(results), channel="keyword")]

    def search_exact_alias(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[RetrievalResult]:
        """Exact canonical alias / metric-name inverted index over chunks."""
        profile = profile or build_metric_retrieval_profile(metric)
        aliases = [alias for alias in profile.aliases if alias and alias != profile.metric_code]
        if not aliases:
            return []
        results: List[RetrievalResult] = []
        for segment in report_content.document_content.segments:
            content = getattr(segment, "content", "") or ""
            matched_aliases = best_alias_matches(content, aliases, limit=16)
            if not matched_aliases:
                continue
            base = min(0.94, 0.66 + 0.06 * len(matched_aliases))
            score = self._score_exact_segment(segment, metric, profile, base=base)
            results.append(
                RetrievalResult(
                    segment_id=segment.segment_id,
                    content=content,
                    page_number=segment.page_number,
                    score=score,
                    retrieval_type="exact_alias",
                    matched_keywords=matched_aliases,
                    metric_id=profile.metric_id or getattr(metric, "metric_id", ""),
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:_internal_pool_size(self.config, metric, observed_matches=len(results), channel="keyword")]

    def search_bm25(
        self,
        report_content: ReportContent,
        metric: ESGMetric,
        profile: Optional[MetricRetrievalProfile] = None,
    ) -> List[RetrievalResult]:
        """BM25-style retrieval over MinerU Markdown/TextSegment chunks."""
        profile = profile or build_metric_retrieval_profile(metric)
        query_tokens = self._query_tokens(profile)
        if not query_tokens:
            return []

        segments = list(report_content.document_content.segments)
        doc_tokens = [self._segment_tokens(segment) for segment in segments]
        if not doc_tokens:
            return []

        n_docs = len(doc_tokens)
        avg_len = sum(len(tokens) for tokens in doc_tokens) / max(n_docs, 1)
        doc_freq: Counter[str] = Counter()
        for tokens in doc_tokens:
            doc_freq.update(set(tokens))

        raw_scores: List[Tuple[object, float, List[str]]] = []
        for segment, tokens in zip(segments, doc_tokens):
            if not tokens:
                continue
            counts = Counter(tokens)
            matched = [token for token in query_tokens if counts.get(token, 0) > 0]
            if not matched:
                continue
            score = self._bm25_score(counts, len(tokens), query_tokens, doc_freq, n_docs, avg_len)
            if score <= 0:
                continue
            raw_scores.append((segment, score, matched))

        if not raw_scores:
            return []
        max_score = max(score for _, score, _ in raw_scores) or 1.0
        anchor_terms = profile.anchor_terms or _extract_metric_anchor_terms(metric)
        results: List[RetrievalResult] = []
        for segment, score, matched in raw_scores:
            normalized = min(1.0, score / max_score)
            adjusted = _clamp_score(
                0.50 * normalized
                + _segment_structure_bonus(
                    segment,
                    expected_unit=getattr(metric, "unit", None),
                    prefer_narrative=not _is_quantitative_metric(metric),
                )
                + _qualitative_relevance_adjustment(metric, getattr(segment, "content", "") or "", anchor_terms, getattr(segment, "segment_type", ""))
                + _metric_evidence_quality_adjustment(metric, segment, anchor_terms)
            )
            if adjusted < 0.08:
                continue
            results.append(
                RetrievalResult(
                    segment_id=segment.segment_id,
                    content=getattr(segment, "content", "") or "",
                    page_number=segment.page_number,
                    score=adjusted,
                    retrieval_type="bm25",
                    matched_keywords=matched[:18],
                    metric_id=profile.metric_id or getattr(metric, "metric_id", ""),
                )
            )
        results.sort(key=lambda item: item.score, reverse=True)
        observed_matches = len(results)
        return results[:_internal_pool_size(self.config, metric, observed_matches=observed_matches, channel="keyword")]

    def search_keywords_in_text(
        self,
        text: str,
        keywords: Sequence[str],
        case_sensitive: bool = False,
    ) -> List[Tuple[str, List[int]]]:
        """Simple phrase search kept for compatibility with older callers."""
        results = []
        flags = 0 if case_sensitive else re.IGNORECASE
        for keyword in keywords:
            pattern = re.escape(str(keyword or ""))
            if not pattern:
                continue
            matches = [(m.start(), m.end()) for m in re.finditer(pattern, text or "", flags)]
            if matches:
                results.append((str(keyword), matches))
        return results

    def _score_exact_segment(
        self,
        segment,
        metric: ESGMetric,
        profile: MetricRetrievalProfile,
        base: float,
    ) -> float:
        score = float(base)
        score += _segment_structure_bonus(
            segment,
            expected_unit=getattr(metric, "unit", None),
            prefer_narrative=not _is_quantitative_metric(metric),
        )
        score += _metric_evidence_quality_adjustment(metric, segment, profile.anchor_terms)
        return _clamp_score(score)

    def _query_tokens(self, profile: MetricRetrievalProfile) -> List[str]:
        tokens: List[str] = []
        for term in profile.bm25_terms:
            tokens.extend(tokenize_metric_text(term))
        # Keep deterministic order and avoid huge BM25 queries.
        seen = set()
        out = []
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= 48:
                break
        return out

    def _segment_tokens(self, segment) -> List[str]:
        structured_parts = [
            getattr(segment, "content", "") or "",
            getattr(segment, "row_header", "") or "",
            getattr(segment, "col_header", "") or "",
            getattr(segment, "value_text", "") or "",
            getattr(segment, "unit", "") or "",
        ]
        structured_data = getattr(segment, "structured_data", None)
        if isinstance(structured_data, dict):
            for key in ("table_title", "table_id", "row_header", "column_headers", "caption"):
                value = structured_data.get(key)
                if isinstance(value, list):
                    structured_parts.extend(str(v) for v in value)
                elif value:
                    structured_parts.append(str(value))
        return tokenize_metric_text(" ".join(structured_parts))

    def _bm25_score(
        self,
        counts: Counter[str],
        doc_len: int,
        query_tokens: Sequence[str],
        doc_freq: Counter[str],
        n_docs: int,
        avg_len: float,
    ) -> float:
        k1 = 1.4
        b = 0.72
        score = 0.0
        for token in query_tokens:
            tf = counts.get(token, 0)
            if tf <= 0:
                continue
            df = max(1, doc_freq.get(token, 0))
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1.0 - b + b * (doc_len / max(avg_len, 1.0)))
            score += idf * (tf * (k1 + 1.0)) / max(denom, 1e-9)
        return float(score)

    # Legacy helper kept for callers that still use the old class method.
    def _calculate_keyword_score(
        self,
        segment,
        metric: ESGMetric,
        matched_keywords: List[str],
        total_matches: int,
        weight_map: Optional[Dict[str, float]] = None,
        anchor_terms: Optional[List[str]] = None,
    ) -> float:
        weight_map = weight_map or {kw: 1.0 for kw in matched_keywords}
        total_weight = max(0.0001, sum(float(v or 0.0) for v in weight_map.values()))
        matched_weight = sum(float(weight_map.get(term, 0.0)) for term in set(matched_keywords))
        unique_match_score = matched_weight / total_weight
        density_bonus = min(0.18, 0.02 * max(0, total_matches - len(set(matched_keywords))))
        structure_bonus = _segment_structure_bonus(segment, expected_unit=getattr(metric, "unit", None), prefer_narrative=not _is_quantitative_metric(metric))
        relevance_adjustment = _qualitative_relevance_adjustment(metric, getattr(segment, "content", "") or "", anchor_terms or [], getattr(segment, "segment_type", ""))
        return float(max(0.0, min(1.0, unique_match_score + density_bonus + structure_bonus + relevance_adjustment)))

    def _segment_structure_bonus(self, segment, expected_unit: Optional[str] = None, prefer_narrative: bool = False) -> float:
        return _segment_structure_bonus(segment, expected_unit=expected_unit, prefer_narrative=prefer_narrative)
