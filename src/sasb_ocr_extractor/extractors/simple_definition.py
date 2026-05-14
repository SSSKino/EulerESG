from __future__ import annotations

from sasb_ocr_extractor.utils.text import clean_text


def make_simple_definition(metric: str, category: str, definition: str = "") -> str:
    metric = clean_text(metric)
    category = clean_text(category)
    if category.lower() == "quantitative":
        return (
            f"Report {metric}, including the required scope, calculation basis, "
            "key breakdowns, and qualifiers in the definition."
        )
    return (
        f"Describe {metric.lower()}, including the main policies, processes, scope, "
        "controls, targets, performance, and management information required in the definition."
    )
