from __future__ import annotations

import html
import re
from functools import lru_cache

from sasb_ocr_extractor.utils.text import SASB_ANY_CODE_RE, clean_text


class DefinitionExtractor:
    """Extract technical protocol text for a SASB code."""

    def __init__(self, full_text: str):
        self.full_text = clean_text(full_text)

    @lru_cache(maxsize=512)
    def definition_for_code(self, code: str) -> str:
        code = clean_text(code)
        if not code:
            return ""

        start_match = self._find_protocol_heading(code)
        if not start_match:
            return ""

        sub = self.full_text[start_match.start():]
        end_idx = self._find_definition_end(sub, code)
        return self._clean_definition(sub[:end_idx])

    def _find_protocol_heading(self, code: str) -> re.Match[str] | None:
        """Prefer actual technical-protocol headings over summary-table cells.

        PaddleOCR-VL can emit Table 1 as raw HTML after the protocol pages. If we
        simply pick the last occurrence of a code, definitions can become polluted
        with ``<td>``/``</tr>`` table markup. A protocol heading is normally a line
        beginning with the code, optionally preceded by Markdown heading marks.
        """
        heading = re.compile(
            rf"(?:^|\n)\s*(?:#{{1,6}}\s*)?{re.escape(code)}\s*\.\s+",
            flags=re.I,
        )
        matches = [m for m in heading.finditer(self.full_text) if not self._inside_html_table(m.start())]
        if matches:
            # Use the first protocol heading. Later occurrences are usually notes,
            # duplicated table cells, or activity-summary tables.
            return matches[0]

        loose = re.compile(rf"(?:^|\n)\s*(?:#{{1,6}}\s*)?{re.escape(code)}\b", flags=re.I)
        matches = [m for m in loose.finditer(self.full_text) if not self._inside_html_table(m.start())]
        return matches[0] if matches else None

    def _inside_html_table(self, pos: int) -> bool:
        prefix = self.full_text[:pos]
        last_table = prefix.lower().rfind("<table")
        if last_table < 0:
            return False
        last_close = prefix.lower().rfind("</table>")
        return last_close < last_table

    def _find_definition_end(self, sub: str, code: str) -> int:
        candidates: list[int] = []

        # Next technical-protocol heading. Notes for the same code are retained.
        for match in re.finditer(
            r"\n\s*(?:#{1,6}\s*)?[A-Z]{2}-[A-Z]{2}-(?:\d{3}[a-z]\.\d+|000\.[A-Z])\s*\.\s+",
            sub[len(code):],
            flags=re.I,
        ):
            idx = len(code) + match.start()
            if idx > 80:
                candidates.append(idx)
                break

        # Topic boundary.
        match = re.search(
            r"\n\s*(?:#{1,6}\s*)?[A-Z][A-Za-z0-9 &,/'()\-]{3,}\s*\n+\s*(?:#{1,6}\s*)?Topic\s+Summary\b",
            sub,
            flags=re.I,
        )
        if match and match.start() > 200:
            candidates.append(match.start())

        # Summary/activity tables, front-matter repeats, and footer-like pages are not part of a protocol definition.
        for pat in [
            r"\n\s*(?:#{1,6}\s*)?SUSTAINABILITY\s+DISCLOSURE\s+TOPICS\s*&\s*METRICS\b",
            r"\n\s*(?:<div[^>]*>\s*)?Table\s+1\.\s*Sustainability\s+Disclosure\s+Topics\s*&\s*Metrics",
            r"\n\s*(?:<div[^>]*>\s*)?Table\s+2\.\s*Activity\s+Metrics",
            r"\n\s*Now\s+part\s+of\s+IFRS\s+Foundation\b",
            r"\n\s*(?:#{1,6}\s*)?Table\s+of\s+Contents\b",
            r"\n\s*INTRODUCTION\.{2,}",
            r"\n\s*Overview\s+of\s+SASB\s+Standards\.{2,}",
            r"\n\s*Use\s+of\s+the\s+Standards\.{2,}",
            r"\n\s*Industry\s+Description\.{2,}",
        ]:
            match = re.search(pat, sub, flags=re.I)
            if match and match.start() > 200:
                candidates.append(match.start())

        if candidates:
            return min(candidates)
        return min(len(sub), 9000)

    def _clean_definition(self, text: str) -> str:
        value = self._html_to_text(text)
        value = self._normalise_latex_tokens(value)
        value = re.sub(r"(?m)^\s*#{1,6}\s+", "", value)
        value = re.sub(r"\$\s*(?:\{\}\s*)?\^\s*\{?\d+[A-Za-z,]*\}?\s*\$", " ", value)
        value = re.sub(r"\$\s*", "", value)
        value = re.sub(r"(?im)^\s*SUSTAINABILITY\s+ACCOUNTING\s+STANDARD\s*\|.*$", "", value)
        value = re.sub(r"(?im)^\s*©\s*\d{4}.*$", "", value)
        value = re.sub(r"(?im)^\s*sasb\.org\s*$", "", value)
        value = re.sub(r"(?im)^\s*Now\s+part\s+of\s+IFRS\s+Foundation\s*$", "", value)
        value = re.sub(r"(?im)^\s*Table\s+of\s+Contents\s*$.*", "", value)
        value = re.sub(r"\s+([,.;:])", r"\1", value)
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
