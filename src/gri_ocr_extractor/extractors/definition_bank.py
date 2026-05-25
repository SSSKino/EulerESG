from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List

from gri_ocr_extractor.extractors.gri_definition_parser import GriDefinition
from gri_ocr_extractor.models import GriDisclosureRecord
from gri_ocr_extractor.utils.text import single_line


@dataclass
class DefinitionBank:
    by_standard_code: dict[str, list[GriDefinition]] = field(default_factory=lambda: defaultdict(list))
    global_definitions: list[GriDefinition] = field(default_factory=list)

    def add_many(self, definitions: Iterable[GriDefinition]) -> None:
        for d in definitions:
            if d.standard_code:
                self.by_standard_code[d.standard_code].append(d)
            else:
                self.global_definitions.append(d)
        self._dedupe()

    def _dedupe(self) -> None:
        for code, defs in list(self.by_standard_code.items()):
            seen = set()
            unique = []
            for d in defs:
                key = (d.term.lower(), d.definition.lower())
                if key in seen:
                    continue
                seen.add(key)
                unique.append(d)
            self.by_standard_code[code] = unique
        seen = set()
        unique = []
        for d in self.global_definitions:
            key = (d.term.lower(), d.definition.lower())
            if key in seen:
                continue
            seen.add(key)
            unique.append(d)
        self.global_definitions = unique

    def definitions_for_codes(self, codes: Iterable[str]) -> list[GriDefinition]:
        out: list[GriDefinition] = []
        seen = set()
        for code in codes:
            for d in self.by_standard_code.get(str(code), []):
                key = d.term.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(d)
        return out

    def to_simple_dict(self) -> dict[str, list[dict[str, str]]]:
        return {
            code: [{"term": d.term, "definition": d.definition} for d in defs]
            for code, defs in sorted(self.by_standard_code.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 9999)
        }


_CODE_FROM_DISCLOSURE_RE = re.compile(r"\bDisclosure\s+(\d{1,3})-\d+\b", flags=re.I)
_CODE_FROM_STANDARD_RE = re.compile(r"\bGRI\s+(\d{1,3})\b", flags=re.I)


def codes_from_record(record: GriDisclosureRecord) -> list[str]:
    # Prefer exact disclosure code in Metric; fallback to Standard field.
    text_metric = f"{record.Metric or ''}"
    text_all = f"{record.Standard or ''}\n{record.Metric or ''}"
    codes: list[str] = []
    for m in _CODE_FROM_DISCLOSURE_RE.finditer(text_metric):
        codes.append(m.group(1))
    for m in _CODE_FROM_STANDARD_RE.finditer(text_all):
        codes.append(m.group(1))
    # GRI sector code itself is 11/12/13/14 and should not be used as topic definition source.
    sector_prefix = record.Code.split(".", 1)[0] if record.Code and "." in record.Code else ""
    out: list[str] = []
    seen = set()
    for c in codes:
        if c == sector_prefix and c in {"11", "12", "13", "14"}:
            continue
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _norm_for_match(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    cleaned = re.sub(r"\s+", " ", text).strip()
    return " " + cleaned + " "


def _term_in_text(term: str, text: str) -> bool:
    term_norm = _norm_for_match(term)
    # Avoid very generic single words unless they are explicit acronyms.
    words = term_norm.strip().split()
    if len(words) == 1 and len(words[0]) < 4:
        return False
    return term_norm.strip() and term_norm in text


def format_definitions(definitions: list[GriDefinition], max_chars: int = 0) -> str:
    lines: list[str] = []
    for d in definitions:
        line = f"{single_line(d.term)}: {single_line(d.definition)}"
        if line and line not in lines:
            lines.append(line)
    value = "\n".join(lines).strip()
    if max_chars and len(value) > max_chars:
        value = value[: max_chars].rstrip()
    return value


def enrich_records_with_definitions(
    records: list[GriDisclosureRecord],
    bank: DefinitionBank,
    selection: str = "all-by-code",
    max_chars: int = 0,
) -> list[GriDisclosureRecord]:
    selection = (selection or "all-by-code").strip().lower()
    for record in records:
        codes = codes_from_record(record)
        candidates = bank.definitions_for_codes(codes)
        if selection in {"term", "term-in-record", "matched-terms"}:
            searchable = _norm_for_match("\n".join([record.Standard, record.Metric, record.Topic, record.Type, record.Code]))
            candidates = [d for d in candidates if _term_in_text(d.term, searchable)]
        record.Definition = format_definitions(candidates, max_chars=max_chars)
    return records
