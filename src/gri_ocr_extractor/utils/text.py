from __future__ import annotations

import re

# GRI Sector Standard reference numbers, e.g. 11.1.1, 12.21.8, 13.26.4, 14.0.1.
# Keep it generic so future GRI Sector Standards with other numbers can be parsed.
GRI_SECTOR_REF_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{1,2}\b")

# GRI Topic/Universal Standard disclosures, e.g. Disclosure 2-1, Disclosure 305-1, Disclosure 102-1.
GRI_DISCLOSURE_RE = re.compile(r"\bDisclosure\s+\d{1,3}-\d+\b", flags=re.I)

# GRI requirements, e.g. Requirement 1, Requirement 7.
GRI_REQUIREMENT_RE = re.compile(r"\bRequirement\s+\d+[A-Z]?\b", flags=re.I)


def clean_text(text: object) -> str:
    value = str(text or "")
    value = value.replace("\u00a0", " ")
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = value.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    value = value.replace("\uf0b7", "•").replace("\u2022", "•")
    value = value.replace("￾", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def single_line(text: object, max_chars: int = 0) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars and len(value) > max_chars:
        return value[: max_chars - 3].rstrip() + "..."
    return value


def normalise_header(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().lower()
    return text
