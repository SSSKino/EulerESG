from __future__ import annotations

import html
import re
from functools import lru_cache
from typing import Iterable, List

from sasb_ocr_extractor.utils.text import SASB_ANY_CODE_RE, clean_text, single_line


class TopicSummaryExtractor:
    """Extract SASB "Topic Summary" text and attach it to metrics by topic.

    SASB standards usually structure each disclosure topic section as:

        <Topic name>
        Topic Summary
        <summary paragraphs>
        Metrics
        <metric code technical protocol>

    The extractor uses known topics from Table 1 to avoid grabbing table-of-contents
    entries or unrelated headings. It is deliberately independent from the table
    parser so the OCR reader and section parser remain easy to manage.
    """

    def __init__(self, full_text: str, known_topics: Iterable[str] | None = None):
        self.full_text = clean_text(full_text)
        self.known_topics = self._normalise_topics(known_topics or [])

    def _normalise_topics(self, topics: Iterable[str]) -> List[str]:
        values: List[str] = []
        seen = set()
        for topic in topics:
            value = clean_text(topic)
            if not value:
                continue
            key = re.sub(r"\s+", " ", value).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
        # Longer topics first prevents a short prefix topic from stealing a section.
        values.sort(key=len, reverse=True)
        return values

    @lru_cache(maxsize=256)
    def summary_for_topic(self, topic: str) -> str:
        topic = clean_text(topic)
        if not topic:
            return ""

        # Activity Metrics do not have disclosure-topic summaries.
        if topic.lower() in {"activity", "activity metric", "activity metrics"}:
            return ""

        match = self._find_topic_summary_heading(topic)
        if not match:
            return ""

        body_start = match.end()
        body_end = self._find_summary_end(body_start, current_topic=topic)
        summary = self.full_text[body_start:body_end]
        return self._clean_summary(summary, topic)

    def _find_topic_summary_heading(self, topic: str) -> re.Match[str] | None:
        topic_pattern = self._flexible_phrase_pattern(topic)
        heading_prefix = r"\s*(?:#{1,6}\s*)?"

        # Preferred: topic heading followed directly by "Topic Summary".
        # PaddleOCR-VL commonly emits Markdown headings such as:
        #   # <Topic>
        #   ## Topic Summary
        # Keep the matcher tolerant of those heading markers so the final JSON
        # uses the Topic Summary under each actual topic section, not a blank
        # value caused by failing to recognise Markdown output.
        direct = re.compile(
            rf"(?:^|\n){heading_prefix}(?:{topic_pattern})\s*\n+"
            rf"{heading_prefix}Topic\s+Summary\b",
            flags=re.I,
        )
        matches = list(direct.finditer(self.full_text))
        if matches:
            # Prefer the later match to avoid table-of-contents text if OCR repeats headings.
            return matches[-1]

        # Fallback for OCR that places heading and Topic Summary on one line.
        loose = re.compile(
            rf"(?:^|\n){heading_prefix}(?:{topic_pattern})\s+"
            rf"(?:Topic\s+Summary)\b",
            flags=re.I,
        )
        matches = list(loose.finditer(self.full_text))
        return matches[-1] if matches else None

    def _find_summary_end(self, body_start: int, current_topic: str) -> int:
        sub = self.full_text[body_start:]
        candidates: List[int] = []

        end_patterns = [
            # The explicit Metrics heading normally follows Topic Summary.
            r"\n\s*(?:#{1,6}\s*)?Metrics\s*\n",
            # If the Metrics heading is lost, the first technical-protocol code starts next.
            r"\n\s*[A-Z]{2}-[A-Z]{2}-(?:\d{3}[a-z]\.\d+|000\.[A-Z])\s*\.",
            # A new topic section.
            r"\n\s*(?:#{1,6}\s*)?[A-Z][A-Za-z0-9 &,/'()\-]{3,}\s*\n\s*(?:#{1,6}\s*)?Topic\s+Summary\b",
            # A new table/major section.
            r"\n\s*(?:#{1,6}\s*)?Table\s+\d+\.",
        ]
        for pat in end_patterns:
            match = re.search(pat, sub, flags=re.I)
            if match and match.start() > 20:
                candidates.append(match.start())

        # Known-topic guard: find the next different known topic heading.
        current_key = single_line(current_topic).lower()
        for topic in self.known_topics:
            if single_line(topic).lower() == current_key:
                continue
            topic_pattern = self._flexible_phrase_pattern(topic)
            match = re.search(
                rf"\n\s*(?:#{{1,6}}\s*)?(?:{topic_pattern})\s*\n+"
                rf"\s*(?:#{{1,6}}\s*)?Topic\s+Summary\b",
                sub,
                flags=re.I,
            )
            if match and match.start() > 20:
                candidates.append(match.start())

        if candidates:
            return body_start + min(candidates)

        # Conservative fallback cap. Topic summaries are normally short.
        return min(len(self.full_text), body_start + 2500)

    def _clean_summary(self, summary: str, topic: str) -> str:
        value = self._html_to_text(summary)
        value = self._normalise_latex_tokens(value)
        value = clean_text(value)
        if not value:
            return ""

        # Remove common OCR leftovers and running footers without discarding real prose.
        value = re.sub(r"(?i)^\s*(?:#{1,6}\s*)?Topic\s+Summary\s*", "", value).strip()
        # Remove a duplicated topic-heading line only when OCR repeats the heading
        # as its own line. Do not remove prose such as "Product safety is...".
        lines = value.splitlines()
        if lines and re.fullmatch(self._flexible_phrase_pattern(topic), lines[0].strip(), flags=re.I):
            value = "\n".join(lines[1:]).strip()
        value = re.sub(
            r"(?im)^\s*SUSTAINABILITY\s+ACCOUNTING\s+STANDARD\s*\|.*$",
            "",
            value,
        )
        value = re.sub(r"(?im)^\s*©\s*\d{4}.*$", "", value)
        value = re.sub(r"(?im)^\s*sasb\.org\s*$", "", value)
        value = re.sub(r"\$\s*(?:\{\}\s*)?\^\s*\{?\d+[A-Za-z,]*\}?\s*\$", " ", value)
        value = re.sub(r"\$\s*", "", value)

        # If a code leaked into the summary, cut at that code.
        code_match = SASB_ANY_CODE_RE.search(value)
        if code_match and code_match.start() > 100:
            value = value[: code_match.start()]

        return clean_text(value)

    def _html_to_text(self, text: str) -> str:
        value = html.unescape(str(text or ""))
        value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
        value = re.sub(r"(?i)</\s*(p|div|h[1-6]|tr|table)\s*>", "\n", value)
        value = re.sub(r"(?i)</\s*(td|th)\s*>", "\t", value)
        value = re.sub(r"(?i)<\s*(p|div|h[1-6]|tr|table|td|th)[^>]*>", "\n", value)
        value = re.sub(r"<[^>]+>", " ", value)
        return value

    def _normalise_latex_tokens(self, text: str) -> str:
        replacements = [
            (r"\$?\s*CO_\{2\}-e\s*\$?", "CO₂-e"),
            (r"\$?\s*CO_\{2\}\s*\$?", "CO₂"),
            (r"\$?\s*gCO\s*\$?\s*_\{2\}\s*\$?", "gCO₂"),
            (r"\$?\s*NO_\{x\}\s*\$?", "NOx"),
            (r"\$?\s*N_\{2\}O\s*\$?", "N2O"),
            (r"\$?\s*SO_\{x\}\s*\$?", "SOx"),
            (r"\$?\s*PM_\{10\}\s*\$?", "PM10"),
            (r"\$?\s*m\^\{2\}\s*\$?", "m²"),
            (r"\$?\s*m\^\{3\}\s*\$?", "m³"),
        ]
        value = str(text or "")
        for pattern, replacement in replacements:
            value = re.sub(pattern, replacement, value, flags=re.I)
        return value

    def _flexible_phrase_pattern(self, phrase: str) -> str:
        # Match OCR line breaks and multiple spaces between words, while keeping punctuation optional-ish.
        tokens = re.findall(r"[A-Za-z0-9]+", clean_text(phrase))
        if not tokens:
            return re.escape(clean_text(phrase))
        # Allow spaces, line breaks, ampersands, hyphens and OCR punctuation between words.
        return r"[\s\W_]+".join(re.escape(token) for token in tokens)
