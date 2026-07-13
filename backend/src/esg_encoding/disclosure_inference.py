"""
Disclosure Inference Engine - Use LLM to analyze ESG metric disclosure status
"""

import json
from typing import Any, List, Dict, Optional, Tuple, Union
import re
from datetime import datetime
import openai
from loguru import logger

from .models import (
    ProcessingConfig, 
    MetricRetrievalResult,
    DisclosureStatus,
    DisclosureAnalysis,
    ComplianceAssessment,
    ReportContent,
    MetricCollection
)

def _is_claude_model(model_name: str) -> bool:
    """Return True if the model is Claude (Anthropic) so we can use json_schema response_format."""
    if not model_name:
        return False
    m = model_name.strip().lower()
    return "claude" in m or "anthropic" in m

# JSON schema for disclosure analysis (used when response_format is json_schema, e.g. Claude)
DISCLOSURE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "metric_hit": {"type": ["boolean", "null"]},
        "disclosure_status": {
            "type": "string",
            "enum": ["fully_disclosed", "partially_disclosed", "not_disclosed"],
        },
        "has_disclosure": {"type": "boolean"},
        "disclosure_quality": {"type": "string"},
        "value_status": {"type": ["string", "null"]},
        "reasoning": {"type": "string"},
        "value": {"type": ["number", "null"]},
        "raw_value": {"type": ["number", "null"]},
        "raw_unit": {"type": ["string", "null"]},
        "page": {"type": ["integer", "null"]},
        "evidence_segment_id": {"type": ["string", "null"]},
        "evidence_quote": {"type": ["string", "null"]},
        "specific_data_found": {"type": ["string", "null"]},
        "improvement_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["disclosure_status", "reasoning"],
    "additionalProperties": False,
}

# Stored in assessment JSON when no metric-specific number is disclosed / extractable.
COMPLIANCE_VALUE_NA = "n/a"

def _parse_llm_numeric_value_only(raw: object) -> Optional[Union[int, float]]:
    """
    Accept a safe numeric literal, or a short numeric phrase like
    '152,341 tCO2e', 'about 12.5 GJ', '1.4 million kWh', '12.5%'.
    Never scrape a number from a longer sentence.
    """
    if raw is None or raw is False:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")):
            return None
        return raw
    s = str(raw).strip()
    if not s or s.lower() in ("n/a", "na", "none", "null", "-", "—", "--"):
        return None
    if len(s) > 80 or "\n" in s:
        return None

    s2 = s.replace(",", "").strip()
    if s2.endswith("%"):
        s2 = s2[:-1].strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s2):
        n = float(s2)
        return int(n) if n == int(n) and abs(n) < 1e15 else n

    m = re.fullmatch(
        r"(?i)(?:about|approximately|approx\.?|around|nearly|roughly)?\s*"
        r"(-?\d[\d,]*(?:\.\d+)?)\s*"
        r"(million|billion|thousand|m|bn|k)?\s*"
        r"([a-zA-Z%/²³0-9._-]{0,20})?",
        s
    )
    if not m:
        return None
    number = m.group(1)
    multiplier = (m.group(2) or "").lower()
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if multiplier in {"thousand", "k"}:
        value *= 1_000
    elif multiplier in {"million", "m"}:
        value *= 1_000_000
    elif multiplier in {"billion", "bn"}:
        value *= 1_000_000_000
    if value == int(value) and abs(value) < 1e15:
        return int(value)
    return value

def _normalize_unit_text(unit: Optional[str]) -> str:
    raw = str(unit or "").strip()
    if not raw:
        return ""
    raw = raw.replace("CO₂", "CO2").replace("co₂", "co2").replace("m³", "m3").replace("％", "%")
    raw = raw.replace("﹪", "%").replace("·", "/").replace("／", "/")
    raw = raw.replace("–", "-").replace("—", "-")
    raw = re.sub(r"[\[\]\{\}]", " ", raw)
    raw = raw.replace("\u00a0", " ")
    raw = re.sub(r"\s+", " ", raw).strip()

    phrase_replacements = [
        (r"(?i)\bmetric\s+tons?\s+of\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\bmetric\s+tons?\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\btons?\s+of\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\btonnes?\s+of\s+co2(?:e|\s*equivalent)\b", "tCO2e"),
        (r"(?i)\bkilograms?\s+of\s+co2(?:e|\s*equivalent)\b", "kgCO2e"),
        (r"(?i)\bkilotons?\s+of\s+co2(?:e|\s*equivalent)\b", "ktCO2e"),
        (r"(?i)\bmegawatt(?:-|\s)?hours?\b", "MWh"),
        (r"(?i)\bkilowatt(?:-|\s)?hours?\b", "kWh"),
        (r"(?i)\bgigawatt(?:-|\s)?hours?\b", "GWh"),
        (r"(?i)\bterawatt(?:-|\s)?hours?\b", "TWh"),
        (r"(?i)\bgigajoules?\b", "GJ"),
        (r"(?i)\bterajoules?\b", "TJ"),
        (r"(?i)\bpetajoules?\b", "PJ"),
        (r"(?i)\bmillion\s+british\s+thermal\s+units?\b", "MMBtu"),
        (r"(?i)\bcubic\s+meters?\b", "m3"),
        (r"(?i)\bcubic\s+metres?\b", "m3"),
        (r"(?i)\bkilolit(?:er|re)s?\b", "kL"),
        (r"(?i)\blit(?:er|re)s?\b", "L"),
        (r"(?i)\bmillilit(?:er|re)s?\b", "mL"),
        (r"(?i)\bmetric\s+tons?\b", "t"),
        (r"(?i)\btonnes?\b", "t"),
        (r"(?i)\bkilograms?\b", "kg"),
        (r"(?i)\bgrams?\b", "g"),
        (r"(?i)\bpercent(?:age)?\b", "%"),
    ]
    for pat, repl in phrase_replacements:
        raw = re.sub(pat, repl, raw)

    raw = re.sub(r"(?i)\bper\b", "/", raw)
    raw = re.sub(r"\s*/\s*", "/", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw

def _extract_unit_hint(text: Optional[str]) -> str:
    value = _normalize_unit_text(text)
    if not value:
        return ""
    patterns = [
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(tCO2e|kgCO2e|ktCO2e|MtCO2e|co2e)\b",
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(MWh|kWh|GWh|TWh|GJ|TJ|PJ|MMBtu|therms?|toe|ktoe)\b",
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(m3|kL|L|mL)\b",
        r"(?i)\b(?:thousand|million|billion|k|m|bn)?\s*(t|kg|g)\b",
        r"(?i)(?:%|percent)",
    ]
    for pat in patterns:
        m = re.search(pat, value)
        if m:
            return m.group(0).strip()
    return ""

def _clean_converted_number(value: float) -> Union[int, float]:
    if value == int(value) and abs(value) < 1e15:
        return int(value)
    return round(value, 9)

def _coalesce_metric_hit(llm_response: dict) -> bool:
    if "metric_hit" in llm_response and llm_response.get("metric_hit") is not None:
        return bool(llm_response.get("metric_hit"))
    return bool(llm_response.get("has_disclosure", False))

def _normalize_value_status(raw: object) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "exact": "exact",
        "converted": "converted",
        "approximate": "approximate",
        "approx": "approximate",
        "raw": "raw_unit_only",
        "raw_unit_only": "raw_unit_only",
        "unit_mismatch": "unit_mismatch",
        "ambiguous": "ambiguous",
        "none": "none",
        "null": "none",
    }
    return aliases.get(value, value or "none")

def _finalize_compliance_value_field(
    found_numeric: Optional[Union[int, float]],
) -> Union[int, float, str]:
    if isinstance(found_numeric, (int, float)) and not isinstance(found_numeric, bool):
        return found_numeric
    return COMPLIANCE_VALUE_NA

def _extract_unit_multiplier(unit: Optional[str]) -> float:
    raw = _normalize_unit_text(unit).lower()
    if not raw:
        return 1.0
    if re.search(r"\b(billion|bn)\b", raw):
        return 1_000_000_000.0
    if re.search(r"\bmillion\b", raw):
        return 1_000_000.0
    if re.search(r"\bthousand\b", raw):
        return 1_000.0
    return 1.0

def _normalize_denominator_atom(unit: Optional[str]) -> str:
    raw = _normalize_unit_text(unit).lower()
    if not raw:
        return ""
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    alias_map = {
        "employees": "employee",
        "employee": "employee",
        "fte": "fte",
        "ftes": "fte",
        "revenue": "revenue",
        "sales": "revenue",
        "usd": "usd",
        "product": "product",
        "products": "product",
        "unit": "unit",
        "units": "unit",
    }
    return alias_map.get(raw, raw)

def _normalize_unit_atom(unit: Optional[str]) -> str:
    raw = _normalize_unit_text(unit)
    if not raw:
        return ""
    raw = re.sub(r"(?i)\b(?:thousand|million|billion|bn)\b", " ", raw)
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"(?i)(mwh|kwh|gwh|twh|gj|tj|pj|mmbtu|therm|therms|toe|ktoe|tco2e|kgco2e|ktco2e|mtco2e|m3|kl|l|ml|t|kg|g)(?:\(?\d+\)?|[ivx]+)?$", r"\1", raw)
    lower = raw.lower()
    alias_map = {
        "%": "%", "percent": "%", "percentage": "%", "pct": "%",
        "ratio": "ratio", "fraction": "ratio", "decimal": "ratio",
        "tco2e": "tco2e", "co2e": "tco2e", "kgco2e": "kgco2e", "ktco2e": "ktco2e", "mtco2e": "mtco2e",
        "mwh": "mwh", "kwh": "kwh", "gwh": "gwh", "twh": "twh", "gj": "gj", "tj": "tj", "pj": "pj", "mmbtu": "mmbtu", "therm": "therm", "therms": "therm", "toe": "toe", "ktoe": "ktoe",
        "m3": "m3", "kl": "kl", "l": "l", "ml": "ml",
        "t": "t", "ton": "t", "tons": "t", "tonne": "t", "tonnes": "t", "kg": "kg", "g": "g",
    }
    return alias_map.get(lower, lower)

def _split_unit_expression(unit: Optional[str]) -> Tuple[str, str, float]:
    raw = _normalize_unit_text(unit)
    if not raw:
        return "", "", 1.0
    multiplier = _extract_unit_multiplier(raw)
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        return _normalize_unit_atom(numerator), _normalize_denominator_atom(denominator), multiplier
    return _normalize_unit_atom(raw), "", multiplier

