"""
Dual-Channel Metrics Retrieval Module

This module implements dual-channel metric retrieval, including:
1. Keyword Retrieval - Keyword-based retrieval
2. Semantic Retrieval - Semantic-based retrieval
"""

import os
import re
import math
import threading
import numpy as np
from typing import Dict, List, Optional, Union, Tuple
from loguru import logger
import torch
from sklearn.metrics.pairwise import cosine_similarity

from .flag_reranker import get_reranker
from .hipporag_settings import HippoRAGSettings
from .shared_embedding_model import encode_query_texts, get_shared_embedding_model
from .embedding_settings import get_configured_embedding_local_path, get_configured_rerank_model_name

from .models import (
    ESGMetric, SemanticExpansion, MetricCollection, ReportContent,
    RetrievalResult, MetricRetrievalResult, ProcessingConfig
)
from .exceptions import ESGEncodingError, ContentEmbeddingError

_GENERIC_METRIC_TERMS = {
    "description", "discussion", "approach", "management", "number", "percentage", "amount", "total",
    "weight", "list", "countries", "products", "services", "employees", "facilities", "metric",
    "associated", "including", "resulting", "data", "user", "users", "information", "risks", "risk",
    "process", "policies", "practices", "related", "activity", "activities", "topics", "topic",
    "sustainability", "disclosure", "metrics", "all", "other", "core", "required", "eligible",
}

_ENVIRONMENTAL_NOISE_TERMS = {
    "emissions", "ghg", "scope 1", "scope 2", "scope 3", "water", "waste", "renewable", "electricity",
    "energy", "supplier environmental", "recycled", "recycling", "e-waste", "potable", "mwh", "gj",
    "co2e", "tco2e", "landfilled", "environmental program", "emission reduction", "supplier emissions",
}

_FOCUS_TERMS = {
    "privacy_security": {"privacy", "data security", "cybersecurity", "breach", "breaches", "vulnerability", "user information", "law enforcement", "security", "targeted advertising", "monitoring", "censoring"},
    "workforce": {"gender", "diversity", "executive", "non-executive", "technical employees", "employee engagement", "work visa", "people leaders", "leadership", "management"},
    "operations": {"service disruption", "downtime", "business continuity", "performance issues", "operations", "cloud-based", "data storage", "data processing capacity", "licences", "subscriptions"},
    "supply_chain": {"tier 1", "rba", "vap", "supplier facilities", "corrective action", "non-conformance", "audit", "high-risk facilities"},
    "product_compliance": {"epeat", "iec 62474", "energy efficiency certification", "declarable substances", "critical materials", "end-of-life", "e-waste"},
}


_SOFT_CATEGORY_ALIASES = {
    "executive management": ["executive leadership", "executive officers", "senior leadership", "c-suite", "senior officials"],
    "non-executive management": ["people leaders", "people managers", "management roles", "management", "leaders"],
    "technical employees": ["technical workforce", "engineers", "engineering", "developers", "technical talent", "r&d employees"],
    "all other employees": ["global workforce", "workforce", "employees", "remaining employees", "broader workforce"],
    "data security risks in products": ["product security", "secure development lifecycle", "secure by design", "sbom", "passwordless authentication", "data sanitization"],
}


def _soft_metric_aliases(metric: ESGMetric) -> List[str]:
    text = " ".join([str(getattr(metric, "metric_name", "") or ""), str(getattr(metric, "sasb_topic", "") or "")]).lower()
    aliases: List[str] = []
    for needle, values in _SOFT_CATEGORY_ALIASES.items():
        if needle in text:
            aliases.extend(values)
    return list(dict.fromkeys([alias for alias in aliases if alias]))


def _is_quantitative_metric(metric: ESGMetric) -> bool:
    category = str(getattr(metric, "sasb_category", "") or "").strip().lower()
    metric_type = str(getattr(metric, "sasb_type", "") or "").strip().lower()
    unit = str(getattr(metric, "unit", "") or "").strip()
    return bool(unit) or category == "quantitative" or "quantitative" in metric_type


