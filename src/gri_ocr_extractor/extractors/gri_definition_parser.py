from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from gri_ocr_extractor.models import OcrDocument
from gri_ocr_extractor.utils.text import clean_text, single_line


@dataclass(frozen=True)
class GriDefinition:
    standard_code: str
    standard: str
    term: str
    definition: str


def infer_gri_standard_code(text: str, source_path: Path | None = None) -> str:
    if source_path and "glossary" in source_path.stem.lower():
        return ""
    candidates: list[str] = []
    if source_path:
        candidates.append(source_path.name)
    candidates.append(text[:4000])
    joined = "\n".join(candidates)
    m = re.search(r"\bGRI\s*(\d{1,3})\s*[:_\- ]", joined, flags=re.I)
    if m:
        return m.group(1)
    return ""


def infer_gri_standard_name(text: str, source_path: Path | None = None) -> str:
    if source_path:
        name = source_path.stem.replace("_", ": ", 1).replace("  ", " ").strip()
        if name:
            return name
    m = re.search(r"(?m)^\s*(GRI\s+\d{1,3}\s*:[^\n]{3,160})$", text)
    if m:
        return single_line(m.group(1))
    return "GRI"


class GriDefinitionParser:
    """Extract definitions from the Glossary section of official GRI PDFs.

    The parser is intentionally small and deterministic. It does not generate
    separate disclosure records; it only builds a term -> definition bank used
    to populate the `Definition` field of sector extraction rows.
    """

    _term_stop_prefixes = (
        "note:", "notes:", "source:", "sources:", "example:", "examples:",
        "requirement", "disclosure", "bibliography", "references", "appendix",
        "this glossary provides", "the definitions included", "if a term is not defined",
        "please note", "* please note", "content", "contents",
    )

    def parse_document(self, document: OcrDocument) -> List[GriDefinition]:
        text = document.text or document.markdown
        return self.parse(text, document.source_path)

    def parse(self, text: str, source_path: Path | None = None) -> List[GriDefinition]:
        text = clean_text(text)
        glossary = self._glossary_body(text)
        if not glossary:
            return []
        code = infer_gri_standard_code(text, source_path)
        standard = infer_gri_standard_name(text, source_path)
        pairs = self._parse_pairs(glossary)
        out: list[GriDefinition] = []
        seen: set[str] = set()
        for term, definition in pairs:
            term = self._clean_term(term)
            definition = self._clean_definition(definition)
            if not term or not definition:
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(GriDefinition(standard_code=code, standard=standard, term=term, definition=definition))
        return out

    def _glossary_body(self, text: str) -> str:
        # Use the last standalone Glossary heading because earlier occurrences often
        # come from the table of contents or introduction.
        matches = list(re.finditer(r"(?mi)^\s*Glossary\s*$", text))
        if not matches:
            return ""
        start = matches[-1].end()
        body = text[start:]
        end = re.search(r"(?mi)^\s*(?:Bibliography|References|Appendix)\s*$", body)
        if end:
            body = body[:end.start()]
        body = re.sub(r"(?m)^\s*\d+\s+GRI\s+\d{1,3}:.*$", "", body)
        body = re.sub(r"(?m)^\s*GRI Standards Glossary\s*$", "", body)
        return body.strip()

    def _parse_pairs(self, glossary: str) -> list[tuple[str, str]]:
        raw_lines = glossary.splitlines()
        groups: list[list[str]] = []
        current: list[str] = []
        for raw in raw_lines:
            line = raw.rstrip()
            if not line.strip():
                if current:
                    groups.append(current)
                    current = []
                continue
            current.append(line)
        if current:
            groups.append(current)

        pairs: list[tuple[str, str]] = []
        current_term: Optional[str] = None
        current_body: list[str] = []

        def flush() -> None:
            nonlocal current_term, current_body
            if current_term and current_body:
                pairs.append((current_term, "\n".join(current_body)))
            current_term = None
            current_body = []

        for group in groups:
            cleaned = [self._strip_page_letter(x) for x in group if self._strip_page_letter(x)]
            if not cleaned:
                continue
            first = cleaned[0]
            if self._is_term_candidate(first) and len(cleaned) >= 2:
                flush()
                current_term = first
                current_body = cleaned[1:]
                continue
            if current_term:
                current_body.extend(cleaned)
        flush()
        return pairs

    def _strip_page_letter(self, line: str) -> str:
        # In GRI glossary pages, the alphabet marker sometimes appears at the
        # left margin on the same line as the first definition line, e.g.
        # "B    historical datum ...". Remove only that margin marker; do not
        # remove normal sentence-initial words inside indented definition text.
        indent = len(line) - len(line.lstrip())
        value = line.strip()
        if indent <= 6:
            value = re.sub(r"^[A-Z]\s+(?=[a-z])", "", value).strip()
        return value

    def _is_term_candidate(self, line: str) -> bool:
        value = single_line(line)
        low = value.lower()
        if not value or len(value) > 110 or len(value) < 2:
            return False
        if low in {"glossary", "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z"}:
            return False
        if any(low.startswith(x) for x in self._term_stop_prefixes):
            return False
        if re.search(r"[.!?:;]$", value):
            return False
        if re.search(r"\b(page|section|table|figure)\s+\d+\b", low):
            return False
        if len(value.split()) > 10:
            return False
        return True

    def _clean_term(self, term: str) -> str:
        value = single_line(term)
        value = re.sub(r"\s+\*+$", "", value).strip()
        return value

    def _clean_definition(self, definition: str) -> str:
        value = clean_text(definition)
        value = re.sub(r"(?m)^\* Please note.*(?:\n.*updated\s+term\.)?", "", value, flags=re.I)
        value = re.sub(r"\n+", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()
