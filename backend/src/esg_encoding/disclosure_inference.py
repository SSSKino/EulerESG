"""
Disclosure Inference Engine - Use LLM to analyze ESG metric disclosure status
"""

from asyncio.subprocess import Process
import json
from typing import List, Dict, Optional, Union
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
        "has_disclosure": {"type": "boolean"},
        "disclosure_quality": {"type": "string"},
        "reasoning": {"type": "string"},
        "value": {"type": ["number", "null"]},
        "page": {"type": ["integer", "null"]},
        "evidence_segment_id": {"type": ["string", "null"]},
        "evidence_quote": {"type": ["string", "null"]},
        "specific_data_found": {"type": ["string", "null"]},
        "improvement_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["reasoning"],
    "additionalProperties": False,
}

# Stored in assessment JSON when no metric-specific number is disclosed / extractable.
COMPLIANCE_VALUE_NA = "n/a"


def _parse_llm_numeric_value_only(raw: object) -> Optional[Union[int, float]]:
    """
    Accept only a JSON number or a string that is *entirely* one numeric literal
    (optional commas, optional trailing %). Never scrape the first digit from a longer sentence.
    """
    if raw is None or raw is False:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")):  # NaN / inf
            return None
        return raw
    s = str(raw).strip()
    if not s or s.lower() in ("n/a", "na", "none", "null", "-", "—", "--"):
        return None
    s2 = s.replace(",", "").strip()
    if s2.endswith("%"):
        s2 = s2[:-1].strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", s2):
        return None
    try:
        n = float(s2)
    except ValueError:
        return None
    if n == int(n) and abs(n) < 1e15:
        return int(n)
    return n


def _finalize_compliance_value_field(
    found_numeric: Optional[Union[int, float]],
) -> Union[int, float, str]:
    if isinstance(found_numeric, (int, float)) and not isinstance(found_numeric, bool):
        return found_numeric
    return COMPLIANCE_VALUE_NA


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
        
        print(f"DEBUG: config: {self.config}")

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
                                definition=(getattr(metric, 'definition', None) or ''),
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
                            definition=(getattr(metric, 'definition', None) or ''),
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
                        # Framework display fields
                        category=getattr(metric, 'sasb_category', ''),
                        topic=(getattr(metric, 'sasb_topic', None) or ''),
                        unit=getattr(metric, 'unit', ''),
                        type=getattr(metric, 'sasb_type', ''),
                        definition=(getattr(metric, 'definition', None) or ''),
                        value=COMPLIANCE_VALUE_NA,
                        context=None,
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
        # 优化点：不仅传命中的 segment，还补充前后相邻 + 同页相邻表格/正文，
        # 以减少标题/定义/数值被切散时的误判。
        relevant_segments = []
        evidence_segment_ids = []
        segment_metadata = []

        TOP_EVIDENCE = 8
        MAX_EVIDENCE_WITH_CONTEXT = 14
        contextual_results = self._augment_results_with_adjacent_segments(
            report_content=report_content,
            primary_results=retrieval_result.combined_results[:TOP_EVIDENCE],
            max_items=MAX_EVIDENCE_WITH_CONTEXT,
        )

        for result, relation in contextual_results:
            segment = None
            try:
                segment = self._get_segment_by_id(report_content, result.segment_id)
            except Exception:
                segment = None

            content = None
            page_number = None
            if segment is not None and getattr(segment, "content", None):
                content = segment.content
                page_number = getattr(segment, "page_number", None)
            else:
                content = getattr(result, "content", None)
                page_number = getattr(result, "page_number", None)

            if content:
                relevant_segments.append(content)
                evidence_segment_ids.append(result.segment_id)

                metadata = {
                    "segment_id": result.segment_id,
                    "page_number": page_number,
                    "score": getattr(result, "score", 0),
                    "retrieval_type": f"{getattr(result, 'retrieval_type', '')}:{relation}",
                    "matched_keywords": getattr(result, "matched_keywords", None),
                }
                segment_metadata.append(metadata)