def _metric_complexity_score(metric: ESGMetric) -> int:
    metric_name = str(getattr(metric, "metric_name", "") or "")
    topic = str(getattr(metric, "sasb_topic", "") or "")
    definition = str(getattr(metric, "definition", "") or getattr(metric, "description", "") or "")
    keywords = list(getattr(metric, "keywords", None) or [])
    token_count = len(re.findall(r"[A-Za-z0-9]{3,}", " ".join([metric_name, topic, definition])))
    complexity = 0
    complexity += min(6, token_count // 14)
    complexity += min(5, len(keywords) // 4)
    if any(ch in metric_name for ch in ["(", ")", "/", "%"]):
        complexity += 1
    return complexity


def _compute_target_window(metric: ESGMetric, observed_matches: int = 0, base_top_k: int = 10) -> int:
    """Dynamic window with no hard upper cap. Grows with metric complexity and available evidence."""
    base = 22 if _is_quantitative_metric(metric) else 14
    complexity_bonus = _metric_complexity_score(metric)
    observed_matches = max(0, int(observed_matches or 0))
    richness_bonus = 0
    if observed_matches > 0:
        richness_bonus += int(math.log1p(observed_matches) * (6 if _is_quantitative_metric(metric) else 5))
        richness_bonus += int(math.sqrt(observed_matches) / (4 if _is_quantitative_metric(metric) else 5))
        if observed_matches >= 120:
            richness_bonus += 4
        if observed_matches >= 300:
            richness_bonus += 6
        if observed_matches >= 800:
            richness_bonus += 8
    return max(11, max(int(base_top_k or 10), base) + complexity_bonus + richness_bonus)


def _compute_internal_pool(metric: ESGMetric, observed_matches: int = 0, base_top_k: int = 10, channel: str = "keyword") -> int:
    target = _compute_target_window(metric, observed_matches=observed_matches, base_top_k=base_top_k)
    is_quant = _is_quantitative_metric(metric)
    if channel == "semantic":
        floor = 180 if is_quant else 96
        multiplier = 4 if is_quant else 3
        overflow = max(0, observed_matches // (5 if is_quant else 7))
    else:
        floor = 120 if is_quant else 72
        multiplier = 3 if is_quant else 2
        overflow = max(0, observed_matches // (7 if is_quant else 10))
    return max(target * multiplier + overflow, floor)


def _target_window_size(config: ProcessingConfig, metric: ESGMetric, observed_matches: int = 0) -> int:
    base_top_k = int(getattr(config, "top_k", 10) or 10)
    return _compute_target_window(metric, observed_matches=observed_matches, base_top_k=base_top_k)


def _internal_pool_size(config: ProcessingConfig, metric: ESGMetric, observed_matches: int = 0, channel: str = "keyword") -> int:
    base_top_k = int(getattr(config, "top_k", 10) or 10)
    return _compute_internal_pool(metric, observed_matches=observed_matches, base_top_k=base_top_k, channel=channel)


def _segment_structure_bonus(segment, expected_unit: Optional[str] = None, prefer_narrative: bool = False) -> float:
    bonus = 0.0
    seg_type = str(getattr(segment, "segment_type", "") or "").lower()
    content = str(getattr(segment, "content", "") or "")
    if prefer_narrative:
        if seg_type == "heading":
            bonus += 0.12
        elif seg_type == "paragraph_cluster":
            bonus += 0.16
        elif seg_type in {"body_text", "text"}:
            bonus += 0.08
        elif seg_type == "table_row":
            bonus += 0.03
        elif seg_type == "table_cell":
            bonus += 0.01
        elif seg_type == "ocr_text":
            bonus -= 0.01
    else:
        if seg_type == "table_cell":
            bonus += 0.14
        elif seg_type == "table_row":
            bonus += 0.08
        elif seg_type == "table":
            bonus += 0.04
        elif seg_type == "paragraph_cluster":
            bonus += 0.04
        elif seg_type == "heading":
            bonus += 0.03
        elif seg_type == "body_text":
            bonus += 0.02
        elif seg_type == "ocr_text":
            bonus -= 0.01
    if re.search(r"-?\d[\d,]*(?:\.\d+)?", content):
        bonus += 0.02 if expected_unit is not None else 0.04
    if getattr(segment, "row_header", None):
        bonus += 0.05
    if getattr(segment, "col_header", None):
        bonus += 0.04
    if getattr(segment, "value_text", None):
        bonus += 0.06
    normalized_unit = str(expected_unit or "").strip().lower()
    if normalized_unit:
        unit_candidates = {normalized_unit}
        if normalized_unit == "m3":
            unit_candidates.add("m³")
        if normalized_unit == "tco2e":
            unit_candidates.update({"tco₂e", "co2e", "co₂e"})
        lower_content = content.lower()
        if any(unit in lower_content for unit in unit_candidates if unit):
            bonus += 0.06
    return bonus


def _normalize_text_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _extract_metric_anchor_terms(metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None, max_terms: int = 28) -> List[str]:
    raw_terms: List[str] = []
    for field in [getattr(metric, "metric_name", None), getattr(metric, "metric_code", None), getattr(metric, "sasb_topic", None), getattr(metric, "definition", None), getattr(metric, "description", None)]:
        if field:
            raw_terms.append(str(field))
    for term in (getattr(metric, "keywords", None) or []):
        raw_terms.append(str(term))
    if semantic_expansion is not None:
        if getattr(semantic_expansion, "semantic_description", None):
            raw_terms.append(str(getattr(semantic_expansion, "semantic_description", "")))
        for term in (getattr(semantic_expansion, "expanded_keywords", None) or []):
            raw_terms.append(str(term))
    for alias in _soft_metric_aliases(metric):
        raw_terms.append(str(alias))

    anchors: List[str] = []
    seen = set()
    for raw in raw_terms:
        raw = _normalize_text_for_match(raw)
        if not raw:
            continue
        if len(raw) <= 32 and raw not in _GENERIC_METRIC_TERMS and not raw.isdigit():
            if raw not in seen:
                anchors.append(raw)
                seen.add(raw)
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", raw):
            if token in _GENERIC_METRIC_TERMS or token.isdigit():
                continue
            if token not in seen:
                anchors.append(token)
                seen.add(token)
            if len(anchors) >= max_terms:
                return anchors
        if len(anchors) >= max_terms:
            return anchors
    return anchors[:max_terms]


def _detect_metric_focus(metric: ESGMetric) -> str:
    text = " ".join([
        str(getattr(metric, "metric_name", "") or ""),
        str(getattr(metric, "sasb_topic", "") or ""),
        str(getattr(metric, "definition", "") or getattr(metric, "description", "") or ""),
    ]).lower()
    for focus, terms in _FOCUS_TERMS.items():
        if any(term in text for term in terms):
            return focus
    return "general"


def _count_anchor_hits(text: str, anchors: List[str]) -> int:
    lowered = _normalize_text_for_match(text)
    hits = 0
    for anchor in anchors:
        if anchor and anchor in lowered:
            hits += 1
    return hits


def _qualitative_relevance_adjustment(metric: ESGMetric, content: str, anchors: List[str], segment_type: str = "") -> float:
    category = str(getattr(metric, "sasb_category", "") or "").strip().lower()
    metric_type = str(getattr(metric, "sasb_type", "") or "").strip().lower()
    unit = str(getattr(metric, "unit", "") or "").strip()
    is_quantitative = bool(unit) or category == "quantitative" or "quantitative" in metric_type
    lowered = _normalize_text_for_match(content)
    anchor_hits = _count_anchor_hits(lowered, anchors)
    seg_type = str(segment_type or "").lower()
    if is_quantitative:
        return min(0.10, anchor_hits * 0.02)

    adjustment = 0.0
    if anchor_hits > 0:
        adjustment += min(0.34, 0.07 * anchor_hits)
    else:
        adjustment -= 0.12

    narrative_phrases = [
        "policy", "policies", "practice", "practices", "process", "processes", "approach", "governance",
        "framework", "program", "programme", "oversight", "management", "strategy", "responsibility",
        "responsible", "risk management", "procedure", "procedures", "controls", "control environment"
    ]
    if any(term in lowered for term in narrative_phrases):
        adjustment += 0.10

    if seg_type == "paragraph_cluster":
        adjustment += 0.16
    elif seg_type == "heading":
        adjustment += 0.08
    elif seg_type in {"body_text", "text", "ocr_text"}:
        adjustment += 0.06
    elif seg_type == "table_row":
        adjustment -= 0.02
    elif seg_type == "table_cell":
        adjustment -= 0.12

    focus = _detect_metric_focus(metric)
    focus_terms = _FOCUS_TERMS.get(focus, set())
    focus_hits = sum(1 for term in focus_terms if term in lowered)
    env_noise_hits = sum(1 for term in _ENVIRONMENTAL_NOISE_TERMS if term in lowered)
    if focus != "general":
        if focus_hits > 0:
            adjustment += min(0.24, 0.07 * focus_hits)
        elif env_noise_hits >= 2 and anchor_hits == 0:
            adjustment -= 0.34

    alias_hits = sum(1 for alias in _soft_metric_aliases(metric) if alias in lowered)
    if alias_hits > 0:
        adjustment += min(0.18, 0.06 * alias_hits)
    return adjustment


def _unit_aliases(unit: str | None) -> List[str]:
    normalized = str(unit or "").strip().lower()
    if not normalized:
        return []
    aliases = {normalized}
    compact = normalized.replace(" ", "")
    aliases.add(compact)
    if normalized in {"%", "percent", "percentage"}:
        aliases.update({"%", "percent", "percentage"})
    if normalized in {"m3", "m³", "cubic meters", "cubic metres"}:
        aliases.update({"m3", "m³", "cubic meters", "cubic metres"})
    if normalized in {"tco2e", "mtco2e", "co2e", "co₂e"}:
        aliases.update({"tco2e", "tco₂e", "mtco2e", "mtco₂e", "co2e", "co₂e", "tonnes co2e", "metric tons co2e"})
    if normalized in {"mwh", "megawatt hours", "megawatt-hours"}:
        aliases.update({"mwh", "megawatt hours", "megawatt-hours"})
    return [a for a in aliases if a]


def _metric_evidence_quality_adjustment(metric: ESGMetric, segment, anchors: List[str]) -> float:
    """Small CPU-only evidence-quality signal for final retrieval ranking.

    This improves ranking quality without increasing model size, GPU batch size,
    or passage re-encoding. It only uses already available text/metadata.
    """
    content = str(getattr(segment, "content", "") or "")
    lowered = _normalize_text_for_match(content)
    anchor_hits = _count_anchor_hits(lowered, anchors)
    has_number = bool(re.search(r"-?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|percentage|[a-zA-Zµμ³₂/.-]+)?", content))
    adjustment = 0.0

    if _is_quantitative_metric(metric):
        if has_number:
            adjustment += 0.06
        else:
            adjustment -= 0.10
        unit_hits = sum(1 for unit in _unit_aliases(getattr(metric, "unit", None)) if unit in lowered)
        if unit_hits > 0:
            adjustment += 0.07
        if anchor_hits >= 2:
            adjustment += 0.04
        elif anchor_hits == 0:
            adjustment -= 0.05
        future_only_terms = ("target", "goal", "aim", "aspire", "by 2030", "by 2040", "by 2050", "commitment")
        if any(term in lowered for term in future_only_terms) and not re.search(r"\b20(?:1\d|2[0-9])\b|fy\s?2[0-9]|fiscal year|during the year|reported", lowered):
            adjustment -= 0.05
    else:
        if anchor_hits >= 2:
            adjustment += 0.05
        elif anchor_hits == 0:
            adjustment -= 0.06
        process_terms = ("policy", "process", "procedure", "control", "governance", "oversight", "risk management", "approach", "program", "programme")
        if any(term in lowered for term in process_terms):
            adjustment += 0.04

    focus = _detect_metric_focus(metric)
    if focus != "general":
        focus_terms = _FOCUS_TERMS.get(focus, set())
        focus_hits = sum(1 for term in focus_terms if term in lowered)
        noise_hits = sum(1 for term in _ENVIRONMENTAL_NOISE_TERMS if term in lowered)
        if focus_hits > 0:
            adjustment += min(0.05, 0.02 * focus_hits)
        elif noise_hits >= 2 and anchor_hits == 0:
            adjustment -= 0.08

    return float(max(-0.18, min(0.18, adjustment)))


def _clamp_score(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _is_narrative_segment(segment) -> bool:
    seg_type = str(getattr(segment, "segment_type", "") or "").lower()
    return seg_type in {"text", "body_text", "heading", "paragraph_cluster", "ocr_text"}


def _ordered_segments(report_content: ReportContent):
    return sorted(
        report_content.document_content.segments,
        key=lambda seg: (
            int(getattr(seg, "page_number", 0) or 0),
            float(getattr(seg, "position_y", 0.0) or 0.0),
            float(getattr(seg, "position_x", 0.0) or 0.0),
            getattr(seg, "segment_id", ""),
        ),
    )


def _synthesize_qualitative_clusters(report_content: ReportContent, metric: ESGMetric, base_results: List[RetrievalResult], anchors: List[str]) -> List[RetrievalResult]:
    if _is_quantitative_metric(metric):
        return []
    result_ids = {r.segment_id for r in base_results}
    by_id = {getattr(seg, "segment_id", ""): seg for seg in report_content.document_content.segments}
    ordered = _ordered_segments(report_content)
    clusters: List[RetrievalResult] = []
    seen_keys = set()
    for idx, seg in enumerate(ordered):
        if getattr(seg, "segment_id", "") not in result_ids or not _is_narrative_segment(seg):
            continue
        seg_text = getattr(seg, "content", "") or ""
        if _count_anchor_hits(seg_text, anchors) <= 0:
            continue
        chain = [seg]
        for nxt in ordered[idx + 1: idx + 3]:
            if not _is_narrative_segment(nxt):
                break
            if int(getattr(nxt, "page_number", 0) or 0) != int(getattr(seg, "page_number", 0) or 0):
                break
            y_gap = float(getattr(nxt, "position_y", 0.0) or 0.0) - float(getattr(chain[-1], "position_y", 0.0) or 0.0)
            x_gap = abs(float(getattr(nxt, "position_x", 0.0) or 0.0) - float(getattr(chain[-1], "position_x", 0.0) or 0.0))
            if y_gap > 90 or x_gap > 70:
                break
            if _count_anchor_hits(getattr(nxt, "content", "") or "", anchors) <= 0 and str(getattr(nxt, "segment_type", "") or "").lower() != "body_text":
                break
            chain.append(nxt)
        if len(chain) < 2:
            continue
        key = tuple(getattr(s, "segment_id", "") for s in chain)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        content_parts = []
        score_seed = []
        matched_terms = []
        for part in chain:
            label = str(getattr(part, "segment_type", "") or "body_text").lower()
            header = "[Heading]" if label == "heading" else "[Narrative]"
            content_parts.append(f"{header}\n{getattr(part, 'content', '') or ''}")
            part_result = next((r for r in base_results if r.segment_id == getattr(part, "segment_id", "")), None)
            if part_result is not None:
                score_seed.append(float(part_result.score or 0.0))
                matched_terms.extend(part_result.matched_keywords or [])
        cluster_text = "\n\n".join(content_parts).strip()
        if not cluster_text:
            continue
        cluster_score = (sum(score_seed) / len(score_seed)) if score_seed else 0.35
        cluster_score += min(0.18, 0.04 * sum(_count_anchor_hits(getattr(part, "content", "") or "", anchors) for part in chain))
        clusters.append(RetrievalResult(
            segment_id=f"CLUSTER::{key[0]}::{key[-1]}",
            content=cluster_text,
            page_number=int(getattr(seg, "page_number", 0) or 0),
            score=max(0.0, min(1.0, cluster_score)),
            retrieval_type="qualitative_cluster",
            matched_keywords=list(dict.fromkeys(matched_terms))[:12],
            metric_id=metric.metric_id,
        ))
    return clusters


def _rebalance_qualitative_results(metric: ESGMetric, results: List[RetrievalResult], report_content: Optional[ReportContent] = None) -> List[RetrievalResult]:
    if _is_quantitative_metric(metric) or not results:
        return results
    by_id = {getattr(seg, "segment_id", ""): seg for seg in (report_content.document_content.segments if report_content is not None else [])}
    def is_narrative_result(res: RetrievalResult) -> bool:
        if str(getattr(res, "retrieval_type", "") or "") == "qualitative_cluster":
            return True
        seg = by_id.get(res.segment_id)
        if seg is not None:
            return _is_narrative_segment(seg)
        lowered = _normalize_text_for_match(getattr(res, "content", "") or "")
        return lowered.startswith("[heading]") or lowered.startswith("[narrative]")
    narrative = [r for r in results if is_narrative_result(r)]
    other = [r for r in results if not is_narrative_result(r)]
    narrative.sort(key=lambda r: float(r.score or 0.0), reverse=True)
    other.sort(key=lambda r: float(r.score or 0.0), reverse=True)
    merged: List[RetrievalResult] = []
    seen = set()
    for bucket in (narrative, other):
        for item in bucket:
            if item.segment_id in seen:
                continue
            merged.append(item)
            seen.add(item.segment_id)
    return merged


class KeywordRetriever:
    """Keyword retriever"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize keyword retriever
        
        Args:
            config: Processing configuration
        """
        self.config = config

    def _dedupe_terms(self, weighted_terms: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
        deduped: Dict[str, float] = {}
        for term, weight in weighted_terms:
            cleaned = re.sub(r"\s+", " ", str(term or "").strip())
            if not cleaned or len(cleaned) < 2:
                continue
            key = cleaned.lower()
            deduped[key] = max(float(weight or 0.0), deduped.get(key, 0.0))
        return [(term, weight) for term, weight in ((k, v) for k, v in deduped.items())]

    def _extract_definition_terms(self, definition: Optional[str], max_terms: int = 8) -> List[str]:
        text = str(definition or "").strip()
        if not text:
            return []
        chunks = re.split(r"[\n;•]+", text)
        collected: List[str] = []
        for chunk in chunks:
            chunk = re.sub(r"\s+", " ", chunk).strip(" .,:-\t")
            if not chunk:
                continue
            if len(chunk.split()) > 16:
                chunk = " ".join(chunk.split()[:16])
            if len(chunk) >= 6:
                collected.append(chunk)
            if len(collected) >= max_terms:
                break
        return collected

    def _build_weighted_lexical_terms(self, metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None) -> List[Tuple[str, float]]:
        weighted_terms: List[Tuple[str, float]] = []

        metric_name = str(getattr(metric, "metric_name", "") or "").strip()
        metric_code = str(getattr(metric, "metric_code", "") or "").strip()
        topic = str(getattr(metric, "sasb_topic", "") or "").strip()
        definition = str(getattr(metric, "definition", "") or getattr(metric, "description", "") or "").strip()

        if metric_name:
            weighted_terms.append((metric_name, 1.00))
        if metric_code:
            weighted_terms.append((metric_code, 0.92))
        if topic:
            weighted_terms.append((topic, 0.62))

        for term in self._extract_definition_terms(definition):
            weighted_terms.append((term, 0.48))

        for term in (getattr(metric, "keywords", None) or []):
            weighted_terms.append((term, 0.36))

        if semantic_expansion is not None:
            semantic_description = str(getattr(semantic_expansion, "semantic_description", "") or "").strip()
            if semantic_description:
                weighted_terms.append((semantic_description, 0.58))
            for term in (getattr(semantic_expansion, "expanded_keywords", None) or []):
                weighted_terms.append((term, 0.42))

        return self._dedupe_terms(weighted_terms)
    
    def search_keywords_in_text(self, text: str, keywords: List[str], 
                              case_sensitive: bool = False) -> List[Tuple[str, List[int]]]:
        """
        Search for keywords in text
        
        Args:
            text: Text to search in
            keywords: List of keywords
            case_sensitive: Whether to be case sensitive
            
        Returns:
            List[Tuple[str, List[int]]]: Keywords and their occurrence positions
        """
        results = []
        
        for keyword in keywords:
            # Build regular expression
            pattern = re.escape(keyword)
            flags = 0 if case_sensitive else re.IGNORECASE
            
            # Find all matching positions
            matches = [(m.start(), m.end()) for m in re.finditer(pattern, text, flags)]
            
            if matches:
                results.append((keyword, matches))
        
        return results
    
    def search_in_report(self, report_content: ReportContent, metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None) -> List[RetrievalResult]:
        """
        Search for metric-related content in report
        
        Args:
            report_content: Report content
            metric: ESG metric
            
        Returns:
            List[RetrievalResult]: Retrieval results
        """
        results = []
        
        weighted_terms = self._build_weighted_lexical_terms(metric, semantic_expansion)
        lexical_terms = [term for term, _ in weighted_terms]
        weight_map = {term: weight for term, weight in weighted_terms}
        anchor_terms = _extract_metric_anchor_terms(metric, semantic_expansion)

        for segment in report_content.document_content.segments:
            keyword_matches = self.search_keywords_in_text(
                segment.content,
                lexical_terms
            )
            
            if keyword_matches:
                matched_keywords = [kw for kw, _ in keyword_matches]
                total_matches = sum(len(positions) for _, positions in keyword_matches)
                score = self._calculate_keyword_score(
                    segment=segment,
                    metric=metric,
                    matched_keywords=matched_keywords,
                    total_matches=total_matches,
                    weight_map=weight_map,
                    anchor_terms=anchor_terms,
                )
                if score <= 0.0:
                    continue

                result = RetrievalResult(
                    segment_id=segment.segment_id,
                    content=segment.content,
                    page_number=segment.page_number,
                    score=score,
                    retrieval_type="keyword",
                    matched_keywords=matched_keywords,
                    metric_id=metric.metric_id
                )
                results.append(result)
        
        results.sort(key=lambda x: x.score, reverse=True)
        
        observed_matches = len(results)
        logger.info(f"Keyword retrieval for metric {metric.metric_id} found {observed_matches} results")
        return results[:_internal_pool_size(self.config, metric, observed_matches=observed_matches, channel="keyword")]

    def _calculate_keyword_score(self, segment, metric: ESGMetric, matched_keywords: List[str], total_matches: int, weight_map: Optional[Dict[str, float]] = None, anchor_terms: Optional[List[str]] = None) -> float:
        """结构化证据信号加权，优先让 table_cell / table_row 浮到前面。"""
        weight_map = weight_map or {}
        total_weight = max(0.0001, sum(float(v or 0.0) for v in weight_map.values()))
        matched_weight = sum(float(weight_map.get(term, 0.0)) for term in set(matched_keywords))
        unique_match_score = matched_weight / total_weight
        density_bonus = min(0.18, 0.02 * max(0, total_matches - len(set(matched_keywords))))
        structure_bonus = self._segment_structure_bonus(segment, expected_unit=getattr(metric, "unit", None), prefer_narrative=not _is_quantitative_metric(metric))
        relevance_adjustment = _qualitative_relevance_adjustment(metric, getattr(segment, "content", "") or "", anchor_terms or [], getattr(segment, "segment_type", ""))
        score = unique_match_score + density_bonus + structure_bonus + relevance_adjustment
        return float(max(0.0, min(1.0, score)))

    def _segment_structure_bonus(self, segment, expected_unit: Optional[str] = None, prefer_narrative: bool = False) -> float:
        return _segment_structure_bonus(segment, expected_unit=expected_unit, prefer_narrative=prefer_narrative)



class SemanticRetriever:
    """Semantic retriever"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize semantic retriever
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.embedding_model = None
        self.reranker = None
        self.reranker_top_k = max(1, int(os.getenv("LOCAL_RERANKER_TOP_K", "46") or "46"))
        self._reranker_lock = threading.Lock()
        self._init_embedding_model()
        self._init_reranker()

    def _init_embedding_model(self):
        """Initialize embedding model"""
        try:
            # Respect Docker/config device exactly; do not silently move embedding to CPU.
            requested_device = os.getenv("LOCAL_EMBEDDINGS_DEVICE") or str(getattr(self.config, "device", "cuda") or "cuda")
            device = torch.device(requested_device)
            logger.info(f"Loading embedding model: {self.config.embedding_model}")
            self.embedding_model = get_shared_embedding_model(
                self.config.embedding_model,
                device=str(device),
                hf_home=os.getenv("HF_HOME", "/root/.cache/huggingface"),
                explicit_local_path=get_configured_embedding_local_path(),
                trust_remote_code=True,
            )
            logger.info(f"Embedding model loaded successfully, device: {device}")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {str(e)}")
            raise ContentEmbeddingError(f"Failed to load embedding model: {str(e)}")
    
    def _init_reranker(self):
        """Initialize reranker model"""
        try:
            rerank_device = os.getenv("LOCAL_RERANKER_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")
            rerank_model = get_configured_rerank_model_name()
            rerank_use_fp16 = str(os.getenv("LOCAL_RERANKER_USE_FP16", "0") or "0").strip().lower() in ("1", "true", "yes", "y", "on")
            settings = HippoRAGSettings(
                rerank_model_name_or_path=rerank_model,
                rerank_device=rerank_device,
                rerank_use_fp16=rerank_use_fp16,
            )
            self.reranker_top_k = max(1, int(getattr(settings, "rerank_top_k", self.reranker_top_k) or self.reranker_top_k))
            self.reranker = get_reranker(settings)
            if self.reranker is not None:
                logger.info(f"Reranker model loaded successfully: {rerank_model}")
            else:
                logger.warning("Reranker not available, will use basic cosine similarity")
        except Exception as e:
            logger.warning(f"Failed to load reranker model, fallback to cosine similarity: {str(e)}")
            self.reranker = None

    def _segment_structure_bonus(self, segment, prefer_narrative: bool = False) -> float:
        return _segment_structure_bonus(segment, prefer_narrative=prefer_narrative)

    def _build_semantic_query(self, metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None) -> str:
        parts: List[str] = []

        metric_name = str(getattr(metric, "metric_name", "") or "").strip()
        metric_code = str(getattr(metric, "metric_code", "") or "").strip()
        topic = str(getattr(metric, "sasb_topic", "") or "").strip()
        definition = str(getattr(metric, "definition", "") or getattr(metric, "description", "") or "").strip()

        if metric_name:
            parts.append(f"Metric Name: {metric_name}")
        if metric_code:
            parts.append(f"Metric Code: {metric_code}")
        if topic:
            parts.append(f"Topic: {topic}")
        if definition:
            parts.append(f"Definition: {definition}")

        if semantic_expansion is not None:
            semantic_description = str(getattr(semantic_expansion, "semantic_description", "") or "").strip()
            if semantic_description:
                parts.append(f"Semantic Description: {semantic_description}")
            expanded_keywords = [str(x).strip() for x in (getattr(semantic_expansion, "expanded_keywords", None) or []) if str(x).strip()]
            if expanded_keywords:
                parts.append("Semantic Expansion: " + "; ".join(expanded_keywords[:20]))

        return "\n".join(part for part in parts if part).strip()

    def _build_rerank_instruction(self, metric: ESGMetric, semantic_expansion: Optional[SemanticExpansion] = None) -> str:
        metric_name = str(getattr(metric, "metric_name", "") or "").strip()
        metric_code = str(getattr(metric, "metric_code", "") or "").strip()
        topic = str(getattr(metric, "sasb_topic", "") or "").strip()

        focus_terms: List[str] = []
        if metric_name:
            focus_terms.append(f"metric '{metric_name}'")
        if metric_code:
            focus_terms.append(f"code '{metric_code}'")
        if topic:
            focus_terms.append(f"topic '{topic}'")

        if semantic_expansion is not None:
            expanded_keywords = [str(x).strip() for x in (getattr(semantic_expansion, "expanded_keywords", None) or []) if str(x).strip()]
            focus_terms.extend([f"keyword '{kw}'" for kw in expanded_keywords[:8]])

        focus_clause = ", ".join(focus_terms) if focus_terms else "the target ESG metric"
        return (
            "ESG report retrieval for evidence ranking. Select passages from the same report that most directly support metric extraction and disclosure assessment. "
            f"Prioritize passages explicitly matching {focus_clause}. "
            "Prefer evidence that can directly support disclosed, partially disclosed, or not disclosed judgment for the metric, including direct current-report evidence, missing-component evidence, scope or boundary limitations, or explicit absence of disclosure. "
            "Prefer passages that contain the exact metric evidence or direct narrative evidence for the metric over broader topic discussion. "
            "Do not prioritize passages only because they mention the same unit or time period. "
            "Deprioritize broad topic discussion, generic commitments, aspirations, future targets, boilerplate, and passages that are only topically related but cannot directly support metric extraction or disclosure judgment."
        )

    def search_by_semantic(self, report_content: ReportContent, 
                          metric: ESGMetric,
                          semantic_expansion: Optional[SemanticExpansion] = None) -> List[RetrievalResult]:
        """
        Search by semantic similarity
        
        Args:
            report_content: Report content
            semantic_expansion: Semantic expansion
            
        Returns:
            List[RetrievalResult]: Retrieval results
        """
        try:
            query_text = self._build_semantic_query(metric, semantic_expansion)
            if not query_text:
                raise ValueError("No semantic query text available")
            anchor_terms = _extract_metric_anchor_terms(metric, semantic_expansion)

            query_embedding = np.array(
                encode_query_texts(self.embedding_model, [query_text], model_name_or_path=self.config.embedding_model, normalize_embeddings=True)
            ).reshape(1, -1)
            target_window = _target_window_size(self.config, metric, observed_matches=0)
            pool_size = _internal_pool_size(self.config, metric, observed_matches=0, channel="semantic")
            preselect_limit = min(pool_size, max(1, int(getattr(self, "reranker_top_k", pool_size) or pool_size))) if self.reranker is not None else pool_size
            
            # Get report segments' embeddings once per report object and reuse the
            # same matrix across metrics. This avoids rebuilding a large numpy array
            # for every metric without moving any passage vectors back onto GPU.
            embedding_cache = getattr(report_content, "_semantic_retrieval_embedding_cache", None)
            if embedding_cache is None:
                segment_embeddings = []
                segments = []
                segment_lookup = {
                    getattr(seg, "segment_id", None): seg
                    for seg in report_content.document_content.segments
                    if getattr(seg, "segment_id", None)
                }

                for segment_emb in report_content.embeddings:
                    seg = segment_lookup.get(segment_emb.segment_id)
                    if seg is None:
                        continue
                    segment_embeddings.append(segment_emb.embedding)
                    segments.append(seg)

                if not segment_embeddings:
                    logger.warning("No embedding vectors found in report")
                    return []

                segment_embeddings = np.asarray(segment_embeddings, dtype=np.float32)
                embedding_cache = (segments, segment_embeddings)
                try:
                    setattr(report_content, "_semantic_retrieval_embedding_cache", embedding_cache)
                except Exception:
                    pass
            else:
                segments, segment_embeddings = embedding_cache

            similarities = cosine_similarity(query_embedding, segment_embeddings)[0]
            relaxed_threshold = max(0.08, float(getattr(self.config, "similarity_threshold", 0.2) or 0.2) * (0.65 if _is_quantitative_metric(metric) else 0.8))

            def _boost_semantic_score(segment, similarity: float) -> float:
                return _clamp_score(
                    float(similarity)
                    + _segment_structure_bonus(segment, expected_unit=getattr(metric, "unit", None), prefer_narrative=not _is_quantitative_metric(metric))
                    + _qualitative_relevance_adjustment(metric, getattr(segment, "content", "") or "", anchor_terms, getattr(segment, "segment_type", ""))
                    + _metric_evidence_quality_adjustment(metric, segment, anchor_terms)
                )

            # Use reranker if available, otherwise fallback to cosine similarity
            if self.reranker is not None:
                pre_candidates = []
                for segment, similarity in zip(segments, similarities):
                    boosted = _boost_semantic_score(segment, similarity)
                    if boosted >= relaxed_threshold:
                        pre_candidates.append((segment, boosted))
                if not pre_candidates:
                    pre_candidates = [
                        (segment, _boost_semantic_score(segment, similarity))
                        for segment, similarity in zip(segments, similarities)
                    ]
                pre_candidates.sort(key=lambda x: x[1], reverse=True)
                pre_candidates = pre_candidates[:preselect_limit]

                rerank_instruction = self._build_rerank_instruction(metric, semantic_expansion)
                rerank_scores = None

                can_reuse_embeddings = False
                if hasattr(self.reranker, "compute_score_from_embeddings"):
                    try:
                        can_reuse_embeddings = bool(self.reranker.can_reuse_document_embeddings(self.config.embedding_model))
                    except Exception:
                        can_reuse_embeddings = False

                if can_reuse_embeddings:
                    embedding_by_segment_id = {
                        getattr(segment, "segment_id", ""): segment_embeddings[idx]
                        for idx, segment in enumerate(segments)
                    }
                    candidate_embeddings = [
                        embedding_by_segment_id.get(getattr(segment, "segment_id", ""))
                        for segment, _ in pre_candidates
                    ]
                    if all(embedding is not None for embedding in candidate_embeddings):
                        with self._reranker_lock:
                            try:
                                rerank_scores = self.reranker.compute_score_from_embeddings(
                                    query_text,
                                    candidate_embeddings,
                                    normalize=True,
                                    instruction=rerank_instruction,
                                )
                            except TypeError:
                                rerank_scores = self.reranker.compute_score_from_embeddings(query_text, candidate_embeddings, normalize=True)

                if rerank_scores is None:
                    query_doc_pairs = [[query_text, segment.content] for segment, _ in pre_candidates]
                    with self._reranker_lock:
                        try:
                            rerank_scores = self.reranker.compute_score(query_doc_pairs, normalize=True, instruction=rerank_instruction)
                        except TypeError:
                            rerank_scores = self.reranker.compute_score(query_doc_pairs, normalize=True)
                if not isinstance(rerank_scores, list):
                    rerank_scores = [rerank_scores]

                results = []
                for (segment, base_score), score in zip(pre_candidates, rerank_scores):
                    rerank_score = _clamp_score(float(score))
                    final_score = _clamp_score((base_score * 0.35) + (rerank_score * 0.65))
                    if final_score >= relaxed_threshold or len(results) < target_window:
                        results.append(RetrievalResult(
                            segment_id=segment.segment_id,
                            content=segment.content,
                            page_number=segment.page_number,
                            score=float(final_score),
                            retrieval_type="semantic+rerank",
                            matched_keywords=[],
                            metric_id=metric.metric_id
                        ))
                logger.info("Used reranker for semantic retrieval" + (" with cached report embeddings" if can_reuse_embeddings else ""))
            else:
                results = []
                for segment, similarity in zip(segments, similarities):
                    boosted = _boost_semantic_score(segment, similarity)
                    if boosted >= relaxed_threshold:
                        results.append(RetrievalResult(
                            segment_id=segment.segment_id,
                            content=segment.content,
                            page_number=segment.page_number,
                            score=float(boosted),
                            retrieval_type="semantic",
                            matched_keywords=[],
                            metric_id=metric.metric_id
                        ))
                
                logger.info(f"Used cosine similarity fallback for semantic retrieval")
            
            # Sort by score
            results.sort(key=lambda x: x.score, reverse=True)
            
            observed_matches = len(results)
            final_window = _target_window_size(self.config, metric, observed_matches=observed_matches)
            metric_log_id = getattr(metric, "metric_id", None) or "unknown"
            logger.info(f"Semantic retrieval for metric {metric_log_id} found {observed_matches} results")
            return results[:final_window]
            
        except Exception as e:
            logger.error(f"Semantic retrieval failed: {str(e)}")
            raise ESGEncodingError(f"Semantic retrieval failed: {str(e)}")


class DualChannelRetriever:
    """Dual-channel retriever"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize dual-channel retriever
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.keyword_retriever = KeywordRetriever(config)
        self.semantic_retriever = SemanticRetriever(config)
    
    def retrieve_for_metric(self, report_content: ReportContent, 
                          metric: ESGMetric, 
                          semantic_expansion: Optional[SemanticExpansion] = None) -> MetricRetrievalResult:
        """
        Perform dual-channel retrieval for a single metric
        
        Args:
            report_content: Report content
            metric: ESG metric
            semantic_expansion: Semantic expansion (optional)
            
        Returns:
            MetricRetrievalResult: Metric retrieval result
        """
        try:
            logger.info(f"Starting dual-channel retrieval for metric {metric.metric_name}")
            
            # Keyword retrieval
            keyword_results = self.keyword_retriever.search_in_report(report_content, metric, semantic_expansion)
            
            # Semantic retrieval
            semantic_results = []
            if semantic_expansion or getattr(metric, "definition", None) or getattr(metric, "sasb_topic", None) or getattr(metric, "metric_code", None):
                semantic_results = self.semantic_retriever.search_by_semantic(
                    report_content, metric, semantic_expansion
                )
            
            # Combine results
            combined_results = self._combine_results(keyword_results, semantic_results, metric, report_content)
            
            result = MetricRetrievalResult(
                metric_id=metric.metric_id,
                metric_name=metric.metric_name,
                metric_code=metric.metric_code,
                keyword_results=keyword_results,
                semantic_results=semantic_results,
                combined_results=combined_results,
                total_matches=len(combined_results)
            )
            
            logger.info(f"Retrieval completed for metric {metric.metric_name}, found {len(combined_results)} results")
            return result
            
        except Exception as e:
            logger.error(f"Metric retrieval failed: {str(e)}")
            raise ESGEncodingError(f"Metric retrieval failed: {str(e)}")
    
    def _combine_results(self, keyword_results: List[RetrievalResult], 
                        semantic_results: List[RetrievalResult],
                        metric: Optional[ESGMetric] = None,
                        report_content: Optional[ReportContent] = None) -> List[RetrievalResult]:
        """
        Combine keyword and semantic retrieval results
        
        Args:
            keyword_results: Keyword retrieval results
            semantic_results: Semantic retrieval results
            
        Returns:
            List[RetrievalResult]: Combined results
        """
        # Use dictionary for deduplication (based on segment_id)
        combined_dict = {}
        
        # Add keyword retrieval results
        for result in keyword_results:
            combined_dict[result.segment_id] = result
        
        # Add semantic retrieval results, merge scores if already exists
        for result in semantic_results:
            if result.segment_id in combined_dict:
                # Merge scores with different weights based on retrieval type
                existing = combined_dict[result.segment_id]
                
                # Give higher weight to reranker results
                if result.retrieval_type == "semantic+rerank":
                    # Weight: 0.3 keyword + 0.7 reranker
                    combined_score = (existing.score * 0.3) + (result.score * 0.7)
                    existing.retrieval_type = "keyword+rerank"
                else:
                    # Original simple average for cosine similarity
                    combined_score = (existing.score + result.score) / 2
                    existing.retrieval_type = "keyword+semantic"
                
                existing.score = combined_score
            else:
                combined_dict[result.segment_id] = result
        
        # Convert to list and sort
        combined_results = list(combined_dict.values())
        if metric is not None and report_content is not None:
            anchors = _extract_metric_anchor_terms(metric)
            combined_results.extend(_synthesize_qualitative_clusters(report_content, metric, combined_results, anchors))
        deduped = {}
        for result in combined_results:
            current = deduped.get(result.segment_id)
            if current is None or float(result.score or 0.0) > float(current.score or 0.0):
                deduped[result.segment_id] = result
        combined_results = list(deduped.values())
        combined_results.sort(key=lambda x: x.score, reverse=True)
        if metric is not None:
            combined_results = _rebalance_qualitative_results(metric, combined_results, report_content=report_content)
        
        final_k = int(getattr(self.config, "top_k", 10) or 10)
        if metric is not None:
            final_k = _target_window_size(self.config, metric, observed_matches=len(combined_results))
        final_k = max(11, final_k)
        return combined_results[:final_k]
    
    def retrieve_for_collection(self, report_content: ReportContent, 
                              metric_collection: MetricCollection) -> List[MetricRetrievalResult]:
        """
        Perform dual-channel retrieval for metric collection
        
        Args:
            report_content: Report content
            metric_collection: Metric collection
            
        Returns:
            List[MetricRetrievalResult]: Retrieval results for all metrics
        """
        results = []
        
        # Create semantic expansion mapping
        expansions_map = {
            exp.metric_id: exp 
            for exp in metric_collection.semantic_expansions
        }
        
        for metric in metric_collection.metrics:
            logger.info(f"Retrieving metric: {metric.metric_name}")
            
            # Get corresponding semantic expansion
            semantic_expansion = expansions_map.get(metric.metric_id)
            
            # Perform retrieval
            result = self.retrieve_for_metric(
                report_content, 
                metric, 
                semantic_expansion
            )
            results.append(result)
        
        logger.info(f"Completed metric collection retrieval, processed {len(results)} metrics")
        return results
    
    def generate_retrieval_report(self, retrieval_results: List[MetricRetrievalResult]) -> str:
        """
        Generate retrieval report
        
        Args:
            retrieval_results: List of retrieval results
            
        Returns:
            str: Retrieval report (Markdown format)
        """
        report_lines = [
            "# ESG Metric Retrieval Report\n",
            f"**Generated Time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            f"**Number of Retrieved Metrics**: {len(retrieval_results)}\n",
            f"**Total Matched Segments**: {sum(r.total_matches for r in retrieval_results)}\n",
            "---\n"
        ]
        
        for result in retrieval_results:
            report_lines.extend([
                f"## {result.metric_name} ({result.metric_id})\n",
                f"**Total Matches**: {result.total_matches}\n",
                f"**Keyword Matches**: {len(result.keyword_results)}\n",
                f"**Semantic Matches**: {len(result.semantic_results)}\n",
                ""
            ])
            
            # Show top 5 best matches
            if result.combined_results:
                report_lines.append("### Best Matching Segments\n")
                for i, match in enumerate(result.combined_results[:5], 1):
                    report_lines.extend([
                        f"**{i}. Segment {match.segment_id}** (Page: {match.page_number}, Score: {match.score:.3f})\n",
                        f"*Retrieval Type: {match.retrieval_type}*\n",
                        f"*Matched Keywords: {', '.join(match.matched_keywords) if match.matched_keywords else 'None'}*\n",
                        f"```\n{match.content[:200]}...\n```\n",
                        ""
                    ])
            
            report_lines.append("---\n")
        
        return "\n".join(report_lines)


from datetime import datetime 