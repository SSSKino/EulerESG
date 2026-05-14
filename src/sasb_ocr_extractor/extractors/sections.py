from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from sasb_ocr_extractor.utils.text import clean_text


@dataclass
class SectionBlock:
    type: str
    text: str


class SasbSectionLocator:
    """Locate Table 1 and optional Table 2 blocks in OCR text/Markdown."""

    TABLE1_PAT = re.compile(
        r"(?:SUSTAINABILITY\s+DISCLOSURE\s+TOPICS\s*&\s*METRICS|"
        r"Table\s+1\.\s*Sustainability\s+Disclosure\s+Topics\s*&\s*Metrics)",
        flags=re.I,
    )
    TABLE2_PAT = re.compile(r"Table\s+2\.\s*Activity\s+Metrics|\bActivity\s+Metrics\b", flags=re.I)

    def locate(self, text: str, include_activity: bool = True) -> List[SectionBlock]:
        full = clean_text(text)
        table1 = self._extract_table1(full)
        blocks: List[SectionBlock] = []
        if table1:
            blocks.append(SectionBlock(type="Sustainability Disclosure Topics & Metrics", text=table1))
        if include_activity:
            table2 = self._extract_table2(full)
            if table2:
                blocks.append(SectionBlock(type="Activity Metrics", text=table2))
        return blocks

    def _extract_table1(self, text: str) -> str:
        # Prefer the actual table caption over the table-of-contents mention.
        caption_pat = re.compile(r"Table\s+1\.\s*Sustainability\s+Disclosure\s+Topics\s*&\s*Metrics", flags=re.I)
        start = caption_pat.search(text)
        if not start:
            # Fall back to the last matching heading, because early pages can contain a TOC entry.
            matches = list(self.TABLE1_PAT.finditer(text))
            if not matches:
                return ""
            start = matches[-1] if len(matches) > 1 else matches[0]
        sub = text[start.start():]
        end = self.TABLE2_PAT.search(sub)
        if end:
            return clean_text(sub[: end.start()])
        # If Table 2 was not found, stop at the first technical-protocol-like heading.
        fallback_end = re.search(r"\n\s*[A-Z][A-Za-z &,'/-]{3,}\n\s*Topic Summary\b", sub, flags=re.I)
        return clean_text(sub[: fallback_end.start()] if fallback_end else sub[:8000])

    def _extract_table2(self, text: str) -> str:
        caption_pat = re.compile(r"Table\s+2\.\s*Activity\s+Metrics", flags=re.I)
        start = caption_pat.search(text)
        if not start:
            matches = list(self.TABLE2_PAT.finditer(text))
            if not matches:
                return ""
            start = matches[-1] if len(matches) > 1 else matches[0]
        sub = text[start.start():]
        # Activity table is usually short. Stop before first topic section or first repeated technical protocol.
        end_patterns = [
            r"\n\s*[A-Z][A-Za-z &,'/-]{3,}\n\s*Topic Summary\b",
            r"\n\s*[A-Z]{2}-[A-Z]{2}-\d{3}[a-z]\.\d+\.",
            r"\n\s*Discussion\s+of\s+",
        ]
        end_idx: Optional[int] = None
        for pat in end_patterns:
            m = re.search(pat, sub, flags=re.I)
            if m and m.start() > 50:
                end_idx = m.start() if end_idx is None else min(end_idx, m.start())
        if end_idx is None:
            # Hard cap prevents notes/definitions from overwhelming the fallback parser.
            end_idx = min(len(sub), 3000)
        return clean_text(sub[:end_idx])