def _unit_profile(unit: Optional[str]) -> Optional[Tuple[str, float]]:
    token = _normalize_unit_atom(unit)
    if not token:
        return None
    maps = {
        "ratio_percent": {"%": 1.0, "ratio": 100.0},
        "energy_gj": {"gj": 1.0, "tj": 1000.0, "pj": 1_000_000.0, "kwh": 0.0036, "mwh": 3.6, "gwh": 3600.0, "twh": 3_600_000.0, "mmbtu": 1.055056, "therm": 0.1055056, "toe": 41.868, "ktoe": 41_868.0},
        "volume_m3": {"m3": 1.0, "kl": 1.0, "l": 0.001, "ml": 0.000001},
        "emissions_tco2e": {"tco2e": 1.0, "kgco2e": 0.001, "ktco2e": 1000.0, "mtco2e": 1_000_000.0},
        "mass_t": {"t": 1.0, "kg": 0.001, "g": 0.000001},
    }
    for family, mapping in maps.items():
        if token in mapping:
            return family, mapping[token]
    return None

def _convert_numeric_value_between_units(value: Optional[Union[int, float]], from_unit: Optional[str], to_unit: Optional[str]) -> Optional[Union[int, float]]:
    if value is None:
        return None
    from_num, from_den, from_scale = _split_unit_expression(from_unit)
    to_num, to_den, to_scale = _split_unit_expression(to_unit)
    if not from_num or not to_num:
        return None
    if (from_den or to_den) and from_den != to_den:
        return None
    from_profile = _unit_profile(from_num)
    to_profile = _unit_profile(to_num)
    if from_profile is None or to_profile is None or from_profile[0] != to_profile[0]:
        return None
    converted = float(value) * float(from_scale) * float(from_profile[1]) / (float(to_scale) * float(to_profile[1]))
    return _clean_converted_number(converted)

