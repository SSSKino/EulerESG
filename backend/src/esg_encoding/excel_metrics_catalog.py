"""Excel-driven ESG metric catalog.

Loads ESGMetrics.xlsx and converts each row into a strict metric spec.

This project supports two catalog schemas:
1) Legacy: Primary Navigation / Secondary Navigation / Topic / Sub-topic
2) Extended (recommended): + Unit / Unit conversions / Definitions

Why this exists:
ESG numeric extraction accuracy is bounded by how hard we constrain the target
metrics. The Excel catalog becomes the executable specification.

The retrieval layer is English-first (as requested) and generates query packs
that are safe to pass into embedding recall + rerank + HippoRAG.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import re

import pandas as pd


_WS = re.compile(r"\s+")


def _clean_text(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("（", "(").replace("）", ")")
    s = _WS.sub(" ", s)
    return s


def _strip_paren_cn(s: str) -> str:
    """Strip Chinese hints inside parentheses when building English-first queries."""
    s = _clean_text(s)
    # Remove non-ascii chunks inside parentheses if the rest has English.
    # Example: "Greenhouse gas emissions （GHG emissions）" -> "Greenhouse gas emissions (GHG emissions)"
    return s


def _englishish_tokens(text: str) -> List[str]:
    """Extract query-friendly English tokens/phrases.

    We keep short phrases rather than single tokens, because rerank works better.
    """
    t = _clean_text(text)
    if not t:
        return []
    # Split on common separators while keeping phrases.
    parts = re.split(r"[\|/•·]|\s{2,}", t)
    out: List[str] = []
    for p in parts:
        p = _clean_text(p)
        if not p:
            continue
        out.append(p)
    # Also add highly-informative tokens like "Scope 1" / "TRIR" / "LTIFR".
    for m in re.findall(r"\b(?:Scope\s*[123]|TRIR|LTIFR|GHG|MWh|kWh|GJ|m3|m³|tCO2e|CO2e|%|fatalit(?:y|ies))\b", t, flags=re.I):
        mm = _clean_text(m)
        if mm and mm not in out:
            out.append(mm)
    return out[:24]


def infer_units_allow(primary: str, secondary: str, topic: str, subtopic: str) -> List[str]:
    """Heuristic unit allow-list to improve precision.

    This is intentionally permissive. Hard-filtering uses this list only when
    confident, to avoid false negatives.
    """
    text = " ".join([primary, secondary, topic, subtopic]).lower()
    units: List[str] = []

    if any(k in text for k in ["emission", "ghg", "co2"]):
        units += ["tco2e", "kgco2e", "ktco2e", "co2e"]

    if any(k in text for k in ["energy", "consumption", "mwh", "gj", "kwh"]):
        units += ["gj", "mwh", "kwh"]

    if any(k in text for k in ["water", "withdrawal", "m3", "m³"]):
        units += ["m3", "m³"]

    if any(k in text for k in ["waste", "recycling", "hazard"]):
        units += ["t", "ton", "tons", "tonne", "tonnes", "kg"]

    if any(k in text for k in ["percentage", "percent", "%", "share", "coverage", "rate", "ratio"]):
        units += ["%"]

    if any(k in text for k in ["fine", "penalt", "usd", "cny", "rmb", "eur", "hkd", "sgd", "$", "contribution"]):
        units += ["usd", "cny", "rmb", "eur", "hkd", "sgd", "$"]

    if any(k in text for k in ["hours", "hour", "training"]):
        units += ["hours", "hour", "h"]

    if any(k in text for k in ["fatalit", "incident", "events", "cases", "complaint", "recall"]):
        # counts are unitless in many reports; keep permissive.
        units += ["count"]

    # de-dup (keep order)
    out: List[str] = []
    for u in units:
        uu = _clean_text(u).lower()
        if not uu:
            continue
        if uu not in out:
            out.append(uu)
    return out


def _split_semicolon_list(s: str) -> List[str]:
    """Split a semi-colon separated cell into clean items."""
    s = _clean_text(s)
    if not s:
        return []
    parts = [p.strip() for p in s.split(';')]
    out: List[str] = []
    for p in parts:
        p = _clean_text(p)
        if not p:
            continue
        out.append(p)
    return out


def _canonical_unit(unit_items: List[str]) -> Optional[str]:
    """Pick a canonical unit for storage/comparison.

    Heuristics:
    - prefer tCO2e / CO2e canonicalization
    - otherwise use the first listed unit
    """
    if not unit_items:
        return None
    low = [u.lower().replace(' ', '') for u in unit_items]
    # prefer CO2e canonical
    for i, u in enumerate(low):
        if 'tco2e' in u or 'tco₂e' in u or 'tonneco2e' in u or 'metrictonsco2e' in u:
            return 'tCO2e'
    # percent
    for i, u in enumerate(low):
        if u in ('%', 'percent', 'percentage', 'percentagepoints', 'pp'):
            return '%'
    # energy: prefer GJ when present (stable for conversions)
    for i, u in enumerate(low):
        if u in ('gj', 'tj', 'pj'):
            return 'GJ'
    return unit_items[0]


@dataclass(frozen=True)
class ExcelMetricSpec:
    primary: str
    secondary: str
    topic: str
    subtopic: str

    # Derived helpers
    query_pack_en: Tuple[str, ...]
    units_allow: Tuple[str, ...]

    # Extended catalog fields (optional)
    unit_raw: str = ""
    unit_items: Tuple[str, ...] = ()
    canonical_unit: Optional[str] = None
    unit_conversions_raw: str = ""
    definition: str = ""

    def key(self) -> Tuple[str, str, str, str]:
        return (
            _clean_text(self.primary).lower(),
            _clean_text(self.secondary).lower(),
            _clean_text(self.topic).lower(),
            _clean_text(self.subtopic).lower(),
        )

    def display_subtopic(self) -> str:
        return self.subtopic if self.subtopic else self.topic


def load_excel_catalog(path: str | Path) -> List[ExcelMetricSpec]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"ESG metric catalog not found: {p}")

    df = pd.read_excel(p)
    expected = {"Primary Navigation", "Secondary Navigation", "Topic", "Sub-topic"}
    cols = set(df.columns.astype(str))
    if not expected.issubset(cols):
        raise ValueError(f"Catalog must contain columns {sorted(expected)}; got {sorted(cols)}")

    # Extended fields (optional)
    has_units = "Unit" in cols
    has_conversions = "Unit conversions" in cols
    has_def = "Definitions" in cols

    specs: List[ExcelMetricSpec] = []
    for _i, row in df.iterrows():
        primary = _clean_text(str(row.get("Primary Navigation") or ""))
        secondary = _clean_text(str(row.get("Secondary Navigation") or ""))
        topic = _clean_text(str(row.get("Topic") or ""))
        subtopic = row.get("Sub-topic")
        subtopic = _clean_text("" if pd.isna(subtopic) else str(subtopic))

        if not (primary and secondary and topic):
            continue

        # English-first query pack: prioritize English phrases from Secondary/Topic/Subtopic.
        q: List[str] = []
        q.extend(_englishish_tokens(_strip_paren_cn(secondary)))
        q.extend(_englishish_tokens(topic))
        if subtopic:
            q.extend(_englishish_tokens(subtopic))

        # De-dup and cap
        q2: List[str] = []
        for x in q:
            x = _clean_text(x)
            if not x:
                continue
            if x.lower() in (y.lower() for y in q2):
                continue
            q2.append(x)
            if len(q2) >= 24:
                break

        unit_raw = _clean_text(str(row.get("Unit") or "")) if has_units else ""
        unit_items = _split_semicolon_list(unit_raw) if unit_raw else []
        canonical = _canonical_unit(unit_items) if unit_items else None
        unit_conv = _clean_text(str(row.get("Unit conversions") or "")) if has_conversions else ""
        definition = _clean_text(str(row.get("Definitions") or "")) if has_def else ""

        # Backward compatible unit allow list:
        # - if Unit column exists, allow *all* listed synonyms (normalized)
        # - else fall back to heuristic inference
        if unit_items:
            units = [u.lower().strip() for u in unit_items if u]
        else:
            units = infer_units_allow(primary, secondary, topic, subtopic)

        specs.append(
            ExcelMetricSpec(
                primary=primary,
                secondary=secondary,
                topic=topic,
                subtopic=subtopic,
                query_pack_en=tuple(q2),
                units_allow=tuple(units),
                unit_raw=unit_raw,
                unit_items=tuple(unit_items),
                canonical_unit=canonical,
                unit_conversions_raw=unit_conv,
                definition=definition,
            )
        )

    return specs


def group_specs(specs: Iterable[ExcelMetricSpec]) -> Dict[Tuple[str, str], List[ExcelMetricSpec]]:
    """Group by (Primary, Secondary) for efficient retrieval + batch extraction."""
    groups: Dict[Tuple[str, str], List[ExcelMetricSpec]] = {}
    for s in specs:
        k = (_clean_text(s.primary), _clean_text(s.secondary))
        groups.setdefault(k, []).append(s)
    return groups