# Build prompt containing tag information
        prompt = self._build_analysis_prompt(
            retrieval_result.metric_name,
            retrieval_result.metric_id,
            relevant_segments,
            segment_metadata,
            metric_unit=(getattr(metric, "unit", None) or "") if metric else "",
            metric_description=(getattr(metric, "definition", None) or "").strip()
            if metric
            else "",
        )
        
        try:
            json_example = """
            {
              "has_disclosure": true,
              "disclosure_quality": "high",
              "reasoning": "The report explicitly states the total energy consumption.",
              "value": 511,
              "page": 23,
              "evidence_segment_id": "SEG_000123",
              "evidence_quote": "Total energy consumption was 511 GJ in 2024...",
              "specific_data_found": "511 GJ (2024)",
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
You are a professional ESG compliance analysis expert.
For the JSON output, the "value" field must be ONLY a number when the report gives a
single quantitative figure that directly answers THIS exact metric (same substance/KPI).
Never put narrative text in "value". Never pick a number that belongs to a different
pollutant, indicator, or line item than the metric asks for. If unsure, use null for
value and explain in "reasoning" / "specific_data_found".
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

            # Classify disclosure status
            disclosure_status = self._classify_disclosure_status(llm_result)
            
            # -----------------------------
            # Extract value / page / context
            # -----------------------------
            # Only the top-level JSON "value" may become a stored number — never mine
            # specific_data_found / evidence_quote (avoids wrong pollutant or table cell).
            found_numeric = _parse_llm_numeric_value_only(llm_result.get("value", None))
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

            # 4) Context: prefer a short quote from LLM; fallback to specific_data_found; then best segment excerpt
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
                        found_context = excerpt[:600]

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
    
    def _build_analysis_prompt(
        self,
        metric_name: str,
        metric_id: str,
        segments: List[str],
        segment_metadata: List[Dict] = None,
        metric_unit: str = "",
        metric_description: str = "",
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
        desc_line = (
            f"- Metric definition / guidance: {metric_description}\n"
            if (metric_description or "").strip()
            else ""
        )

        prompt = f"""As a professional ESG compliance analysis expert, please conduct a unified disclosure analysis for the following metric.

Metric Information:
- Metric Name: {metric_name}
- Metric Code: {metric_id}
{unit_line}{desc_line}
All Related Retrieved Segments (with segment_id + tag info):
{segments_text if segments_text else "No related segments found"}

Please conduct a unified analysis based on ALL segments above (do not analyze each segment separately).

Output Rules (must follow):
1) Respond ONLY with a JSON object. No markdown, no backticks.
2) If you output a page, it MUST be one of the Tag Info pages shown above.
3) If you output evidence_segment_id, it MUST be one of the Segment IDs shown above.
4) "value" must be a single JSON NUMBER only when the report clearly discloses one quantitative figure that **directly answers this exact metric** (same substance, scope, and line item the metric asks for). Example: if the metric is about NOx emissions specifically, the number must be NOx — not SO2, not total air emissions, and not an unrelated table cell.
5) If the metric lists several sub-items (e.g. "(1) NOx"), use a number only when the text explicitly breaks out that sub-item. If the report only gives combined totals or another pollutant, set "value" to null and explain in "reasoning" why the metric-specific figure is missing.
6) Do **not** grab the first number appearing in a paragraph if that number could belong to a different KPI than this metric. When in doubt, use null for "value".
7) For purely qualitative / narrative disclosure (no clear single number for this metric), set "value" to null. Put the narrative and interpretation in "reasoning" and supporting detail in "specific_data_found".
8) If there is no relevant disclosure for this metric, set has_disclosure false, disclosure_quality "none", and "value" to null.
9) evidence_quote must be a short excerpt (<= 180 chars) that supports your conclusion (the numeric figure if any, or the qualitative point).

