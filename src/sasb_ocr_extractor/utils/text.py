from __future__ import annotations

import re


SASB_DISCLOSURE_CODE_RE = re.compile(r"\b[A-Z]{2}-[A-Z]{2}-\d{3}[a-z]\.\d+\b")
SASB_ACTIVITY_CODE_RE = re.compile(r"\b[A-Z]{2}-[A-Z]{2}-000\.[A-Z]\b")
SASB_ANY_CODE_RE = re.compile(
    r"\b[A-Z]{2}-[A-Z]{2}-(?:\d{3}[a-z]\.\d+|000\.[A-Z])\b"
)


def clean_text(text: object) -> str:
    value = str(text or "")
    value = value.replace("\u00a0", " ")
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = value.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
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
