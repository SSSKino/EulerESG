from __future__ import annotations

import html
import re
from typing import Dict, List, Optional

from sasb_ocr_extractor.utils.text import clean_text


class MetricSplitter:
    """Expand multi-part SASB metrics into separate records.

    This is rule-based and intentionally separated so project users can extend
    industry-specific patterns without touching OCR or definition extraction.
    """

    def split_row(self, row: Dict[str, str], enabled: bool = True) -> List[Dict[str, str]]:
        row = self._normalise_row(row)
        if not enabled:
            return [row]
        metric = clean_text(row.get("Metric", ""))
        category = clean_text(row.get("Category", ""))

        # Activity metric examples: "Number of manufacturing facilities, percentage outsourced".
        comma_split = self._split_comma_activity(metric, row)
        if comma_split:
            return comma_split

        generic_activity = self._split_generic_comma_activity(metric, row)
        if generic_activity:
            return generic_activity

        # Quantitative comma-separated SASB metrics that do not use numbered markers.
        comma_quantitative = self._split_comma_quantitative(metric, row)
        if comma_quantitative:
            return comma_quantitative

        # Transport fuel and workforce rate metrics use nested (1)/(2) and (a)/(b) components.
        fuel_transport = self._split_fuel_transport_cross_product(metric, row)
        if fuel_transport:
            return fuel_transport

        incident_rate = self._split_incident_rate_cross_product(metric, row)
        if incident_rate:
            return incident_rate

        # Direct suppliers audit cross-product pattern: (1)/(2) with (a)/(b).
        social_audit = self._split_social_audit_cross_product(metric, row)
        if social_audit:
            return social_audit

        # Gender/diversity representation has a two-by-four matrix in the SASB standard.
        diversity_split = self._split_diversity_representation(metric, row)
        if diversity_split:
            return diversity_split

        generic_nested = self._split_generic_nested_components(metric, row)
        if generic_nested:
            return generic_nested

        # General numbered components. Most useful for Quantitative and Activity Metrics;
        # discussion metrics with many explanatory numbered clauses are kept unless the
        # metric itself starts with a numbered component.
        if "(1)" in metric and "(2)" in metric:
            if category.lower() != "discussion and analysis" and (
                category.lower() == "quantitative" or row.get("Type") == "Activity Metrics" or metric.startswith("(1)")
            ):
                expanded = self._split_numbered_metric(metric, row)
                if len(expanded) > 1:
                    return expanded

        return [row]

    def _normalise_row(self, row: Dict[str, str]) -> Dict[str, str]:
        clean_row = dict(row)
        for key in ("Metric", "Category", "Unit", "Code", "Topic", "Type"):
            if key in clean_row:
                clean_row[key] = self._clean_field(clean_row.get(key, ""), strip_note=(key == "Metric"))
        return clean_row

    def _normalise_latex_tokens(self, text: str) -> str:
        replacements = [
            (r"\$\s*CO_\{2\}-e\s*\$", "CO₂-e"),
            (r"\$\s*CO_\{2\}\s*\$", "CO₂"),
            (r"\$\s*gCO\s*\$?\s*_\{2\}\s*\$?", "gCO₂"),
            (r"\$\s*NO_\{x\}\s*\$", "NOx"),
            (r"\$\s*N_\{2\}O\s*\$", "N2O"),
            (r"\$\s*SO_\{x\}\s*\$", "SOx"),
            (r"\$\s*PM_\{10\}\s*\$", "PM10"),
            (r"\$\s*m\^\{2\}\s*\$", "m²"),
            (r"\$\s*m\^\{3\}\s*\$", "m³"),
        ]
        clean_value = str(text or "")
        for pattern, replacement in replacements:
            clean_value = re.sub(pattern, replacement, clean_value, flags=re.I)
        return clean_value

    def _clean_field(self, value: object, strip_note: bool = False) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"<[^>]+>", " ", text)
        if strip_note:
            text = re.sub(r"\$\s*(?:\{\}\s*)?\^\s*\{?\d+[A-Za-z,]*\}?\s*\$", " ", text)
        text = self._normalise_latex_tokens(text)
        text = re.sub(r"\s+([,.;:])", r"\1", text)
        text = clean_text(text)
        if strip_note:
            text = re.sub(r"\s*,\s*$", "", text).strip()
        return text

    def _split_comma_activity(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        if row.get("Type") != "Activity Metrics":
            return []
        if re.search(r"(?i)^Number of .+,\s*percentage outsourced$", metric):
            left, right = [clean_text(x) for x in metric.split(",", 1)]
            r1 = dict(row)
            r2 = dict(row)
            r1["Metric"] = left
            r2["Metric"] = "Percentage of " + left.replace("Number of ", "", 1) + " outsourced"
            unit_parts = self._unit_parts(row.get("Unit", ""), 2)
            if unit_parts:
                r1["Unit"] = unit_parts[0]
                r2["Unit"] = unit_parts[1]
            elif not r2.get("Unit"):
                r2["Unit"] = "Percentage (%)"
            return [r1, r2]
        return []

    def _split_generic_comma_activity(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        if row.get("Type") != "Activity Metrics":
            return []
        if "," not in metric:
            return []
        parts = [clean_text(x) for x in re.split(r"\s*,\s*", metric) if clean_text(x)]
        if len(parts) <= 1:
            return []
        starts = re.compile(r"(?i)^(number|percentage|amount|total|annual|area|production|volume|weight|length|revenue|median|cost)\b")
        if not all(starts.search(part) for part in parts):
            return []
        unit_parts = self._unit_parts(row.get("Unit", ""), len(parts))
        return [
            self._clone(row, self._sentence_case_metric(part), unit_parts[idx] if idx < len(unit_parts) else None)
            for idx, part in enumerate(parts)
        ]

    def _split_comma_quantitative(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        if clean_text(row.get("Category", "")).lower() != "quantitative":
            return []
        lower = metric.lower()
        unit_parts = self._unit_parts(row.get("Unit", ""), 4)

        if "percentage of campaigns reviewed" in lower and "percentage of those in compliance" in lower:
            base = clean_text(metric.split(",", 1)[0])
            return [
                self._clone(row, base, unit_parts[0] if unit_parts else None),
                self._clone(row, f"{base} that are in compliance", unit_parts[1] if len(unit_parts) > 1 else None),
            ]

        if re.search(r"(?i)^Fleet fuel consumed,\s*percentage renewable$", metric):
            return [
                self._clone(row, "Fleet fuel consumed", unit_parts[0] if unit_parts else None),
                self._clone(row, "Percentage of fleet fuel consumed that is renewable fuel", unit_parts[1] if len(unit_parts) > 1 else None),
            ]

        if re.search(r"(?i)^(Total energy consumed|Operational energy consumed),\s*percentage grid electricity", metric):
            first = clean_text(metric.split(",", 1)[0])
            return [
                self._clone(row, first, unit_parts[0] if unit_parts else None),
                self._clone(row, "Percentage of energy consumed that was supplied from grid electricity", unit_parts[1] if len(unit_parts) > 1 else None),
                self._clone(row, "Percentage of energy consumed that was renewable energy", unit_parts[2] if len(unit_parts) > 2 else None),
            ]

        if re.search(r"(?i)^Total water withdrawn,\s*total water consumed", metric):
            location = "regions"
            if "locations" in lower:
                location = "locations"
            tail = f"in {location} with High or Extremely High Baseline Water Stress"
            return [
                self._clone(row, "Total water withdrawn", unit_parts[0] if unit_parts else None),
                self._clone(row, "Total water consumed", unit_parts[0] if unit_parts else None),
                self._clone(row, f"Percentage of total water withdrawn {tail}", unit_parts[1] if len(unit_parts) > 1 else None),
                self._clone(row, f"Percentage of total water consumed {tail}", unit_parts[1] if len(unit_parts) > 1 else None),
            ]

        if re.search(r"(?i)^Total weight of packaging,\s*percentage", metric):
            return [
                self._clone(row, "Total weight of packaging", unit_parts[0] if unit_parts else None),
                self._clone(row, "Percentage of packaging, by weight, that is recyclable, reusable or compostable", unit_parts[1] if len(unit_parts) > 1 else None),
            ]

        return []

    def _split_fuel_transport_cross_product(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        lower = metric.lower()
        if not lower.startswith("fuel consumed by"):
            return []
        if "road transport" not in lower or "air transport" not in lower:
            return []
        unit_parts = self._unit_parts(row.get("Unit", ""), 2)
        fuel_unit = unit_parts[0] if unit_parts else None
        pct_unit = unit_parts[1] if len(unit_parts) > 1 else None
        return [
            self._clone(row, "Fuel consumed by (1) road transport", fuel_unit),
            self._clone(row, "Fuel consumed by (1) road transport, percentage (a) natural gas", pct_unit),
            self._clone(row, "Fuel consumed by (1) road transport, percentage (b) renewable fuel", pct_unit),
            self._clone(row, "Fuel consumed by (2) air transport", fuel_unit),
            self._clone(row, "Fuel consumed by (2) air transport, percentage (a) alternative fuel", pct_unit),
            self._clone(row, "Fuel consumed by (2) air transport, percentage (b) sustainable fuel", pct_unit),
        ]

    def _split_incident_rate_cross_product(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        lower = metric.lower()
        if "total recordable incident rate" not in lower or "fatality rate" not in lower:
            return []
        if "direct employees" not in lower or "contract employees" not in lower:
            return []
        return [
            self._clone(row, "(1) Total recordable incident rate (TRIR) for (a) direct employees"),
            self._clone(row, "(1) Total recordable incident rate (TRIR) for (b) contract employees"),
            self._clone(row, "(2) Fatality rate for (a) direct employees"),
            self._clone(row, "(2) Fatality rate for (b) contract employees"),
        ]

    def _split_social_audit_cross_product(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        lower = metric.lower()
        if "non-conformance" not in lower or "corrective action" not in lower:
            return []
        if "(a)" not in metric or "(b)" not in metric:
            return []
        prefix = clean_text(metric.split("(1)", 1)[0])
        if not prefix:
            prefix = "Direct suppliers' social responsibility audit"

        if "major non-conformances" in lower or "minor non-conformances" in lower:
            first_label = "(a) major non-conformances"
            second_label = "(b) minor non-conformances"
        else:
            first_label = "(a) priority non-conformances"
            second_label = "(b) other non-conformances"

        nonconformance_term = "non-conformance rates" if "non-conformance rates" in lower else "non-conformance rate"
        corrective_term = "associated corrective action rates" if "corrective action rates" in lower else "associated corrective action rate"

        return [
            self._clone(row, f"{prefix} (1) {nonconformance_term} for {first_label}"),
            self._clone(row, f"{prefix} (1) {nonconformance_term} for {second_label}"),
            self._clone(row, f"{prefix} (2) {corrective_term} for {first_label}"),
            self._clone(row, f"{prefix} (2) {corrective_term} for {second_label}"),
        ]

    def _split_diversity_representation(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        lower = metric.lower()
        if "gender" not in lower or "diversity" not in lower or "executive management" not in lower:
            return []
        if "(a)" not in metric or "(b)" not in metric:
            return []
        groups = [
            "(a) executive management",
            "(b) non-executive management",
            "(c) professionals",
            "(d) all other employees",
        ]
        return [
            self._clone(row, f"Percentage of gender representation for {group}")
            for group in groups
        ] + [
            self._clone(row, f"Percentage of diversity group representation for {group}")
            for group in groups
        ]

    def _split_generic_nested_components(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        if clean_text(row.get("Category", "")).lower() == "discussion and analysis":
            return []
        if "(1)" not in metric or "(2)" not in metric or "(a)" not in metric or "(b)" not in metric:
            return []

        prefix_match = re.match(r"(?P<prefix>.+?)\s*\(1\)\s*(?P<body>.+)", metric)
        if not prefix_match:
            return []
        prefix = clean_text(prefix_match.group("prefix"))
        numbered_parts = self._numbered_parts("(1) " + prefix_match.group("body"))
        if len(numbered_parts) <= 1:
            return []

        shared_alpha_parts = self._shared_alpha_parts(numbered_parts[-1])
        if shared_alpha_parts:
            base_parts = [self._strip_shared_alpha_tail(part) for part in numbered_parts]
            unit_parts = self._unit_parts(row.get("Unit", ""), len(base_parts) * len(shared_alpha_parts))
            out: List[Dict[str, str]] = []
            for base in base_parts:
                marker = self._marker(base)
                base_body = self._subject_without_marker(base)
                for alpha in shared_alpha_parts:
                    text = f"{prefix} {marker} {base_body} for {alpha}" if prefix else f"{marker} {base_body} for {alpha}"
                    out.append(self._clone(row, self._sentence_case_metric(text), unit_parts[len(out)] if len(out) < len(unit_parts) else None))
            return out

        out_parts: List[str] = []
        for part in numbered_parts:
            alpha_parts = self._alpha_parts(part)
            if not alpha_parts:
                out_parts.append(clean_text(f"{prefix} {part}" if prefix else part))
                continue
            base = self._strip_alpha_tail(part)
            for alpha in alpha_parts:
                out_parts.append(clean_text(f"{prefix} {base} {alpha}" if prefix else f"{base} {alpha}"))

        if len(out_parts) <= len(numbered_parts):
            return []
        unit_parts = self._unit_parts(row.get("Unit", ""), len(out_parts))
        return [self._clone(row, self._sentence_case_metric(part), unit_parts[idx] if idx < len(unit_parts) else None) for idx, part in enumerate(out_parts)]

    def _alpha_parts(self, text: str) -> List[str]:
        matches = list(re.finditer(r"\([a-z]\)", text, flags=re.I))
        if len(matches) <= 1:
            return []
        parts: List[str] = []
        for idx, match in enumerate(matches):
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            part = clean_text(text[match.start():end])
            part = re.sub(r"(?i)(?:\s+and)?\s*[,;]\s*$", "", part).strip()
            part = re.sub(r"(?i)\s+and\s*$", "", part).strip()
            if part:
                parts.append(part)
        return parts

    def _shared_alpha_parts(self, text: str) -> List[str]:
        match = re.search(r"\bfor\s+(\([a-z]\).+)$", text, flags=re.I)
        if not match:
            return []
        return self._alpha_parts(match.group(1))

    def _strip_shared_alpha_tail(self, text: str) -> str:
        return clean_text(re.sub(r"\bfor\s+\([a-z]\).+$", "", text, flags=re.I))

    def _strip_alpha_tail(self, text: str) -> str:
        return clean_text(re.sub(r"\([a-z]\).+$", "", text, flags=re.I))

    def _split_numbered_metric(self, metric: str, row: Dict[str, str]) -> List[Dict[str, str]]:
        # Pattern A: "(1) A, (2) B and (3) C"
        if metric.startswith("(1)"):
            parts = self._numbered_parts(metric)
            if len(parts) <= 1:
                return []
            parts = self._propagate_common_tail(parts)
            parts = self._normalise_parts_with_context(parts, metric)
            unit_parts = self._unit_parts(row.get("Unit", ""), len(parts))
            return [self._clone(row, p, unit_parts[idx] if idx < len(unit_parts) else None) for idx, p in enumerate(parts)]

        # Pattern B: "Percentage of (1) A and (2) B that ..."
        prefix_match = re.match(r"(?P<prefix>.+?)\s*\(1\)\s*(?P<body>.+)", metric)
        if not prefix_match:
            return []
        prefix = clean_text(prefix_match.group("prefix"))
        body = "(1) " + prefix_match.group("body")
        parts = self._numbered_parts(body)
        if len(parts) <= 1:
            return []

        # If first part lacks a trailing condition but later part contains one, copy common tail.
        parts = self._propagate_common_tail(parts)
        unit_parts = self._unit_parts(row.get("Unit", ""), len(parts))
        out = []
        for idx, p in enumerate(parts):
            p = clean_text(p)
            text = f"{prefix} {p}" if prefix else p
            out.append(self._clone(row, self._sentence_case_metric(text), unit_parts[idx] if idx < len(unit_parts) else None))
        return out

    def _numbered_parts(self, text: str) -> List[str]:
        marker_matches = list(re.finditer(r"\(\d+\)", text))
        if len(marker_matches) <= 1:
            return [clean_text(text)] if clean_text(text) else []
        parts: List[str] = []
        for idx, m in enumerate(marker_matches):
            end = marker_matches[idx + 1].start() if idx + 1 < len(marker_matches) else len(text)
            part = clean_text(text[m.start():end])
            part = re.sub(r"(?i)(?:\s+and)?\s*[,;]\s*$", "", part).strip()
            part = re.sub(r"(?i)\s+and\s*$", "", part).strip()
            if part:
                parts.append(part)
        return parts

    def _normalise_parts_with_context(self, parts: List[str], full_metric: str) -> List[str]:
        lower_full = full_metric.lower()
        out: List[str] = []
        first_subject = self._subject_without_marker(parts[0]) if parts else ""
        for idx, part in enumerate(parts):
            text = clean_text(part)
            lower = text.lower()
            marker = self._marker(text)

            if re.search(r"\bpercentage\s+grid\s+electricity\b", lower):
                text = self._with_marker(marker, "Percentage of energy consumed that was supplied from grid electricity")
            elif re.search(r"\bpercentage\s+renewable\b", lower) and "energy" in lower_full:
                text = self._with_marker(marker, "Percentage of energy consumed that was renewable energy")
            elif re.search(r"\bpercentage\s+alternative\b", lower) and "fuel" in lower_full:
                text = self._with_marker(marker, "Percentage of fuel consumption that was alternative fuel")
            elif re.search(r"\bpercentage\s+sustainable\b", lower) and "fuel" in lower_full:
                text = self._with_marker(marker, "Percentage of fuel consumed that was sustainable fuel")
            elif re.search(r"\bpercentage\s+investigated\b", lower) and "complaints" in lower_full:
                subject = re.sub(r"(?i)^Number of\s+", "", first_subject).strip()
                text = self._with_marker(marker, f"Percentage of {subject} investigated") if subject else self._with_marker(marker, "Percentage investigated")
            elif re.search(r"\bpercentage\s+recycled\b", lower):
                subject = self._recycled_subject(first_subject)
                text = self._with_marker(marker, f"Percentage of {subject} recycled") if subject else self._sentence_case_metric(text)
            elif re.search(r"\bpercentage\s+hazardous\b", lower) and "waste" in lower_full:
                text = self._with_marker(marker, "Percentage of hazardous waste, by weight, generated from manufacturing operations")
            elif re.search(r"\bpercentage\s+renewable\b", lower) and "fuel" in lower_full:
                text = self._with_marker(marker, "Percentage of fleet fuel consumed that is renewable fuel")
            else:
                text = self._sentence_case_metric(text)
            out.append(text)
        return out

    def _subject_without_marker(self, text: str) -> str:
        return clean_text(re.sub(r"^\(\d+\)\s*", "", text))

    def _marker(self, text: str) -> str:
        m = re.match(r"\(\d+\)", text.strip())
        return m.group(0) if m else ""

    def _with_marker(self, marker: str, text: str) -> str:
        if not marker:
            return self._sentence_case_metric(text)
        return f"{marker} {self._sentence_case_metric(text)}"

    def _recycled_subject(self, first_subject: str) -> str:
        value = self._subject_without_marker(first_subject)
        value = re.sub(r"(?i)^Amount of\s+", "", value).strip()
        value = re.sub(r"(?i)^Total amount of\s+", "", value).strip()
        value = re.sub(r"(?i)\s+generated$", "", value).strip()
        return value

    def _unit_parts(self, unit: str, count: int) -> List[str]:
        unit = self._clean_field(unit)
        if count <= 1 or not unit:
            return [unit] if unit else []
        raw_parts = [clean_text(x) for x in re.split(r"\s*,\s*", unit) if clean_text(x)]
        if len(raw_parts) <= 1:
            return [unit for _ in range(count)]
        if len(raw_parts) == count:
            return raw_parts
        if len(raw_parts) == 2 and count > 2:
            return [raw_parts[0]] + [raw_parts[1] for _ in range(count - 1)]
        if len(raw_parts) < count:
            return raw_parts + [raw_parts[-1] for _ in range(count - len(raw_parts))]
        return raw_parts[:count]

    def _propagate_common_tail(self, parts: List[str]) -> List[str]:
        # Common SASB syntax often means: (1) A and (2) B <tail>
        # Try to copy a tail beginning at "in compliance", "that have", "that were", "certified", etc.
        if len(parts) > 2:
            second_tail = re.search(
                r"\b(in compliance with|that have|that has|that were|that are|for suppliers)\b.+$",
                parts[1],
                flags=re.I,
            )
            if second_tail and not re.search(re.escape(second_tail.group(0)), parts[0], flags=re.I):
                updated = list(parts)
                updated[0] = clean_text(updated[0] + " " + second_tail.group(0))
                return updated
            last_tail = re.search(
                r"\b(in compliance with|that have|that has|that were|that are|sold|for suppliers)\b.+$",
                parts[-1],
                flags=re.I,
            )
            if last_tail:
                tail = last_tail.group(0)
                return [clean_text(p if re.search(re.escape(tail), p, flags=re.I) else p + " " + tail) for p in parts]
            return parts
        if len(parts) != 2:
            return parts
        tail_match = re.search(
            r"\b(in compliance with|that have|that has|that were|that are|sold|for suppliers)\b.+$",
            parts[1],
            flags=re.I,
        )
        if not tail_match:
            shared_tail = re.search(r"(?i)^\(2\)\s*percentage\s+(of\s+.+)$", parts[1])
            if shared_tail and re.search(r"(?i)^\(1\)\s*Number(?:\s+and)?$", parts[0]):
                return [clean_text(parts[0] + " " + shared_tail.group(1)), parts[1]]
            pct_subject = re.search(r"(?i)^\(2\)\s*percentage\s+of\s+(.+)$", parts[1])
            if pct_subject and re.search(r"(?i)^\(1\)\s*Number(?:\s+and)?$", parts[0]):
                return [clean_text("(1) Number of " + pct_subject.group(1)), parts[1]]
            return parts
        tail = tail_match.group(0)
        if re.search(re.escape(tail), parts[0], flags=re.I):
            return parts
        return [clean_text(parts[0] + " " + tail), parts[1]]

    def _sentence_case_metric(self, text: str) -> str:
        text = clean_text(text)
        # Keep marker and proper case; just capitalise a lowercase first alpha if needed.
        if not text:
            return text
        marker_match = re.match(r"^(\(\d+\)\s+)(.+)$", text)
        if marker_match:
            prefix, rest = marker_match.groups()
            return prefix + (rest[0].upper() + rest[1:] if rest else rest)
        return text[0].upper() + text[1:]

    def _clone(self, row: Dict[str, str], metric: str, unit: Optional[str] = None) -> Dict[str, str]:
        new_row = dict(row)
        metric_text = clean_text(metric)
        metric_text = re.sub(r"\s*,\s*$", "", metric_text).strip()
        metric_text = re.sub(r"\(\s+", "(", metric_text)
        metric_text = re.sub(r"\s+\)", ")", metric_text)
        metric_text = re.sub(r"(?i)^Percentage of (\(\d+\))\s+percentage of\s+", r"Percentage of \1 ", metric_text)
        new_row["Metric"] = metric_text
        if unit is not None:
            new_row["Unit"] = clean_text(unit)
        return new_row