class DisclosureInferenceEngine:
    """Disclosure Inference Engine - Call LLM to analyze disclosure status"""
    
    def __init__(self, config: ProcessingConfig):
        """
        Initialize disclosure inference engine
        
        Args:
            config: Processing configuration
        """
        self.config = config
        self.llm_client = self._init_llm_client()
        
    def _init_llm_client(self):
        """Initialize LLM client"""
        if not self.config.llm_api_key:
            raise ValueError("LLM API key is required for disclosure inference. Please configure LLM_API_KEY in your .env file.")
        
        client = openai.OpenAI(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url if self.config.llm_base_url else "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        logger.info("LLM client initialized successfully for disclosure inference")
        return client
    
    def analyze_compliance(
        self,
        retrieval_results: List[MetricRetrievalResult],
        report_content: ReportContent,
        report_file_path: str = "",
        all_metrics: Optional[MetricCollection] = None,
        framework: Optional[str] = None,
        industry: Optional[str] = None,
        semi_industry: Optional[str] = None
    ) -> ComplianceAssessment:
        """
        Analyze compliance status of all metrics

        Args:
            retrieval_results: Dual-channel retrieval results
            report_content: Report content
            report_file_path: Report file path
            all_metrics: All metrics to analyze (if provided, will analyze all metrics)
            framework: Framework used (e.g., SASB, GRI)
            industry: Industry sector
            semi_industry: Sub-industry sector

        Returns:
            ComplianceAssessment: Compliance assessment report
        """
        
        # If all metrics are provided, analyze all metrics; otherwise only analyze retrieved metrics
        if all_metrics:
            logger.info(f"Starting compliance analysis for all {len(all_metrics.metrics)} metrics in collection")
            
            # Create retrieval results mapping
            retrieval_map = {result.metric_id: result for result in retrieval_results}
            
            metric_analyses = []
            for i, metric in enumerate(all_metrics.metrics):
                logger.info(f"Analyzing metric {i+1}/{len(all_metrics.metrics)}: {metric.metric_name}")
                
                # If retrieval results exist and matching content found, use retrieval analysis; otherwise mark as not disclosed
                #print("======== DEBUG METRIC STRUCTURE ========")
                #print(metric)
                if metric.metric_id in retrieval_map:
                    retrieval_result = retrieval_map[metric.metric_id]
                    # Only perform LLM analysis when matching content is actually found
                    if retrieval_result.total_matches > 0:
                        try:
                            analysis = self._analyze_single_metric(retrieval_result, report_content, metric)
                        except Exception as e:
                            logger.warning(f"Metric analysis failed for {metric.metric_name}, using fallback: {e}")
                            analysis = DisclosureAnalysis(
                                metric_id=metric.metric_id,
                                metric_name=metric.metric_name,
                                metric_code=metric.metric_code,
                                disclosure_status=DisclosureStatus.NOT_DISCLOSED,
                                reasoning=f"Analysis failed: {e}",
                                evidence_segments=[],
                                improvement_suggestions=[],
                                category=getattr(metric, 'sasb_category', ''),
                                topic=(getattr(metric, 'sasb_topic', None) or ''),
                                unit=getattr(metric, 'unit', ''),
                                type=getattr(metric, 'sasb_type', ''),
                                value=COMPLIANCE_VALUE_NA,
                                page=None
                            )
                    else:
                        # Retrieval result exists but no matching content, directly mark as not disclosed
                        analysis = DisclosureAnalysis(
                            metric_id=metric.metric_id,
                            metric_name=metric.metric_name,
                            metric_code=metric.metric_code,
                            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
                            reasoning="No relevant metric content found",
                            evidence_segments=[],
                            improvement_suggestions=[],
                            # SASB display fields
                            category=getattr(metric, 'sasb_category', ''),
                            topic=(getattr(metric, 'sasb_topic', None) or ''),
                            unit=getattr(metric, 'unit', ''),
                            type=getattr(metric, 'sasb_type', ''),
                            value=COMPLIANCE_VALUE_NA,
                            page=None
                        )
                else:
                    # No relevant content retrieved, directly mark as not disclosed
                    analysis = DisclosureAnalysis(
                        metric_id=metric.metric_id,
                        metric_name=metric.metric_name,
                        metric_code=metric.metric_code,
                        disclosure_status=DisclosureStatus.NOT_DISCLOSED,
                        reasoning="No relevant metric content found",
                        evidence_segments=[],
                        improvement_suggestions=[],
                        # SASB display fields
                        category=getattr(metric, 'sasb_category', ''),
                        topic=(getattr(metric, 'sasb_topic', None) or ''),
                        unit=getattr(metric, 'unit', ''),
                        type=getattr(metric, 'sasb_type', ''),
                        value=COMPLIANCE_VALUE_NA,
                        page=None
                    )
                metric_analyses.append(analysis)
                
        else:
            logger.info(f"Starting compliance analysis for {len(retrieval_results)} retrieved metrics")
            
            # Analyze each retrieved metric
            metric_analyses = []
            for i, retrieval_result in enumerate(retrieval_results):
                logger.info(f"Analyzing metric {i+1}/{len(retrieval_results)}: {retrieval_result.metric_name}")
                analysis = self._analyze_single_metric(retrieval_result, report_content)
                metric_analyses.append(analysis)
        
        # Count quantities for each status
        disclosure_summary = {
            DisclosureStatus.FULLY_DISCLOSED: 0,
            DisclosureStatus.PARTIALLY_DISCLOSED: 0,
            DisclosureStatus.NOT_DISCLOSED: 0
        }
        
        for analysis in metric_analyses:
            disclosure_summary[analysis.disclosure_status] += 1
        
        # Calculate overall compliance score
        total_metrics = len(metric_analyses)
        if total_metrics > 0:
            fully_disclosed = disclosure_summary[DisclosureStatus.FULLY_DISCLOSED]
            partially_disclosed = disclosure_summary[DisclosureStatus.PARTIALLY_DISCLOSED]
            overall_score = (fully_disclosed * 1.0 + partially_disclosed * 0.5) / total_metrics
        else:
            overall_score = 0.0
        
        # Create assessment report
        assessment = ComplianceAssessment(
            report_id=report_content.document_id,
            total_metrics_analyzed=total_metrics,
            disclosure_summary=disclosure_summary,
            metric_analyses=metric_analyses,
            overall_compliance_score=overall_score,
            report_file_path=report_file_path,
            framework=framework,
            industry=industry,
            semi_industry=semi_industry
        )
        
        logger.info(f"Compliance analysis completed. Overall score: {overall_score:.2%}")
        logger.info(f"Disclosure summary - Fully: {disclosure_summary[DisclosureStatus.FULLY_DISCLOSED]}, "
                   f"Partially: {disclosure_summary[DisclosureStatus.PARTIALLY_DISCLOSED]}, "
                   f"Not disclosed: {disclosure_summary[DisclosureStatus.NOT_DISCLOSED]}")
        
        return assessment
    
    def _extract_year_from_text(self, text: Optional[str]) -> Optional[int]:
        """Extract the first 4-digit reporting year from free text."""
        value = str(text or "").strip()
        if not value:
            return None
        match = re.search(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)", value)
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _extract_json_from_llm_response(self, content: str) -> Optional[dict]:
        """Extract a JSON object from LLM response text. Returns None if no valid JSON found."""
        if not content or not content.strip():
            return None
        content = content.strip()
        # 1) Direct parse
        try:
            return json.loads(content)
        except Exception:
            pass
        # 2) Strip markdown code fences (```json ... ``` or ``` ... ```)
        for pattern in [
            r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
            r"```\s*(\{[\s\S]*?\})\s*```",
        ]:
            m = re.search(pattern, content, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1).strip())
                except Exception:
                    pass
        # 3) Find first { and extract balanced-brace JSON
        start = content.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(content)):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(content[start : i + 1])
                        except Exception:
                            break
        # 4) Greedy first {...} (original fallback)
        mobj = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
        if mobj:
            try:
                return json.loads(mobj.group(0))
            except Exception:
                pass
        return None
    
    def _fallback_disclosure_analysis(
        self,
        retrieval_result: MetricRetrievalResult,
        metric: Optional['ESGMetric'],
        evidence_segment_ids: List[str],
        reasoning: str,
    ) -> DisclosureAnalysis:
        """Return a NOT_DISCLOSED analysis when LLM fails or returns invalid output."""
        return DisclosureAnalysis(
            metric_id=retrieval_result.metric_id,
            metric_name=retrieval_result.metric_name,
            metric_code=retrieval_result.metric_code,
            disclosure_status=DisclosureStatus.NOT_DISCLOSED,
            reasoning=reasoning,
            evidence_segments=evidence_segment_ids or [],
            improvement_suggestions=[],
            category=getattr(metric, "sasb_category", "") if metric else "",
            topic=(getattr(metric, "sasb_topic", None) or "") if metric else "",
            unit=(getattr(metric, "unit", None) or "") if metric else "",
            type=getattr(metric, "sasb_type", "") if metric else "",
            definition=(getattr(metric, "definition", None) or "") if metric else "",
            value=COMPLIANCE_VALUE_NA,
            page=None,
            context=None,
        )
    

    def _metric_code_candidates(
        self,
        retrieval_result: MetricRetrievalResult,
        metric: Optional['ESGMetric'] = None,
    ) -> List[str]:
        """Return robust current-metric code candidates for deterministic evidence checks."""
        raw_candidates: List[object] = [
            getattr(metric, "metric_code", None) if metric is not None else None,
            getattr(metric, "metric_id", None) if metric is not None else None,
            getattr(retrieval_result, "metric_code", None),
            getattr(retrieval_result, "metric_id", None),
        ]
        candidates: List[str] = []
        seen = set()
        for raw in raw_candidates:
            if raw is None:
                continue
            for part in re.split(r"[,;\n|]+", str(raw)):
                code = part.strip().strip("()[]{}")
                if not code:
                    continue
                # Metric codes should normally contain both letters and digits.
                # This avoids accidental matches on broad words or short ids.
                if len(code) < 3 or not re.search(r"[A-Za-z]", code) or not re.search(r"\d", code):
                    continue
                key = code.upper()
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(code)
        return candidates

    def _contains_metric_code(self, text: object, code_candidates: List[str]) -> bool:
        """True when evidence text explicitly contains one of the current metric codes."""
        evidence = str(text or "")
        if not evidence or not code_candidates:
            return False
        for code in code_candidates:
            pattern = r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])"
            if re.search(pattern, evidence, flags=re.IGNORECASE):
                return True
        return False

    def _direct_numeric_from_segment_fields(self, segment) -> Tuple[Optional[Union[int, float]], Optional[str], str]:
        """Extract a compact numeric value from structured/cell fields before prose fallback."""
        if segment is None:
            return None, None, ""

        unit = self._format_short_metadata_value(
            self._get_segment_field(segment, "unit", "cell_unit", "raw_unit"),
            120,
        ) or None

        field_names = (
            "value", "value_text", "cell_value", "raw_value", "numeric_value",
            "amount", "figure", "data", "extracted_value",
        )
        for field in field_names:
            raw_value = self._get_segment_field(segment, field)
            parsed = _parse_llm_numeric_value_only(raw_value)
            if parsed is not None:
                return parsed, unit, f"{field}={raw_value}"

        data = self._segment_structured_data_dict(segment)
        for key, raw_value in data.items():
            if str(key).lower() in {"page", "page_number", "row_index", "column_index", "year"}:
                continue
            parsed = _parse_llm_numeric_value_only(raw_value)
            if parsed is not None:
                unit = unit or self._format_short_metadata_value(
                    data.get("unit") or data.get("cell_unit") or data.get("raw_unit"),
                    120,
                ) or None
                return parsed, unit, f"structured_data.{key}={raw_value}"

        compact_sources = [
            self._get_segment_field(segment, "value_text", "cell_value", "value"),
            getattr(segment, "content", ""),
        ]
        for source in compact_sources:
            parsed = self._extract_numeric_from_cell_text(source)
            if parsed is not None:
                return parsed, unit, self._format_short_metadata_value(source, 220)

        return None, unit, ""

    def _direct_evidence_bundle_for_segment(
        self,
        report_content: ReportContent,
        segment,
        code_candidates: List[str],
        expected_unit: str = "",
    ) -> Tuple[str, Optional[Union[int, float]], Optional[str], str]:
        """Build same-row/same-segment evidence and extract a deterministic numeric value."""
        if segment is None:
            return "", None, None, ""

        row_context, row_numeric, row_unit, row_desc = self._build_table_row_aggregation_context(
            report_content,
            segment,
            max_chars=1800,
        )
        structured_hint = self._build_structured_segment_hint(segment)
        hit_text = self._truncate_segment_text(getattr(segment, "content", "") or "", 1000)
        evidence_text = "\n\n".join(x for x in [row_context, structured_hint, hit_text] if x)

        numeric_value = row_numeric
        raw_unit = row_unit
        data_desc = row_desc or ""

        # If the row-level latest-year value is not available, use the current cell/segment.
        if numeric_value is None:
            numeric_value, raw_unit, data_desc = self._direct_numeric_from_segment_fields(segment)

        # If the code is present only in the row context and the current cell has the value,
        # this still counts as code + data in the same table row.
        if not self._contains_metric_code(evidence_text, code_candidates):
            return evidence_text, None, raw_unit, data_desc

        if numeric_value is None:
            return evidence_text, None, raw_unit, data_desc

        return evidence_text, numeric_value, raw_unit, data_desc

    def _direct_code_data_disclosure_analysis(
        self,
        retrieval_result: MetricRetrievalResult,
        report_content: ReportContent,
        metric: Optional['ESGMetric'],
        segment_metadata: List[Dict],
        evidence_segment_ids: List[str],
    ) -> Optional[DisclosureAnalysis]:
        """Classify as fully_disclosed when current metric code and direct data are found together.

        This is intentionally a high-confidence deterministic shortcut placed before
        the LLM. It only triggers when the retrieved evidence contains the current
        metric code and a directly extractable numeric data value in the same
        segment/table row. Otherwise, the normal LLM assessment path is preserved.
        """
        code_candidates = self._metric_code_candidates(retrieval_result, metric)
        if not code_candidates or not segment_metadata:
            return None

        expected_unit = getattr(metric, "unit", "") if metric else ""
        best_hit: Optional[Dict[str, Any]] = None

        for meta in segment_metadata:
            segment_id = meta.get("segment_id")
            if not segment_id:
                continue
            try:
                segment = self._get_segment_by_id(report_content, segment_id)
            except Exception:
                segment = None
            if segment is None:
                continue

            evidence_text, numeric_value, raw_unit, data_desc = self._direct_evidence_bundle_for_segment(
                report_content=report_content,
                segment=segment,
                code_candidates=code_candidates,
                expected_unit=expected_unit,
            )
            if not evidence_text or not self._contains_metric_code(evidence_text, code_candidates):
                continue
            if numeric_value is None:
                continue

            converted_value = _convert_numeric_value_between_units(numeric_value, raw_unit, expected_unit)
            stored_numeric = converted_value if converted_value is not None else numeric_value
            value_status = "converted" if converted_value is not None and raw_unit and expected_unit else "exact"
            page_number = getattr(segment, "page_number", None) or meta.get("page_number")
            score = float(meta.get("score", 0) or 0)

            hit = {
                "segment_id": segment_id,
                "page": page_number,
                "value": stored_numeric,
                "raw_value": numeric_value,
                "raw_unit": raw_unit,
                "value_status": value_status,
                "context": self._format_short_metadata_value(evidence_text, 1400),
                "data_desc": data_desc or f"{numeric_value} {raw_unit or ''}".strip(),
                "score": score,
            }
            if best_hit is None or hit["score"] > best_hit.get("score", 0):
                best_hit = hit

        if best_hit is None:
            return None

        code_text = "/".join(code_candidates[:2])
        raw_unit_text = f" {best_hit['raw_unit']}" if best_hit.get("raw_unit") else ""
        reasoning = (
            f"Direct code-data extraction found the current metric code ({code_text}) "
            f"and a directly extractable metric data value ({best_hit['raw_value']}{raw_unit_text}) "
            "in the same evidence row/segment, so this metric is classified as fully_disclosed before LLM assessment."
        )

        return DisclosureAnalysis(
            metric_id=retrieval_result.metric_id,
            metric_name=retrieval_result.metric_name,
            metric_code=retrieval_result.metric_code,
            disclosure_status=DisclosureStatus.FULLY_DISCLOSED,
            reasoning=reasoning,
            evidence_segments=evidence_segment_ids or [best_hit["segment_id"]],
            improvement_suggestions=[],
            category=getattr(metric, 'sasb_category', '') if metric else '',
            topic=(getattr(metric, 'sasb_topic', None) or '') if metric else '',
            unit=getattr(metric, 'unit', '') or '' if metric else '',
            type=getattr(metric, 'sasb_type', '') if metric else '',
            definition=(getattr(metric, 'definition', None) or '') if metric else '',
            value=_finalize_compliance_value_field(best_hit.get("value")),
            context=best_hit.get("context"),
            page=best_hit.get("page"),
        )

    def _analyze_single_metric(
        self, 
        retrieval_result: MetricRetrievalResult,
        report_content: ReportContent,
        metric: Optional['ESGMetric'] = None
    ) -> DisclosureAnalysis:
        """
        Analyze disclosure status of a single metric
        
        Args:
            retrieval_result: Retrieval result for single metric
            report_content: Report content
            
        Returns:
            DisclosureAnalysis: Analysis result for this metric
        """
        # Get relevant segment content and tag information
        relevant_segments = []
        evidence_segment_ids = []
        segment_metadata = []

        # Keep the 0327 direct-LLM style as the primary analysis path, but use a dynamic evidence window in the 11-40 range.
        final_window = self._get_final_window_size(metric, observed_matches=int(getattr(retrieval_result, "total_matches", 0) or 0))
        for result in retrieval_result.combined_results[:final_window]:
            # Prefer segment from report_content; fallback to retrieval payload (more robust across caches)
            segment = None
            try:
                segment = self._get_segment_by_id(report_content, result.segment_id)
            except Exception:
                segment = None

            page_number = getattr(segment, "page_number", None) if segment is not None else getattr(result, "page_number", None)
            content = self._build_augmented_segment_context(
                report_content=report_content,
                segment_id=result.segment_id,
                fallback_content=getattr(result, "content", None),
            )

            if content:
                relevant_segments.append(content)
                evidence_segment_ids.append(result.segment_id)

                table_id, row_index = self._get_table_row_key(segment) if segment is not None else (None, None)
                col_index = self._get_table_column_index(segment) if segment is not None else None
                metadata = {
                    "segment_id": result.segment_id,
                    "page_number": page_number,
                    "score": getattr(result, "score", 0),
                    "retrieval_type": getattr(result, "retrieval_type", ""),
                    "matched_keywords": getattr(result, "matched_keywords", None),
                    "source_table_id": table_id,
                    "row_index": row_index,
                    "column_index": col_index,
                }
                segment_metadata.append(metadata)

        # Deterministic high-confidence shortcut:
        # If the retrieved evidence itself contains the current metric code AND
        # a directly extractable data value, classify it as fully_disclosed
        # before asking the LLM. This prevents the LLM from downgrading clear
        # same-code + data table rows because of over-checking definitions.
        direct_analysis = self._direct_code_data_disclosure_analysis(
            retrieval_result=retrieval_result,
            report_content=report_content,
            metric=metric,
            segment_metadata=segment_metadata,
            evidence_segment_ids=evidence_segment_ids,
        )
        if direct_analysis is not None:
            return direct_analysis