Return JSON format:
{{
  "has_disclosure": true/false,
  "disclosure_quality": "high/medium/low/none",
  "reasoning": "Comprehensive reasoning based on all segments",
  "value": <number|null>,
  "page": <int|null>,
  "evidence_segment_id": "<segment_id|null>",
  "evidence_quote": "<short quote|null>",
  "specific_data_found": "<detailed context, may include unit/year if present>",
  "improvement_suggestions": ["Suggestion 1", "Suggestion 2", ...]
}}
"""
        return prompt
    
    def _augment_results_with_adjacent_segments(
        self,
        report_content: ReportContent,
        primary_results: List[RetrievalResult],
        max_items: int = 14,
    ) -> List[tuple[RetrievalResult, str]]:
        """
        为命中结果补充前后相邻 segment，以及同页最近的异类型 segment（表格/正文）。
        """
        if not primary_results:
            return []

        ordered_segments = list(getattr(report_content.document_content, 'segments', None) or [])
        if not ordered_segments:
            return [(r, 'primary') for r in primary_results[:max_items]]

        id_to_index = {seg.segment_id: idx for idx, seg in enumerate(ordered_segments)}
        id_to_segment = {seg.segment_id: seg for seg in ordered_segments}
        primary_map = {r.segment_id: r for r in primary_results}
        augmented: List[tuple[RetrievalResult, str]] = []
        seen: set[str] = set()

        def add_result(result: RetrievalResult, relation: str):
            if result.segment_id in seen or len(augmented) >= max_items:
                return
            seen.add(result.segment_id)
            augmented.append((result, relation))

        def is_table_segment(seg) -> bool:
            sid = getattr(seg, 'segment_id', '') or ''
            content = getattr(seg, 'content', '') or ''
            return '_T' in sid or content.startswith('**[表格')

        def make_context_result(seg, base: RetrievalResult) -> RetrievalResult:
            return RetrievalResult(
                segment_id=seg.segment_id,
                content=seg.content,
                page_number=getattr(seg, 'page_number', None),
                score=max(float(getattr(base, 'score', 0.0) or 0.0) * 0.92, 0.0),
                retrieval_type=getattr(base, 'retrieval_type', 'context'),
                matched_keywords=list(getattr(base, 'matched_keywords', None) or []),
                metric_id=getattr(base, 'metric_id', ''),
            )

        for result in primary_results:
            add_result(result, 'primary')
            idx = id_to_index.get(result.segment_id)
            if idx is None:
                continue
            base_seg = id_to_segment.get(result.segment_id)
            if base_seg is None:
                continue
            page_no = getattr(base_seg, 'page_number', None)

            # 前后 1 段
            for offset, relation in [(-1, 'prev'), (1, 'next')]:
                nidx = idx + offset
                if 0 <= nidx < len(ordered_segments):
                    nseg = ordered_segments[nidx]
                    if getattr(nseg, 'page_number', None) == page_no:
                        add_result(make_context_result(nseg, result), relation)

            # 同页最近的异类型 segment（例如正文命中时补最近表格）
            base_is_table = is_table_segment(base_seg)
            for direction, relation in [(-1, 'same_page_adjacent'), (1, 'same_page_adjacent')]:
                for step in range(1, 4):
                    nidx = idx + direction * step
                    if not (0 <= nidx < len(ordered_segments)):
                        break
                    nseg = ordered_segments[nidx]
                    if getattr(nseg, 'page_number', None) != page_no:
                        break
                    if is_table_segment(nseg) != base_is_table:
                        add_result(make_context_result(nseg, result), relation)
                        break

            if len(augmented) >= max_items:
                break

        return augmented[:max_items]

    def _classify_disclosure_status(self, llm_response: dict) -> DisclosureStatus:
        """
        Perform three-category classification based on LLM analysis results
        
        Args:
            llm_response: JSON result returned by LLM
            
        Returns:
            DisclosureStatus: Disclosure status
        """
        has_disclosure = llm_response.get("has_disclosure", False)
        quality = llm_response.get("disclosure_quality", "none")
        
        if not has_disclosure or quality == "none":
            return DisclosureStatus.NOT_DISCLOSED
        elif quality == "high":
            return DisclosureStatus.FULLY_DISCLOSED
        else:  # medium or low
            return DisclosureStatus.PARTIALLY_DISCLOSED
    
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
        # Deduplicate metric analyses based on metric_id
        seen_metric_ids = set()
        unique_metric_analyses = []
        for analysis in assessment.metric_analyses:
            if analysis.metric_id not in seen_metric_ids:
                unique_metric_analyses.append(analysis)
                seen_metric_ids.add(analysis.metric_id)
        
        # Update assessment with deduplicated analyses
        total_unique_metrics = len(unique_metric_analyses)
        
        # Recalculate disclosure summary based on unique metrics
        disclosure_summary = {"fully_disclosed": 0, "partially_disclosed": 0, "not_disclosed": 0}
        for analysis in unique_metric_analyses:
            if analysis.disclosure_status.value == "fully_disclosed":
                disclosure_summary["fully_disclosed"] += 1
            elif analysis.disclosure_status.value == "partially_disclosed":
                disclosure_summary["partially_disclosed"] += 1
            else:
                disclosure_summary["not_disclosed"] += 1
        
        # Recalculate overall score
        overall_score = (disclosure_summary["fully_disclosed"] + 0.5 * disclosure_summary["partially_disclosed"]) / max(total_unique_metrics, 1)
        
        report = f"""# ESG Compliance Assessment Report

