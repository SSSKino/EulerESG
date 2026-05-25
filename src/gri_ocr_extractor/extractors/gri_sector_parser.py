from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from gri_ocr_extractor.config import ExtractionConfig
from gri_ocr_extractor.models import GriDisclosureRecord
from gri_ocr_extractor.utils.text import GRI_SECTOR_REF_RE, clean_text, single_line


_SECTION_HEADERS = {
    "management of the topic": "Management of the topic",
    "topic standard disclosures": "Topic Standard disclosures",
    "topic standard disclosure": "Topic Standard disclosure",
    "additional sector recommendations": "Additional sector recommendations",
    "additional sector recommendation": "Additional sector recommendation",
    "additional sector disclosures": "Additional sector disclosures",
    "additional sector disclosure": "Additional sector disclosure",
}


@dataclass
class TopicBlock:
    code_prefix: str
    topic: str
    text: str


class GriSectorParser:
    """Extract rows from GRI Sector Standards.

    This parser is generic for GRI Sector Standards with rows such as:
        Topic 14.1 Climate change
        Reporting on climate change
        STANDARD / DISCLOSURE / SECTOR STANDARD REF. NO.
        Disclosure 3-3 ... 14.1.1

    Output schema remains compatible with checked GRI Excel files:
        Standard, Metric, Code, Topic, Type, Value, Page, Context
    """

    def __init__(self, config: Optional[ExtractionConfig] = None):
        self.config = config or ExtractionConfig()

    @staticmethod
    def looks_like(text: str, source_path: Path | None = None) -> bool:
        sample = "\n".join([source_path.name if source_path else "", text[:10000]])
        if re.search(r"\bSector\s+Standard\b", sample, flags=re.I) and re.search(r"\bTopic\s+\d{1,2}\.\d{1,2}\b", text, flags=re.I):
            return True
        if re.search(r"\bSECTOR\s+STANDARD\s+REF\.?\s+NO\.?\b", text, flags=re.I):
            return True
        return False

    def parse(self, text: str, source_path: Path | None = None) -> List[GriDisclosureRecord]:
        full_text = self._prepare_text(text)
        sector_id = self._infer_sector_id(full_text, source_path)
        type_style = self._resolve_type_style(sector_id)
        blocks = self._topic_blocks(full_text, sector_id)
        records: List[GriDisclosureRecord] = []
        for block in blocks:
            records.extend(self._parse_topic_block(block, type_style))
        return self._deduplicate(records)

    def _prepare_text(self, text: str) -> str:
        value = clean_text(text)
        value = value.replace("\f", "\n")
        value = re.sub(r"(?m)^\s*\d+\s+GRI\s+\d{1,2}:.*$", "", value)
        value = re.sub(r"(?m)^\s*GRI\s+\d{1,2}:.*V\d\.\d\s*$", "", value)
        value = re.sub(r"(?m)^\s*References and resources\s*$", "\nReferences and resources\n", value, flags=re.I)
        return clean_text(value)

    def _infer_sector_id(self, text: str, source_path: Path | None) -> str:
        candidates = []
        if source_path:
            candidates.append(source_path.name)
        candidates.append(text[:6000])
        joined = "\n".join(candidates)
        # Prefer title-like signal: GRI 11/12/13/14: ... Sector.
        m = re.search(r"\bGRI\s*(\d{1,2})\s*[:\-]?\s*[^\n]{0,120}\bSector\b", joined, flags=re.I)
        if m:
            return m.group(1)
        m = re.search(r"\bTopic\s+(\d{1,2})\.\d{1,2}\b", text, flags=re.I)
        if m:
            return m.group(1)
        m = re.search(r"\b(\d{1,2})\.\d{1,2}\.\d{1,2}\b", text)
        if m:
            return m.group(1)
        return ""

    def _resolve_type_style(self, sector_id: str) -> str:
        style = (self.config.type_name_style or "auto").strip().lower()
        if style in {"singular", "plural"}:
            return style
        # Preserve checked-file compatibility for already validated sectors.
        if sector_id in {"11", "12"}:
            return "plural"
        return "singular"

    def _type_name(self, base: str, style: str) -> str:
        base_norm = base.strip().lower()
        if base_norm == "management":
            return "Management of the topic"
        if base_norm == "topic":
            return "Topic Standard disclosures" if style == "plural" else "Topic Standard disclosure"
        if base_norm == "additional_disclosure":
            return "Additional sector disclosures" if style == "plural" else "Additional sector disclosure"
        if base_norm == "additional_recommendation":
            return "Additional sector recommendations" if style == "plural" else "Additional sector recommendation"
        return base

    def _topic_blocks(self, text: str, sector_id: str) -> List[TopicBlock]:
        material_starts = list(re.finditer(r"(?i)This section comprises the likely material topics", text))
        if material_starts:
            # Use the last occurrence because the sentence can also appear in a preface or
            # duplicated extracted text before the real Section 2 body.
            text = text[material_starts[-1].start():]
        body_end = re.search(r"(?mi)^\s*(?:Glossary|Bibliography)\s*$", text)
        if body_end:
            text = text[: body_end.start()]

        sector_pat = re.escape(sector_id) if sector_id else r"\d{1,2}"
        topic_pat = re.compile(
            rf"(?m)^\s*(?:#+\s*)?Topic\s+({sector_pat}\.\d{{1,2}})\s+([^\n]+?)\s*$",
            flags=re.I,
        )
        starts = []
        seen = set()
        for m in topic_pat.finditer(text):
            code_prefix = m.group(1)
            topic = self._clean_topic(m.group(2))
            low = topic.lower()
            if len(topic) > 120 or ")" in topic or "," in topic or "see also" in low or "reporting on" in low:
                continue
            key = (m.start(), code_prefix)
            if key in seen:
                continue
            seen.add(key)
            starts.append((m.start(), m.end(), code_prefix, topic))

        # Some GRI sector standards contain a standalone pre-topic disclosure, e.g. GRI 14 mine-site disclosure 14.0.1.
        if self.config.include_mine_site and (sector_id == "14" or "14.0.1" in text) and re.search(r"\bMine-site disclosure\b", text, flags=re.I):
            m = re.search(r"(?m)^\s*(?:#+\s*)?Mine-site disclosure\s*$", text, flags=re.I)
            if m:
                starts.insert(0, (m.start(), m.end(), "14.0", "Mine-site disclosure"))

        # After trimming to the real Section 2 body, keep one occurrence per topic code.
        # If extraction still duplicates a heading, the later occurrence is normally the
        # actual body rather than the section overview list.
        by_code: dict[str, tuple[int, int, str, str]] = {}
        for start, end, code_prefix, topic in starts:
            by_code[code_prefix] = (start, end, code_prefix, topic)
        starts = sorted(by_code.values(), key=lambda x: x[0])

        blocks: List[TopicBlock] = []
        for idx, (_start, end, code_prefix, topic) in enumerate(starts):
            next_start = starts[idx + 1][0] if idx + 1 < len(starts) else len(text)
            block_text = text[end:next_start]
            if self.config.strict_topic_section:
                block_text = self._reporting_subsection(block_text)
            blocks.append(TopicBlock(code_prefix=code_prefix, topic=topic, text=block_text))
        return blocks

    def _reporting_subsection(self, block_text: str) -> str:
        m = re.search(r"(?mi)^\s*(?:#+\s*)?Reporting\s+on\s+[^\n]+\s*$", block_text)
        if m:
            sub = block_text[m.end():]
        else:
            alt = re.search(r"(?mi)^\s*(?:ADDITIONAL\s+SECTOR\s+DISCLOSURES|Additional\s+sector\s+disclosures?)\b", block_text)
            sub = block_text[alt.start():] if alt else block_text
        end = re.search(r"(?mi)^\s*(?:#+\s*)?References and resources\s*$", sub)
        if end:
            sub = sub[: end.start()]
        table_example = re.search(r"(?mi)^\s*Table\s+\d+\.\s*Example\s+template", sub)
        if table_example:
            sub = sub[: table_example.start()]
        return sub

    def _parse_topic_block(self, block: TopicBlock, type_style: str) -> List[GriDisclosureRecord]:
        lines = [self._clean_line(x) for x in block.text.splitlines()]
        lines = [x for x in lines if x]

        records: List[GriDisclosureRecord] = []
        code_re = re.compile(rf"\b{re.escape(block.code_prefix)}\.\d{{1,2}}\b")
        pending: List[str] = []
        current_type_base = ""
        current_standard = ""
        append_recommendation_to_last = False
        recommendation_lines: List[str] = []
        open_additional: Optional[dict[str, object]] = None

        def flush_recommendations() -> None:
            nonlocal recommendation_lines, append_recommendation_to_last
            if records and recommendation_lines:
                addition = self._format_recommendation(recommendation_lines)
                if addition:
                    records[-1].Metric = clean_text(records[-1].Metric + "\n" + addition)
            recommendation_lines = []
            append_recommendation_to_last = False

        def flush_open_additional() -> None:
            nonlocal open_additional
            if not open_additional:
                return
            parts = [str(x) for x in open_additional.get("parts", []) if str(x).strip()]
            metric = self._clean_metric("\n".join(parts))
            if metric:
                records.append(GriDisclosureRecord(
                    Standard="",
                    Metric=metric,
                    Code=str(open_additional["code"]),
                    Topic=block.topic,
                    Type=self._type_name("additional_disclosure", type_style),
                    Value=None,
                    Page=None,
                    Context=metric if self.config.include_context else None,
                ))
            open_additional = None

        for raw_line in lines:
            line = self._strip_page_noise(raw_line)
            if not line:
                continue

            if records and self._looks_like_standard_continuation(line) and records[-1].Standard and not re.search(r"\b(?:19|20)\d{2}\b", records[-1].Standard):
                records[-1].Standard = single_line(records[-1].Standard + " " + line)
                current_standard = records[-1].Standard
                continue

            section = self._section_header(line)
            if section:
                if section.lower().startswith("additional sector recommendation"):
                    flush_recommendations()
                    append_recommendation_to_last = True
                    recommendation_lines = [section]
                    continue
                flush_recommendations()
                flush_open_additional()
                current_type_base = self._base_for_section(section)
                pending = []
                continue

            if append_recommendation_to_last:
                if self._starts_new_row(line) or code_re.search(line) or self._looks_like_standard_start(line):
                    flush_recommendations()
                else:
                    recommendation_lines.append(line)
                    continue

            if self._is_table_header(line):
                continue

            code_matches = list(code_re.finditer(line))
            if not code_matches:
                if open_additional is not None:
                    open_additional.setdefault("parts", []).append(line)
                    continue
                pending.append(line)
                maybe_standard = self._extract_standard_from_text("\n".join(pending))
                if maybe_standard:
                    current_standard = maybe_standard
                continue

            start_pos = 0
            for match in code_matches:
                code = match.group(0)
                before = line[start_pos: match.start()].strip()
                if before:
                    pending.append(before)
                row_text = clean_text("\n".join(pending))
                pending = []

                inferred_base = current_type_base or self._infer_type_from_code_and_text(code, row_text)
                if inferred_base == "additional_disclosure" and "Disclosure" not in row_text:
                    flush_open_additional()
                    open_additional = {"code": code, "parts": [row_text]}
                else:
                    flush_open_additional()
                    record = self._record_from_row_text(
                        row_text=row_text,
                        code=code,
                        topic=block.topic,
                        type_base=current_type_base,
                        current_standard=current_standard,
                        type_style=type_style,
                    )
                    if record:
                        records.append(record)
                        if record.Standard:
                            current_standard = record.Standard
                start_pos = match.end()
            after = line[start_pos:].strip()
            if after:
                if open_additional is not None:
                    open_additional.setdefault("parts", []).append(after)
                else:
                    pending.append(after)

        flush_recommendations()
        flush_open_additional()
        return records

    def _record_from_row_text(
        self,
        row_text: str,
        code: str,
        topic: str,
        type_base: str,
        current_standard: str,
        type_style: str,
    ) -> Optional[GriDisclosureRecord]:
        row_text = self._clean_row_text(row_text)
        if not row_text:
            return None

        inferred_base = type_base or self._infer_type_from_code_and_text(code, row_text)
        standard = self._extract_standard_from_text(row_text)
        metric = self._metric_from_text(row_text, standard, inferred_base)

        if not standard and inferred_base in {"management", "topic"}:
            standard = current_standard
        if inferred_base == "additional_disclosure" and not self._starts_with_gri_standard(row_text):
            standard = ""

        metric = self._clean_metric(metric)
        if not metric:
            return None

        return GriDisclosureRecord(
            Standard=standard,
            Metric=metric,
            Code=code,
            Topic=topic,
            Type=self._type_name(inferred_base, type_style),
            Value=None,
            Page=None,
            Context=row_text if self.config.include_context else None,
        )

    def _infer_type_from_code_and_text(self, code: str, row_text: str) -> str:
        if code.endswith(".1") and "Disclosure 3-3" in row_text:
            return "management"
        if "Disclosure" in row_text:
            return "topic"
        return "additional_disclosure"

    def _base_for_section(self, section: str) -> str:
        section = section.lower()
        if section == "management of the topic":
            return "management"
        if section.startswith("topic standard"):
            return "topic"
        if section.startswith("additional sector disclosure"):
            return "additional_disclosure"
        if section.startswith("additional sector recommendation"):
            return "additional_recommendation"
        return ""

    def _section_header(self, line: str) -> str:
        key = re.sub(r"\s+", " ", line.strip().lower()).strip(" :")
        return _SECTION_HEADERS.get(key, "")

    def _is_table_header(self, line: str) -> bool:
        norm = re.sub(r"[^A-Za-z]+", " ", line).strip().lower()
        if not norm:
            return True
        headers = {
            "standard",
            "disclosure",
            "sector standard ref no",
            "ref no",
            "standard disclosure sector standard ref no",
        }
        return norm in headers or norm.startswith("standard disclosure sector")

    def _starts_new_row(self, line: str) -> bool:
        return bool(re.search(r"\bDisclosure\s+\d{1,3}-\d+\b", line, flags=re.I))

    def _looks_like_standard_start(self, line: str) -> bool:
        return bool(re.match(r"^GRI\s+\d{1,3}\s*:", line, flags=re.I))

    def _extract_standard_from_text(self, text: str) -> str:
        value = clean_text(text)
        m = re.search(
            r"\b(GRI\s+\d{1,3}\s*:\s*.+?\s+(?:19|20)\d{2})\b(?=\s+Disclosure|\s*$|\n)",
            value,
            flags=re.I | re.S,
        )
        if m:
            return re.sub(r"\s+", " ", single_line(m.group(1))).strip()
        m = re.search(r"\b(GRI\s+\d{1,3}\s*:\s*[^\n]+?)(?=\s+Disclosure\s+\d{1,3}-\d+)", value, flags=re.I)
        if m:
            return re.sub(r"\s+", " ", single_line(m.group(1))).strip()
        return ""

    def _starts_with_gri_standard(self, text: str) -> bool:
        return bool(re.match(r"\s*GRI\s+\d{1,3}\s*:", text, flags=re.I))

    def _looks_like_standard_continuation(self, line: str) -> bool:
        if re.search(r"\bDisclosure\s+\d{1,3}-\d+\b", line, flags=re.I):
            return False
        if GRI_SECTOR_REF_RE.search(line):
            return False
        if len(line) > 100:
            return False
        return bool(re.search(r"\b(?:19|20)\d{2}\b", line))

    def _metric_from_text(self, row_text: str, standard: str, type_base: str) -> str:
        value = clean_text(row_text)
        if standard:
            value_single = single_line(value)
            if value_single.startswith(standard):
                value = value_single[len(standard):].strip()
            else:
                value = clean_text(value.replace(standard, "", 1))
        value = re.sub(r"^GRI\s+\d{1,3}\s*:\s*", "", value, flags=re.I).strip()
        if type_base in {"management", "topic"}:
            m = re.search(r"\bDisclosure\s+\d{1,3}-\d+\b.*", value, flags=re.I | re.S)
            if m:
                return m.group(0)
        return value

    def _format_recommendation(self, lines: Iterable[str]) -> str:
        items = []
        for line in lines:
            line = self._clean_line(line)
            if not line or self._is_table_header(line):
                continue
            items.append(line)
        return clean_text("\n".join(items))

    def _clean_line(self, line: str) -> str:
        value = str(line or "").replace("\x0c", " ")
        value = value.replace("￾", "")
        value = value.replace("•", " • ")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\s+([,.;:])", r"\1", value)
        return value.strip()

    def _strip_page_noise(self, line: str) -> str:
        value = line.strip()
        value = re.sub(r"^\d+\s+GRI\s+\d{1,2}:.*$", "", value)
        value = re.sub(r"^GRI\s+\d{1,2}:.*V\d\.\d$", "", value)
        return value.strip()

    def _clean_topic(self, topic: str) -> str:
        value = single_line(topic)
        value = re.sub(r"\s+\d+$", "", value).strip()
        return value[:1].upper() + value[1:] if value else value

    def _clean_row_text(self, text: str) -> str:
        value = clean_text(text)
        value = re.sub(r"(?im)^STANDARD$", "", value)
        value = re.sub(r"(?im)^DISCLOSURE$", "", value)
        value = re.sub(r"(?im)^SECTOR STANDARD REF\. NO\.$", "", value)
        value = re.sub(r"(?im)^REF\. NO\.$", "", value)
        value = re.sub(r"(?im)^STANDARD\s+DISCLOSURE\s+SECTOR\s+STANDARD\s+REF\.\s*NO\.?$", "", value)
        return clean_text(value)

    def _clean_metric(self, metric: str) -> str:
        value = clean_text(metric)
        value = re.sub(r"\s+", " ", value)
        value = value.replace(" • ", "\n• ")
        value = re.sub(r"\n\s*\n+", "\n", value)
        return value.strip()

    def _deduplicate(self, records: List[GriDisclosureRecord]) -> List[GriDisclosureRecord]:
        seen = set()
        out = []
        for rec in records:
            key = (rec.Code, rec.Topic, rec.Type, rec.Metric)
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
        return out