# Build prompt containing tag information
        prompt = self._build_analysis_prompt(
            retrieval_result.metric_name,
            retrieval_result.metric_id,
            relevant_segments,
            segment_metadata,
            metric_unit=(getattr(metric, "unit", None) or "") if metric else "",
            metric_description=((getattr(metric, "definition", None) or getattr(metric, "description", None) or "").strip())
            if metric
            else "",
            metric_code=(getattr(metric, "metric_code", None) or retrieval_result.metric_id) if metric else retrieval_result.metric_id,
            metric_topic=(getattr(metric, "sasb_topic", None) or "") if metric else "",
            metric_category=(getattr(metric, "sasb_category", None) or "") if metric else "",
            metric_type=(getattr(metric, "sasb_type", None) or "") if metric else "",
            metric_keywords=(getattr(metric, "keywords", None) or []) if metric else [],
        )
        
        try:
            json_example = """
            {
              "metric_hit": true,
              "disclosure_status": "fully_disclosed",
              "has_disclosure": true,
              "disclosure_quality": "high",
              "value_status": "converted",
              "reasoning": "The report clearly addresses the metric and discloses total energy use as 511 MWh, which can be safely converted to 1839.6 GJ.",
              "value": 1839.6,
              "raw_value": 511,
              "raw_unit": "MWh",
              "page": 23,
              "evidence_segment_id": "SEG_000123",
              "evidence_quote": "Total energy use was 511 MWh in FY2024...",
              "specific_data_found": "511 MWh (FY2024), normalized to 1839.6 GJ",
              "improvement_suggestions": []
            }
            """

            system_prompt_text = f"""
            You are a professional ESG compliance analysis expert. Please analyze metric
            disclosure status based on the provided information.

            Respond ONLY with a JSON object in the following format. Do not include
            any other text, explanations, or especially, markdown backticks.

            Example Format:
            {json_example}
            """
            
            system_prompt_json = """
You are a professional ESG/SASB disclosure assessment expert.
You must directly decide the final disclosure_status as exactly one of: fully_disclosed, partially_disclosed, not_disclosed.
The disclosure_status field is final. Python will only read/map this field and will not reclassify it from metric_hit, has_disclosure, disclosure_quality, value_status, units, or numeric values.

Assessment principles:
- Use only the provided metric information and retrieved report segments.
- The current Metric Name is the target being assessed. When the SASB definition contains multiple numbered components under the same code, do not assess the entire combined definition as the current metric unless those components are part of the current Metric Name.
- The metric definition/guidance is interpretive context, not an exhaustive checklist. Use it to understand the metric core, denominator, required split, and measurement basis. Do not require every technical-protocol clause, note, example, or auxiliary guidance item to appear.
- A retrieved report segment that explicitly contains the current SASB metric code and a metric-specific value or narrative is the strongest evidence of disclosure. When the row/section label semantically belongs to the current metric or current code-level metric family, treat it as a direct metric hit.
- If a same-code report row/table or same-metric-label row/table provides a numeric value or narrative for the current metric, classify it as fully_disclosed. Do not downgrade because sibling sub-items under the same code are missing, because the unit is different but convertible, because the report does not restate the conversion formula, or because the framework definition contains additional guidance.
- For split metrics under one SASB code, assess the current sub-item only. Missing sibling sub-items must not reduce the status for the current sub-item. A value for a clearly different sub-item should not be used as this sub-item's value.
- If the report label is semantically aligned with the current metric, do not require verbatim wording from the framework. Category labels, employee groups, product labels, operational labels, and line items may be equivalent even when phrased differently.
- Unit differences must be handled by judgment. If the reported value can be safely converted, normalized, or interpreted as an equivalent unit for the current metric, keep the disclosure as fully_disclosed when the metric itself is directly disclosed. Provide raw_value/raw_unit and converted value when possible.
- Do not downgrade from fully_disclosed solely because of source unit wording, reporting-unit wording, missing conversion narrative, non-core scope uncertainty, or definition/guidance text when the reported value is directly usable for the current metric.
- Never put narrative text in value. Do not choose a number from a clearly different line item or clearly different sub-item.
"""

            FORCE_JSON = True # If model outputs thought train in response
            
            api_kwargs = {
                "model": self.config.llm_model,
                "temperature": 0.2  # Lower for more stable JSON extraction
            }
            
            queries = []
            
            if (FORCE_JSON):
                if _is_claude_model(self.config.llm_model):
                    api_kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "disclosure_analysis",
                            "strict": True,
                            "schema": DISCLOSURE_JSON_SCHEMA,
                        },
                    }
                else:
                    api_kwargs["response_format"] = {"type": "json_object"}
                queries.append({"role": "system", "content": system_prompt_json})
                queries.append({"role": "user", "content": prompt})

                # (Optional) Add the assistant prefill for models like Claude
                # messages.append({"role": "assistant", "content": "{"}) 
            else:
                queries.append({"role": "system", "content": system_prompt_text})
                queries.append({"role": "user", "content": prompt})

            api_kwargs["messages"] = queries

            # Call LLM for analysis
            response = self.llm_client.chat.completions.create(
                **api_kwargs
            )
            
            #print("======== DEBUG LLM RESPONSE ========")
            #print(response.choices[0].message.content)
            
            # Parse JSON response (response_format=json_object should already guarantee JSON)
            content = (response.choices[0].message.content or "").strip()
            llm_result = self._extract_json_from_llm_response(content)
            if llm_result is None:
                logger.warning(
                    f"LLM did not return valid JSON for metric {retrieval_result.metric_name}; "
                    "returning fallback NOT_DISCLOSED analysis."
                )
                return self._fallback_disclosure_analysis(
                    retrieval_result, metric, evidence_segment_ids,
                    reasoning="LLM did not return valid JSON; analysis skipped."
                )

            # Validate required fields from LLM
            if "reasoning" not in llm_result or not llm_result["reasoning"]:
                logger.warning(
                    f"LLM response missing required 'reasoning' for metric {retrieval_result.metric_name}; "
                    "returning fallback analysis."
                )
                return self._fallback_disclosure_analysis(
                    retrieval_result, metric, evidence_segment_ids,
                    reasoning="LLM response missing required reasoning field."
                )

            # -----------------------------
            # Read LLM final status / value / page / context
            # -----------------------------
            # IMPORTANT:
            # disclosure_status is decided by the prompt/LLM directly. Python only maps
            # that field to the internal enum and does not reclassify from
            # metric_hit/has_disclosure/disclosure_quality/value_status/units/numbers.
            disclosure_status = self._map_llm_disclosure_status(llm_result)

            found_numeric = _parse_llm_numeric_value_only(llm_result.get("value", None))
            if found_numeric is None:
                found_numeric = _parse_llm_numeric_value_only(llm_result.get("raw_value", None))

            found_page = None
            found_context = None

            # Candidate pages set (for validation)
            candidate_pages = {
                m.get("page_number")
                for m in (segment_metadata or [])
                if m.get("page_number") is not None
            }

            # 1) Prefer page specified by LLM if valid
            llm_page = llm_result.get("page", None)
            if llm_page is not None:
                try:
                    # Allow formats like "p. 12" / "page 12"
                    mpage = re.search(r"\d+", str(llm_page))
                    llm_page_int = int(mpage.group(0)) if mpage else int(str(llm_page).strip())
                    if (not candidate_pages) or (llm_page_int in candidate_pages):
                        found_page = llm_page_int
                except Exception:
                    pass

            # 2) If LLM returned an evidence segment id, map it back to page
            evidence_seg_id = llm_result.get("evidence_segment_id", None)
            if found_page is None and evidence_seg_id and segment_metadata:
                for meta in segment_metadata:
                    if meta.get("segment_id") == evidence_seg_id and meta.get("page_number") is not None:
                        found_page = meta.get("page_number")
                        break

            # 3) As fallback, pick page of highest scoring segment (or top retrieval result)
            best_segment_meta = None
            if segment_metadata:
                best_segment_meta = max(segment_metadata, key=lambda x: x.get("score", 0) or 0)
                if found_page is None:
                    found_page = best_segment_meta.get("page_number")

            if found_page is None:
                try:
                    top = (retrieval_result.combined_results or [])[0]
                    if top and getattr(top, "page_number", None) is not None:
                        found_page = top.page_number
                except Exception:
                    pass

            # 4) Context/value: for table-cell evidence, preserve the full table row and prefer latest-year value.
            selected_segment_id = evidence_seg_id or (best_segment_meta or {}).get("segment_id")
            selected_row_context = None
            selected_latest_numeric = None
            selected_latest_unit = None
            if selected_segment_id:
                try:
                    selected_segment = self._get_segment_by_id(report_content, selected_segment_id)
                    if selected_segment is not None:
                        row_ctx, latest_num, latest_unit, _latest_desc = self._build_table_row_aggregation_context(
                            report_content, selected_segment, max_chars=1600
                        )
                        if row_ctx:
                            selected_row_context = row_ctx
                            selected_latest_numeric = latest_num
                            selected_latest_unit = latest_unit
                except Exception:
                    pass

            # Prefer the LLM-returned metric-specific value. Table row aggregation is
            # only a fallback because aggregated cells can contain paired sibling values
            # from the same table row (for example an absolute value plus a percentage).
            if (
                disclosure_status != DisclosureStatus.NOT_DISCLOSED
                and found_numeric is None
                and selected_latest_numeric is not None
            ):
                expected_unit = getattr(metric, "unit", "") if metric else ""
                converted_latest = _convert_numeric_value_between_units(selected_latest_numeric, selected_latest_unit, expected_unit)
                found_numeric = converted_latest if converted_latest is not None else selected_latest_numeric

            # Prefer the LLM-selected quote/context. Use full-row context only when
            # the LLM did not return metric-specific context.
            quote = llm_result.get("evidence_quote", None)
            if quote:
                found_context = str(quote).strip()
            else:
                specific_data = llm_result.get("specific_data_found", None)
                if specific_data:
                    if isinstance(specific_data, list):
                        found_context = "; ".join(str(x) for x in specific_data if x is not None).strip() or None
                    else:
                        found_context = str(specific_data).strip() or None

            if (
                disclosure_status != DisclosureStatus.NOT_DISCLOSED
                and not found_context
                and selected_row_context
            ):
                found_context = selected_row_context

            if not found_context and best_segment_meta is not None:
                # Provide a short excerpt for UI hover to reduce "empty" feeling.
                idx = None
                # Map back to segments list by order
                for i, meta in enumerate(segment_metadata):
                    if meta.get("segment_id") == best_segment_meta.get("segment_id"):
                        idx = i
                        break
                if idx is not None and idx < len(relevant_segments):
                    excerpt = str(relevant_segments[idx]).strip()
                    if excerpt:
                        found_context = excerpt[:900]

            if disclosure_status == DisclosureStatus.NOT_DISCLOSED:
                stored_value: Union[int, float, str] = COMPLIANCE_VALUE_NA
            else:
                stored_value = _finalize_compliance_value_field(found_numeric)

            # Create analysis result
            analysis = DisclosureAnalysis(
                metric_id=retrieval_result.metric_id,
                metric_name=retrieval_result.metric_name,
                metric_code=retrieval_result.metric_code,
                disclosure_status=disclosure_status,
                reasoning=llm_result["reasoning"],
                evidence_segments=evidence_segment_ids,
                improvement_suggestions=llm_result.get("improvement_suggestions", []),  # This field is optional
                # SASB display fields
                category=getattr(metric, 'sasb_category', '') if metric else '',
                topic=(getattr(metric, 'sasb_topic', None) or '') if metric else '',
                unit=getattr(metric, 'unit', '') or '' if metric else '',
                type=getattr(metric, 'sasb_type', '') if metric else '',
                definition=(getattr(metric, 'definition', None) or '') if metric else '',
                value=stored_value,
                context=found_context,
                page=found_page
            )
            
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM JSON for metric {retrieval_result.metric_name}: {e}")
            return self._fallback_disclosure_analysis(
                retrieval_result, metric, evidence_segment_ids,
                reasoning=f"LLM returned invalid JSON: {e}",
            )
        except Exception as e:
            logger.warning(f"LLM analysis failed for metric {retrieval_result.metric_name}: {e}")
            return self._fallback_disclosure_analysis(
                retrieval_result, metric, evidence_segment_ids,
                reasoning=f"Analysis error: {e}",
            )

        return analysis
    
    def _is_quantitative_metric(self, metric: Optional['ESGMetric']) -> bool:
        if metric is None:
            return False
        category = str(getattr(metric, "sasb_category", "") or "").strip().lower()
        metric_type = str(getattr(metric, "sasb_type", "") or "").strip().lower()
        unit = str(getattr(metric, "unit", "") or "").strip()
        return bool(unit) or category == "quantitative" or "quantitative" in metric_type

    def _is_qualitative_metric(self, metric: Optional['ESGMetric']) -> bool:
        return not self._is_quantitative_metric(metric)

    def _get_final_window_size(self, metric: Optional['ESGMetric'], observed_matches: int = 0) -> int:
        base = 18 if self._is_quantitative_metric(metric) else 11
        metric_name = str(getattr(metric, "metric_name", "") or "") if metric is not None else ""
        definition = str(getattr(metric, "definition", "") or getattr(metric, "description", "") or "") if metric is not None else ""
        token_count = len(re.findall(r"[A-Za-z0-9]{3,}", f"{metric_name} {definition}"))
        complexity_bonus = min(7, token_count // 16)
        if observed_matches >= 1000:
            richness_bonus = 10
        elif observed_matches >= 400:
            richness_bonus = 7
        elif observed_matches >= 150:
            richness_bonus = 4
        elif observed_matches >= 50:
            richness_bonus = 2
        else:
            richness_bonus = 0
        return max(11, min(40, base + complexity_bonus + richness_bonus))

    def _truncate_segment_text(self, text: str, max_chars: int = 1200) -> str:
        value = str(text or "").strip()
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 3].rstrip() + "..."

    def _get_ordered_segments(self, report_content: ReportContent):
        return sorted(
            report_content.document_content.segments,
            key=lambda seg: (
                getattr(seg, "page_number", 0) or 0,
                getattr(seg, "position_y", 0.0) or 0.0,
                getattr(seg, "segment_id", ""),
            ),
        )

    def _is_adjacent_context_segment(self, target_segment, candidate_segment) -> bool:
        if candidate_segment is None or target_segment is None:
            return False
        tp = getattr(target_segment, "page_number", None)
        cp = getattr(candidate_segment, "page_number", None)
        if tp is None or cp is None:
            return False
        if abs(int(cp) - int(tp)) > 1:
            return False
        tc = str(getattr(target_segment, "content", "") or "").strip()
        cc = str(getattr(candidate_segment, "content", "") or "").strip()
        if not cc or cc == tc:
            return False
        return True

    def _segment_structured_data_dict(self, segment) -> Dict[str, Any]:
        """Return structured_data as a dict when available."""
        if segment is None:
            return {}
        structured_data = getattr(segment, "structured_data", None)
        if isinstance(structured_data, dict):
            return structured_data
        if isinstance(structured_data, str) and structured_data.strip():
            try:
                parsed = json.loads(structured_data)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _segment_id_table_parts(self, segment) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """Best-effort parser for segment IDs like P003_T001_R006_C002."""
        segment_id = str(getattr(segment, "segment_id", "") or "")
        m = re.search(r"(P\d+_T\d+)_R(\d+)_C(\d+)", segment_id)
        if not m:
            return None, None, None
        try:
            return m.group(1), int(m.group(2)), int(m.group(3))
        except Exception:
            return m.group(1), None, None

    def _get_segment_field(self, segment, *field_names: str) -> Any:
        """Read a field from segment attrs first, then structured_data, ignoring blank/null strings."""
        for name in field_names:
            value = getattr(segment, name, None) if segment is not None else None
            if value is not None and str(value).strip().lower() not in {"", "none", "null", "nan"}:
                return value
        data = self._segment_structured_data_dict(segment)
        for name in field_names:
            value = data.get(name)
            if value is not None and str(value).strip().lower() not in {"", "none", "null", "nan"}:
                return value
        return None

    def _get_table_row_key(self, segment) -> Tuple[Optional[str], Optional[int]]:
        """Return (source_table_id, row_index) for table/cell segments, with segment_id fallback."""
        table_id = self._get_segment_field(
            segment,
            "source_table_id", "table_id", "source_table", "table_uid", "table_index", "source_table_index",
        )
        row_index = self._get_segment_field(segment, "row_index", "row_idx", "row_number", "source_row_index")
        parsed_table_id, parsed_row, _ = self._segment_id_table_parts(segment)
        if table_id is None:
            table_id = parsed_table_id
        if row_index is None:
            row_index = parsed_row
        try:
            row_index = int(row_index) if row_index is not None else None
        except Exception:
            m = re.search(r"\d+", str(row_index))
            row_index = int(m.group(0)) if m else None
        if table_id is None or row_index is None:
            return None, None
        return str(table_id), row_index

    def _get_table_column_index(self, segment) -> Optional[int]:
        col_index = self._get_segment_field(segment, "column_index", "col_index", "column_idx", "col_number", "source_column_index")
        if col_index is None:
            _, _, parsed_col = self._segment_id_table_parts(segment)
            col_index = parsed_col
        try:
            return int(col_index) if col_index is not None else None
        except Exception:
            m = re.search(r"\d+", str(col_index))
            return int(m.group(0)) if m else None

    def _extract_years_from_text(self, text: object) -> List[int]:
        raw = str(text or "")
        years: List[int] = []
        for m in re.finditer(r"(?i)\b(?:FY|CY|FISCAL\s+YEAR\s*)?(20\d{2}|19\d{2})\b", raw):
            try:
                y = int(m.group(1))
                if 1990 <= y <= 2100:
                    years.append(y)
            except Exception:
                pass
        return years

    def _extract_numeric_from_cell_text(self, text: object) -> Optional[Union[int, float]]:
        raw = str(text or "").strip()
        if not raw:
            return None
        direct = _parse_llm_numeric_value_only(raw)
        if direct is not None:
            return direct
        # Avoid long prose. For table cells, pick the last non-year numeric token.
        if len(raw) > 160:
            return None
        candidates = re.findall(r"-?\d[\d,]*(?:\.\d+)?%?", raw)
        usable: List[str] = []
        for token in candidates:
            cleaned = token.replace(",", "").rstrip("%")
            try:
                val = float(cleaned)
            except Exception:
                continue
            if 1900 <= val <= 2100 and re.fullmatch(r"\d{4}", cleaned):
                continue
            usable.append(token)
        if not usable:
            return None
        # A compact table cell can contain multiple paired values, such as
        # "5,155,385: 85% MWh". In that case, picking the last number would
        # incorrectly select a sibling percentage instead of the absolute value.
        # Return None and let the LLM/context choose the metric-specific value.
        has_percent = any(str(tok).strip().endswith("%") for tok in usable)
        has_non_percent = any(not str(tok).strip().endswith("%") for tok in usable)
        if len(usable) > 1 and has_percent and has_non_percent:
            return None
        return _parse_llm_numeric_value_only(usable[-1])

    def _build_table_row_aggregation_context(
        self,
        report_content: ReportContent,
        target_segment,
        max_chars: int = 1400,
    ) -> Tuple[str, Optional[Union[int, float]], Optional[str], Optional[str]]:
        """Aggregate all cells from the same table row.

        The row key is source_table_id + row_index, with segment_id fallback.
        The returned context is for the LLM/UI only; it does not directly classify status.
        The numeric candidate is the latest-year cell in that row when a year can be identified.
        """
        table_id, row_index = self._get_table_row_key(target_segment)
        if table_id is None or row_index is None:
            return "", None, None, None

        try:
            all_segments = list(getattr(report_content.document_content, "segments", []) or [])
        except Exception:
            all_segments = []

        row_segments = []
        for seg in all_segments:
            key = self._get_table_row_key(seg)
            if key == (table_id, row_index):
                row_segments.append(seg)

        if len(row_segments) <= 1:
            return "", None, None, None

        def sort_key(seg):
            col = self._get_table_column_index(seg)
            return (
                col if col is not None else 10_000,
                getattr(seg, "position_x", 0.0) or 0.0,
                str(getattr(seg, "segment_id", "") or ""),
            )

        row_segments = sorted(row_segments, key=sort_key)

        row_header_values = []
        for seg in row_segments:
            rh = self._format_short_metadata_value(self._get_segment_field(seg, "row_header"), 220)
            if rh and rh not in row_header_values:
                row_header_values.append(rh)
        row_header = row_header_values[0] if row_header_values else ""

        cells: List[str] = []
        latest_year: Optional[int] = None
        latest_numeric: Optional[Union[int, float]] = None
        latest_unit: Optional[str] = None
        latest_desc: Optional[str] = None

        for seg in row_segments:
            col_index = self._get_table_column_index(seg)
            col_header = self._format_short_metadata_value(self._get_segment_field(seg, "col_header", "column_header"), 120)
            value_text = self._format_short_metadata_value(self._get_segment_field(seg, "value_text", "cell_value", "value"), 180)
            unit = self._format_short_metadata_value(self._get_segment_field(seg, "unit", "cell_unit", "raw_unit"), 80)
            content = self._format_short_metadata_value(getattr(seg, "content", "") or "", 260)

            display_value = value_text or content
            if unit and display_value and unit.lower() not in display_value.lower():
                display_value = f"{display_value} {unit}"
            label = col_header or (f"C{col_index}" if col_index is not None else "Cell")
            cell_line = f"{label}: {display_value}" if display_value else label
            if cell_line and cell_line not in cells:
                cells.append(cell_line)

            year_candidates = self._extract_years_from_text(" ".join([col_header, content, value_text]))
            cell_year = max(year_candidates) if year_candidates else None
            numeric_candidate = self._extract_numeric_from_cell_text(value_text or content)
            if cell_year is not None and numeric_candidate is not None:
                if latest_year is None or cell_year > latest_year:
                    latest_year = cell_year
                    latest_numeric = numeric_candidate
                    latest_unit = unit or self._format_short_metadata_value(self._get_segment_field(seg, "raw_unit"), 80) or None
                    latest_desc = cell_line

        if not cells:
            return "", None, None, None

        parts = ["[Full Table Row Context]"]
        parts.append(f"- Row Key: {table_id} / row {row_index}")
        if row_header:
            parts.append(f"- Row Header: {row_header}")
        parts.append("- Row Cells: " + " | ".join(cells))
        if latest_year is not None and latest_desc:
            parts.append(f"- Latest Year Candidate: FY{latest_year}: {latest_desc}")
        row_context = "\n".join(parts)
        if len(row_context) > max_chars:
            row_context = row_context[: max_chars - 3].rstrip() + "..."
        return row_context, latest_numeric, latest_unit, latest_desc

    def _format_short_metadata_value(self, value: object, max_chars: int = 260) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", " ", text)
        if len(text) > max_chars:
            return text[: max_chars - 3].rstrip() + "..."
        return text

    def _build_structured_segment_hint(self, segment) -> str:
        """Build a compact metadata hint for table/cell-derived evidence.

        This only improves the evidence text shown to the LLM. It does not
        classify disclosure status or post-process the LLM decision.
        """
        if segment is None:
            return ""

        hint_lines: List[str] = []
        segment_type = self._format_short_metadata_value(getattr(segment, "segment_type", None), 80)
        if segment_type:
            hint_lines.append(f"- Segment Type: {segment_type}")

        table_id, row_index = self._get_table_row_key(segment)
        col_index = self._get_table_column_index(segment)
        if table_id is not None:
            hint_lines.append(f"- Source Table ID: {table_id}")
        if row_index is not None:
            hint_lines.append(f"- Row Index: {row_index}")
        if col_index is not None:
            hint_lines.append(f"- Column Index: {col_index}")

        row_header = self._format_short_metadata_value(self._get_segment_field(segment, "row_header"))
        if row_header:
            hint_lines.append(f"- Row Header: {row_header}")

        col_header = self._format_short_metadata_value(self._get_segment_field(segment, "col_header", "column_header"))
        if col_header:
            hint_lines.append(f"- Column Header: {col_header}")

        value_text = self._format_short_metadata_value(self._get_segment_field(segment, "value_text", "cell_value", "value"))
        if value_text:
            hint_lines.append(f"- Cell Value: {value_text}")

        unit = self._format_short_metadata_value(self._get_segment_field(segment, "unit", "cell_unit", "raw_unit"), 120)
        if unit:
            hint_lines.append(f"- Cell Unit: {unit}")

        structured_data = getattr(segment, "structured_data", None)
        if structured_data:
            try:
                structured_text = json.dumps(structured_data, ensure_ascii=False, default=str)
            except Exception:
                structured_text = str(structured_data)
            structured_text = self._format_short_metadata_value(structured_text, 420)
            if structured_text:
                hint_lines.append(f"- Structured Data: {structured_text}")

        if not hint_lines:
            return ""
        return "[Structured Evidence Metadata]\n" + "\n".join(hint_lines)

    def _build_augmented_segment_context(self, report_content: ReportContent, segment_id: str, fallback_content: Optional[str] = None) -> Optional[str]:
        target_segment = self._get_segment_by_id(report_content, segment_id)
        if target_segment is None:
            return fallback_content
        ordered_segments = self._get_ordered_segments(report_content)
        target_index = None
        for idx, seg in enumerate(ordered_segments):
            if seg.segment_id == segment_id:
                target_index = idx
                break
        if target_index is None:
            return getattr(target_segment, "content", None) or fallback_content
        parts: List[str] = []
        if target_index > 0:
            prev_seg = ordered_segments[target_index - 1]
            if self._is_adjacent_context_segment(target_segment, prev_seg):
                prev_text = self._truncate_segment_text(getattr(prev_seg, "content", ""))
                if prev_text:
                    parts.append(f"[Previous Context]\n{prev_text}")
        structured_hint = self._build_structured_segment_hint(target_segment)
        if structured_hint:
            parts.append(structured_hint)

        row_context, _, _, _ = self._build_table_row_aggregation_context(report_content, target_segment)
        if row_context:
            parts.append(row_context)

        hit_text = self._truncate_segment_text(getattr(target_segment, "content", "") or fallback_content or "")
        if hit_text:
            parts.append(f"[Hit Segment]\n{hit_text}")
        if target_index + 1 < len(ordered_segments):
            next_seg = ordered_segments[target_index + 1]
            if self._is_adjacent_context_segment(target_segment, next_seg):
                next_text = self._truncate_segment_text(getattr(next_seg, "content", ""))
                if next_text:
                    parts.append(f"[Next Context]\n{next_text}")
        return "\n\n".join(parts) if parts else hit_text or fallback_content

    def _build_analysis_prompt(
        self,
        metric_name: str,
        metric_id: str,
        segments: List[str],
        segment_metadata: List[Dict] = None,
        metric_unit: str = "",
        metric_description: str = "",
        metric_code: str = "",
        metric_topic: str = "",
        metric_category: str = "",
        metric_type: str = "",
        metric_keywords: Optional[List[str]] = None,
    ) -> str:
        """
        Build LLM analysis prompt containing segment tag information
        
        Args:
            metric_name: Metric name
            metric_id: Metric ID
            segments: Related segment content
            segment_metadata: Segment metadata information
            metric_unit: Expected unit for quantitative metrics (if any)
            metric_description: Extra metric definition from framework data
            metric_topic: Framework topic/theme, if available
            metric_category: Framework category, if available
            metric_type: Framework type, if available
            metric_keywords: Framework/search keywords, if available
            
        Returns:
            str: Prompt text
        """
        # Build segment text containing tag information.
        # IMPORTANT: Include segment_id to allow the LLM to point back to the exact evidence.
        segments_text_parts: List[str] = []
        for i, seg in enumerate(segments):
            seg = str(seg or "").strip()
            segment_info = f"Segment {i+1}:\n{seg}"

            if segment_metadata and i < len(segment_metadata):
                meta = segment_metadata[i] or {}
                seg_id = meta.get("segment_id", "")
                page = meta.get("page_number", None)
                rtype = meta.get("retrieval_type", "")
                score = meta.get("score", 0.0)
                segment_info += f"\n[Segment ID: {seg_id}; Tag Info: Page {page}, Retrieval Type: {rtype}, Match Score: {score:.3f}"
                if meta.get("matched_keywords"):
                    try:
                        kws = ", ".join(meta.get("matched_keywords") or [])
                        if kws:
                            segment_info += f", Matched Keywords: {kws}"
                    except Exception:
                        pass
                segment_info += "]"

            segments_text_parts.append(segment_info)

        segments_text = "\n\n".join(segments_text_parts)

        unit_line = (
            f"- Expected unit (if quantitative): {metric_unit}\n"
            if (metric_unit or "").strip()
            else ""
        )
        topic_line = (
            f"- Framework topic: {metric_topic}\n"
            if (metric_topic or "").strip()
            else ""
        )
        category_line = (
            f"- Framework category: {metric_category}\n"
            if (metric_category or "").strip()
            else ""
        )
        type_line = (
            f"- Framework type: {metric_type}\n"
            if (metric_type or "").strip()
            else ""
        )
        keyword_values = [str(x).strip() for x in (metric_keywords or []) if str(x).strip()]
        keywords_line = (
            f"- Metric keywords: {', '.join(keyword_values[:18])}\n"
            if keyword_values
            else ""
        )
        desc_text = str(metric_description or "").strip()
        if len(desc_text) > 3600:
            desc_text = desc_text[:3597].rstrip() + "..."
        desc_line = (
            f"- Metric definition / guidance: {desc_text}\n"
            if desc_text
            else ""
        )

        prompt = f"""As a professional ESG/SASB compliance analyst, conduct one unified disclosure assessment for the current metric.

Metric Information:
- Metric Name: {metric_name}
- Metric Code: {metric_code or metric_id}
{topic_line}{category_line}{type_line}{unit_line}{keywords_line}{desc_line}
All Related Retrieved Segments (with segment_id + tag info):
{segments_text if segments_text else "No related segments found"}

Analyze all retrieved segments together. Do not score segments independently. The final answer must be based on the best evidence for the current metric.

Core assessment principles:
1) Respond ONLY with a JSON object. No markdown and no backticks.
2) The final "disclosure_status" must be exactly one of: "fully_disclosed", "partially_disclosed", "not_disclosed".
3) Python will not derive or correct the status later. Your "disclosure_status" is the final classification.
4) The current Metric Name is the metric being assessed. If the framework definition contains multiple components under the same SASB code, do not require components that are not part of the current Metric Name.
5) Assess the current metric itself, not the broader topic and not all sibling sub-items under the same SASB code.
6) Treat the metric definition/guidance as interpretive context, not as a clause-by-clause checklist. It helps identify the metric core, denominator, required split, and measurement basis. Do not require every note, example, auxiliary detail, or technical-protocol phrase unless it materially changes the current metric itself.
7) Same SASB code / same metric direct-disclosure rule: if a retrieved report segment, table row, or table section explicitly contains the current SASB code OR clearly contains the current metric label, and provides the current metric's value or narrative, classify the current metric as fully_disclosed.
8) Same-code evidence has priority over definition overchecking. Do not downgrade a direct disclosure only because sibling components are absent, because definition/guidance contains extra clauses, because the wording is not identical, because the report does not restate the technical protocol, or because the source unit differs from the expected unit but is equivalent or convertible.
9) For split metrics under one SASB code, assess only the current Metric Name. A direct value for the current sub-item is fully_disclosed even if sibling sub-items under the same code are absent. A value for a clearly different sub-item should not be used as the value for this sub-item.
10) Unit differences must be handled by judgment, not treated as automatic gaps. If the reported unit is equivalent, safely convertible, or standardly normalizable to the expected unit, use the raw value/raw unit and provide the converted value when possible. Do not downgrade from fully_disclosed when unit conversion, unit wording, or missing conversion narrative is the only remaining issue.
11) Do not require the report to state a conversion factor if the conversion is standard and safe. Use ordinary conversions such as MWh to GJ, kWh to GJ, liters to cubic meters, kilograms to metric tons, and percentage fractions when appropriate.
12) Use "value_status" as one of: exact, converted, approximate, raw_unit_only, unit_mismatch, ambiguous, none.
13) For qualitative/narrative metrics, a direct description of the requested approach, policy, process, practice, governance mechanism, or management system can be fully_disclosed without a numeric value. Set "value" to null in that case.
14) Distinguish imperfect disclosure from no disclosure. If the current metric itself is disclosed but has non-core ambiguity, use partially_disclosed rather than not_disclosed. If the current metric is directly and sufficiently disclosed, use fully_disclosed.
15) evidence_quote must be a short excerpt (<= 180 chars) that supports your conclusion; when using table evidence, quote from the full row context rather than an isolated cell.
16) If a segment includes [Full Table Row Context], treat that row as one evidence unit. Use the row header, all row cells, column headers, values, and units together; do not judge a table cell in isolation.
17) For table rows with multiple years, use the latest reporting year as the preferred extracted value unless the current metric explicitly requires another period.
18) If a segment includes [Structured Evidence Metadata], use row headers, column headers, cell values, and units as table context.
19) If multiple candidate segments conflict, choose the segment that most directly matches the current metric name, current metric code, expected unit, category/split, and framework context.

Disclosure status definitions:

fully_disclosed:
The evidence directly discloses the current metric itself and provides the metric-specific value or metric-specific narrative needed to answer it. A report row/table/section that explicitly contains the current SASB code or the current metric label and gives a value or narrative for the current metric is fully_disclosed. For quantitative metrics, values may be reported in the expected unit, an equivalent unit, or a safely convertible/normalizable unit. For qualitative metrics, a direct description of the requested approach, policy, process, practice, governance mechanism, or management system is fully_disclosed. Fully_disclosed does not require the report to reproduce all definition/guidance clauses, technical-protocol details, examples, notes, auxiliary explanatory items, all sibling components, or an explicit conversion formula unless those items materially change the current metric core, denominator, required split, or measurement basis. Non-core scope or methodology uncertainty should not prevent fully_disclosed when the current metric label/code and value/narrative are directly disclosed.

partially_disclosed:
The evidence addresses the current metric itself but does not fully answer it. This includes cases where the metric is clearly hit but the disclosed value is not directly usable, the current sub-item or denominator is not clear, the value is only a proxy for the current metric, the evidence is limited to a narrower required split/category, or the qualitative evidence covers only part of the requested approach. Partially_disclosed requires evidence for the current metric itself, not merely the broader topic or a sibling sub-item. Do not use partially_disclosed merely because the report omits sibling sub-items, uses a convertible unit, or does not repeat every definition/guidance detail.

not_disclosed:
The evidence does not disclose the current metric itself. This includes no retrieved evidence, unrelated evidence, broader-topic discussion without the current metric, a value for a clearly different metric or clearly different sub-item, target-only or future-only statements for a current-performance metric, generic policies or activities that do not answer the requested metric, or evidence that lacks a metric-specific value or metric-specific narrative. Same topic, same SASB topic area, or same general sustainability theme is not enough when the current metric itself is not disclosed. However, do not use not_disclosed when a report row/table directly provides the current metric's value or narrative under the same SASB code or current metric label.

Return JSON format:
{{
  "metric_hit": <true|false|null>,
  "disclosure_status": "fully_disclosed|partially_disclosed|not_disclosed",
  "has_disclosure": true/false,
  "disclosure_quality": "high/medium/low/none",
  "value_status": "exact|converted|approximate|raw_unit_only|unit_mismatch|ambiguous|none|null",
  "reasoning": "Comprehensive reasoning based on all segments",
  "value": <number|null>,
  "raw_value": <number|null>,
  "raw_unit": "<unit|null>",
  "page": <int|null>,
  "evidence_segment_id": "<segment_id|null>",
  "evidence_quote": "<short quote|null>",
  "specific_data_found": "<detailed context, may include unit/year if present>",
  "improvement_suggestions": ["Suggestion 1", "Suggestion 2", ...]
}}
"""
        return prompt
    
    def _map_llm_disclosure_status(self, llm_response: dict) -> DisclosureStatus:
        """Map the LLM-provided final disclosure_status to the internal enum.

        This function intentionally does not infer or reclassify from other fields
        such as metric_hit, has_disclosure, disclosure_quality, value_status,
        numeric values, or units. The prompt is responsible for the final
        disclosure decision.
        """
        raw_status = str(llm_response.get("disclosure_status", "") or "").strip().lower()
        raw_status = raw_status.replace(" ", "_").replace("-", "_")
        aliases = {
            "fully_disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "full_disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "full": DisclosureStatus.FULLY_DISCLOSED,
            "high_quality_disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "disclosed": DisclosureStatus.FULLY_DISCLOSED,
            "partially_disclosed": DisclosureStatus.PARTIALLY_DISCLOSED,
            "partial_disclosed": DisclosureStatus.PARTIALLY_DISCLOSED,
            "partial": DisclosureStatus.PARTIALLY_DISCLOSED,
            "partially": DisclosureStatus.PARTIALLY_DISCLOSED,
            "not_disclosed": DisclosureStatus.NOT_DISCLOSED,
            "non_disclosed": DisclosureStatus.NOT_DISCLOSED,
            "none": DisclosureStatus.NOT_DISCLOSED,
            "not": DisclosureStatus.NOT_DISCLOSED,
            "no_disclosure": DisclosureStatus.NOT_DISCLOSED,
            "undisclosed": DisclosureStatus.NOT_DISCLOSED,
        }
        if raw_status not in aliases:
            raise ValueError(
                "LLM response missing or invalid final disclosure_status; "
                "expected fully_disclosed, partially_disclosed, or not_disclosed."
            )
        return aliases[raw_status]

    def _get_segment_by_id(self, report_content: ReportContent, segment_id: str):
        """
        Get segment content by ID
        
        Args:
            report_content: Report content
            segment_id: Segment ID
            
        Returns:
            TextSegment or None
        """
        for segment in report_content.document_content.segments:
            if segment.segment_id == segment_id:
                return segment
        return None
    
    def generate_compliance_report(self, assessment: ComplianceAssessment) -> str:
        """
        Generate Markdown format compliance report
        
        Args:
            assessment: Compliance assessment result
            
        Returns:
            str: Markdown format report
        """
        def _status_value(analysis: DisclosureAnalysis) -> str:
            raw = getattr(analysis, "disclosure_status", "")
            return raw.value if hasattr(raw, "value") else str(raw or "")

        def _status_label(status: str) -> str:
            return {
                "fully_disclosed": "Fully Disclosed",
                "partially_disclosed": "Partially Disclosed",
                "not_disclosed": "Not Disclosed",
            }.get(status, status.replace("_", " ").title() or "Unknown")

        def _clean_text(value: object, *, default: str = "") -> str:
            text = str(value or default).replace("\r\n", "\n").replace("\r", "\n")
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

        def _compact(value: object, limit: int = 180) -> str:
            text = re.sub(r"\s+", " ", _clean_text(value)).strip()
            if not text:
                return ""
            return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."

        def _table_cell(value: object, limit: int = 180) -> str:
            text = _compact(value, limit=limit)
            if not text:
                return "-"
            return text.replace("|", "\\|")

        def _metric_code(analysis: DisclosureAnalysis) -> str:
            return (
                _clean_text(getattr(analysis, "metric_code", ""))
                or _clean_text(getattr(analysis, "metric_id", ""))
                or "-"
            )

        def _metric_name(analysis: DisclosureAnalysis) -> str:
            return _clean_text(getattr(analysis, "metric_name", "")) or "Unnamed metric"

        def _value_text(analysis: DisclosureAnalysis) -> str:
            value = getattr(analysis, "value", None)
            if value is None or str(value).strip().lower() in {"", "n/a", "na", "none", "null"}:
                return "n/a"
            unit = _clean_text(getattr(analysis, "unit", ""))
            return f"{value} {unit}".strip()

        def _page_text(analysis: DisclosureAnalysis) -> str:
            page = getattr(analysis, "page", None)
            return str(page) if page not in (None, "") else "-"

        def _evidence_text(analysis: DisclosureAnalysis, limit: int = 3) -> str:
            segments = [
                str(x).strip()
                for x in (getattr(analysis, "evidence_segments", None) or [])
                if str(x).strip()
            ]
            if not segments:
                return "-"
            shown = segments[:limit]
            suffix = f" (+{len(segments) - limit} more)" if len(segments) > limit else ""
            return ", ".join(shown) + suffix

        def _first_suggestion(analysis: DisclosureAnalysis) -> str:
            suggestions = [
                _compact(x, 220)
                for x in (getattr(analysis, "improvement_suggestions", None) or [])
                if _clean_text(x)
            ]
            if suggestions:
                return suggestions[0]
            status = _status_value(analysis)
            if status == "not_disclosed":
                return "Add an explicit metric-level disclosure with value, unit, reporting period, and boundary."
            if status == "partially_disclosed":
                return "Clarify the disclosed value or narrative with scope, methodology, unit, and reporting period."
            return "Maintain consistent metric-level disclosure and evidence references in future reports."

        def _pct(count: int, total: int) -> str:
            return f"{count / max(total, 1):.1%}"

        def _metric_phrase(count: int) -> str:
            return f"{count} metric" if count == 1 else f"{count} metrics"

        def _topic_key(analysis: DisclosureAnalysis) -> str:
            return (
                _clean_text(getattr(analysis, "topic", ""))
                or _clean_text(getattr(analysis, "type", ""))
                or _clean_text(getattr(analysis, "category", ""))
                or "General"
            )

        def _append_field(lines: List[str], label: str, value: object, *, limit: int = 0) -> None:
            text = _clean_text(value)
            if not text:
                return
            if limit > 0:
                text = _compact(text, limit)
            lines.append(f"- **{label}**: {text}")

        def _append_suggestions(lines: List[str], analysis: DisclosureAnalysis) -> None:
            suggestions = [
                _clean_text(x)
                for x in (getattr(analysis, "improvement_suggestions", None) or [])
                if _clean_text(x)
            ]
            if not suggestions and _status_value(analysis) != "fully_disclosed":
                suggestions = [_first_suggestion(analysis)]
            if not suggestions:
                return
            lines.append("- **Recommended actions**:")
            for suggestion in suggestions[:5]:
                lines.append(f"  - {suggestion}")

        seen_metric_ids = set()
        unique_metric_analyses = []
        for analysis in assessment.metric_analyses:
            key = (
                _clean_text(getattr(analysis, "metric_id", ""))
                or _clean_text(getattr(analysis, "metric_code", ""))
                or _metric_name(analysis)
            )
            if key not in seen_metric_ids:
                unique_metric_analyses.append(analysis)
                seen_metric_ids.add(key)

        total_unique_metrics = len(unique_metric_analyses)
        disclosure_summary = {
            "fully_disclosed": 0,
            "partially_disclosed": 0,
            "not_disclosed": 0,
        }
        for analysis in unique_metric_analyses:
            status = _status_value(analysis)
            if status in disclosure_summary:
                disclosure_summary[status] += 1
            else:
                disclosure_summary["not_disclosed"] += 1

        overall_score = (
            disclosure_summary["fully_disclosed"]
            + 0.5 * disclosure_summary["partially_disclosed"]
        ) / max(total_unique_metrics, 1)

        needs_attention = [
            a
            for a in unique_metric_analyses
            if _status_value(a) in {"not_disclosed", "partially_disclosed"}
        ]
        priority_gaps = sorted(
            needs_attention,
            key=lambda a: 0 if _status_value(a) == "not_disclosed" else 1,
        )

        topic_counts: Dict[str, int] = {}
        for analysis in needs_attention:
            topic = _topic_key(analysis)
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        top_topics = sorted(topic_counts.items(), key=lambda item: item[1], reverse=True)[:3]

        lines: List[str] = [
            "# ESG Compliance Assessment Report",
            "",
            "## Executive Summary",
            f"- **Overall compliance score**: {overall_score:.2%}",
            (
                f"- **Coverage**: {disclosure_summary['fully_disclosed']} fully disclosed, "
                f"{disclosure_summary['partially_disclosed']} partially disclosed, "
                f"{disclosure_summary['not_disclosed']} not disclosed out of "
                f"{_metric_phrase(total_unique_metrics)}."
            ),
            (
                f"- **Priority workload**: {_metric_phrase(len(needs_attention))} "
                f"{'needs' if len(needs_attention) == 1 else 'need'} follow-up "
                f"({disclosure_summary['not_disclosed']} missing, "
                f"{disclosure_summary['partially_disclosed']} incomplete)."
            ),
        ]
        if top_topics:
            topic_text = "; ".join(f"{topic}: {count}" for topic, count in top_topics)
            lines.append(f"- **Largest disclosure gaps by topic/type**: {topic_text}.")

        lines.extend(
            [
                "",
                "## Report Overview",
                f"- **Report ID**: {assessment.report_id}",
                f"- **Assessment Date**: {assessment.assessment_date.strftime('%Y-%m-%d %H:%M:%S')}",
                f"- **Analyzed Metrics**: {total_unique_metrics}",
                f"- **Overall Compliance Score**: {overall_score:.2%}",
                "",
                "## Disclosure Status Statistics",
                "",
                "| Disclosure Status | Count | Percentage |",
                "|---|---:|---:|",
                (
                    f"| Fully Disclosed | {disclosure_summary['fully_disclosed']} | "
                    f"{_pct(disclosure_summary['fully_disclosed'], total_unique_metrics)} |"
                ),
                (
                    f"| Partially Disclosed | {disclosure_summary['partially_disclosed']} | "
                    f"{_pct(disclosure_summary['partially_disclosed'], total_unique_metrics)} |"
                ),
                (
                    f"| Not Disclosed | {disclosure_summary['not_disclosed']} | "
                    f"{_pct(disclosure_summary['not_disclosed'], total_unique_metrics)} |"
                ),
                "",
            ]
        )

        if priority_gaps:
            lines.extend(
                [
                    "## Priority Gaps",
                    "",
                    "| Priority | Status | Code | Metric | Evidence/Page | Recommended Action |",
                    "|---:|---|---|---|---|---|",
                ]
            )
            for idx, analysis in enumerate(priority_gaps[:10], start=1):
                evidence_page = f"{_evidence_text(analysis)} / page {_page_text(analysis)}"
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            str(idx),
                            _table_cell(_status_label(_status_value(analysis)), 80),
                            _table_cell(_metric_code(analysis), 80),
                            _table_cell(_metric_name(analysis), 220),
                            _table_cell(evidence_page, 120),
                            _table_cell(_first_suggestion(analysis), 260),
                        ]
                    )
                    + " |"
                )
            if len(priority_gaps) > 10:
                lines.append("")
                lines.append(f"_Showing top 10 of {len(priority_gaps)} metrics needing follow-up._")
            lines.append("")

        lines.extend(
            [
                "## Disclosure Matrix",
                "",
                "| Status | Code | Metric | Value | Page | Evidence Segments |",
                "|---|---|---|---|---:|---|",
            ]
        )
        for analysis in unique_metric_analyses:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_cell(_status_label(_status_value(analysis)), 80),
                        _table_cell(_metric_code(analysis), 80),
                        _table_cell(_metric_name(analysis), 240),
                        _table_cell(_value_text(analysis), 120),
                        _table_cell(_page_text(analysis), 40),
                        _table_cell(_evidence_text(analysis), 160),
                    ]
                )
                + " |"
            )
        lines.extend(["", "## Detailed Analysis Results", ""])

        status_sections = [
            ("partially_disclosed", "Partially Disclosed - Needs Follow-up"),
            ("not_disclosed", "Not Disclosed - Missing Metrics"),
            ("fully_disclosed", "Fully Disclosed - Supported Metrics"),
        ]
        for status, title in status_sections:
            status_metrics = [a for a in unique_metric_analyses if _status_value(a) == status]
            if not status_metrics:
                continue
            lines.extend([f"### {title}", ""])
            for analysis in status_metrics:
                lines.append(f"#### {_metric_name(analysis)} ({_metric_code(analysis)})")
                lines.append(f"- **Status**: {_status_label(status)}")
                _append_field(lines, "Topic/Type", _topic_key(analysis))
                _append_field(lines, "Expected unit", getattr(analysis, "unit", ""))
                lines.append(f"- **Reported value**: {_value_text(analysis)}")
                lines.append(f"- **Page**: {_page_text(analysis)}")
                if _evidence_text(analysis) != "-":
                    lines.append(f"- **Evidence segments**: {_evidence_text(analysis)}")
                _append_field(lines, "Evidence context", getattr(analysis, "context", ""), limit=700)
                _append_field(lines, "Analysis reasoning", getattr(analysis, "reasoning", ""), limit=1200)
                _append_suggestions(lines, analysis)
                lines.append("")

        lines.extend(["## Improvement Recommendations Summary", ""])
        if needs_attention:
            lines.append(
                "1. Prioritize metric-level remediation for missing and partial "
                "disclosures before broad narrative edits."
            )
            if disclosure_summary["not_disclosed"]:
                missing = [
                    f"{_metric_code(a)} {_metric_name(a)}"
                    for a in priority_gaps
                    if _status_value(a) == "not_disclosed"
                ][:5]
                lines.append(
                    "2. Add explicit disclosures for missing metrics: "
                    + "; ".join(missing)
                    + "."
                )
            else:
                lines.append(
                    "2. No fully missing metrics were detected; focus on strengthening partial disclosures."
                )
            if disclosure_summary["partially_disclosed"]:
                lines.append(
                    "3. For partially disclosed metrics, add the missing value, "
                    "unit, reporting boundary, period, and evidence page reference."
                )
            else:
                lines.append(
                    "3. Keep current fully disclosed metrics traceable by preserving "
                    "value, unit, period, and source page references."
                )
            lines.append(
                "4. Align future report sections with framework metric codes so "
                "retrieval and reviewer traceability are stronger."
            )
        else:
            lines.append(
                "All analyzed metrics are fully disclosed. Maintain the current "
                "metric-code mapping, evidence citations, and reporting-period "
                "consistency in future reports."
            )

        return "\n".join(lines).rstrip() + "\n"
