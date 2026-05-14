from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class OcrPage:
    page_number: Optional[int]
    text: str
    markdown: str = ""
    raw_json_path: Optional[str] = None
    raw_markdown_path: Optional[str] = None


@dataclass
class OcrDocument:
    source_path: Path
    text: str
    markdown: str = ""
    pages: List[OcrPage] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricRecord:
    Metric: str
    Category: str
    Unit: str
    Code: str
    Topic: str
    Type: str
    Value: Optional[Any] = None
    Page: Optional[int] = None
    Context: Optional[str] = None
    definition: str = ""
    simple_definition: str = ""
    topic_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "Metric": self.Metric,
            "Category": self.Category,
            "Unit": self.Unit,
            "Code": self.Code,
            "Topic": self.Topic,
            "Type": self.Type,
            "Value": self.Value,
            "Page": self.Page,
            "Context": self.Context,
            "definition": self.definition,
            "simple_definition": self.simple_definition,
            "topic_summary": self.topic_summary,
        }