## Report Overview
- **Report ID**: {assessment.report_id}
- **Assessment Date**: {assessment.assessment_date.strftime("%Y-%m-%d %H:%M:%S")}
- **Analyzed Metrics**: {total_unique_metrics}
- **Overall Compliance Score**: {overall_score:.2%}

## Disclosure Status Statistics

| Disclosure Status | Count | Percentage |
|---------|------|------|
| Fully Disclosed | {disclosure_summary["fully_disclosed"]} | {disclosure_summary["fully_disclosed"] / max(total_unique_metrics, 1):.1%} |
| Partially Disclosed | {disclosure_summary["partially_disclosed"]} | {disclosure_summary["partially_disclosed"] / max(total_unique_metrics, 1):.1%} |
| Not Disclosed | {disclosure_summary["not_disclosed"]} | {disclosure_summary["not_disclosed"] / max(total_unique_metrics, 1):.1%} |

## Detailed Analysis Results

"""
        
        # Group by disclosure status using deduplicated metrics
        for status in [DisclosureStatus.NOT_DISCLOSED, DisclosureStatus.PARTIALLY_DISCLOSED, DisclosureStatus.FULLY_DISCLOSED]:
            status_metrics = [a for a in unique_metric_analyses if a.disclosure_status == status]
            
            if status_metrics:
                status_name = {
                    DisclosureStatus.FULLY_DISCLOSED: "✅ Fully Disclosed",
                    DisclosureStatus.PARTIALLY_DISCLOSED: "⚠️ Partially Disclosed",
                    DisclosureStatus.NOT_DISCLOSED: "❌ Not Disclosed"
                }[status]
                
                report += f"### {status_name}\n\n"
                
                for analysis in status_metrics:
                    report += f"#### {analysis.metric_name} ({analysis.metric_code})\n"
                    report += f"- **Analysis Reasoning**: {analysis.reasoning}\n"
                    
                    if analysis.evidence_segments:
                        report += f"- **Evidence Segments**: {', '.join(analysis.evidence_segments[:3])}\n"
                    
                    if analysis.improvement_suggestions:
                        report += "- **Improvement Suggestions**:\n"
                        for suggestion in analysis.improvement_suggestions:
                            report += f"  - {suggestion}\n"
                    
                    report += "\n"
        
        report += """## Improvement Recommendations Summary

Based on the analysis results, it is recommended to prioritize improvements in the following areas:
1. For undisclosed metrics, recommend adding relevant content in the next report
2. For partially disclosed metrics, recommend providing more detailed quantitative data
3. Recommend adopting standardized ESG reporting frameworks to improve completeness and comparability of disclosures
"""
        
        return report
