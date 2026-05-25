from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class OcrPage:
    page_number: Optional[int]
    text: str
    markdown: str = ""


@dataclass
class OcrDocument:
    source_path: Path
    text: str
    markdown: str = ""
    pages: List[OcrPage] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GriDisclosureRecord:
    Standard: str
    Metric: str
    Code: str
    Topic: str
    Type: str
    Value: Optional[Any] = None
    Page: Optional[int] = None
    Context: Optional[str] = None
    Definition: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Standard": self.Standard,
            "Metric": self.Metric,
            "Code": self.Code,
            "Topic": self.Topic,
            "Type": self.Type,
            "Value": self.Value,
            "Page": self.Page,
            "Context": self.Context,
            "Definition": self.Definition or "",
        }


MetricRecord = GriDisclosureRecord
