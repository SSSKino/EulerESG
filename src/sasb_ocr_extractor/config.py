from __future__ import annotations

from dataclasses import dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


@dataclass
class PaddleOcrConfig:
    """Configuration for the built-in PaddleOCR-VL reader."""

    pipeline_version: str = "v1.5"
    model_name_or_path: Optional[str] = None
    use_cache: bool = True
    vl_rec_backend: Optional[str] = None
    vl_rec_server_url: Optional[str] = None
    extra_kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CommandReaderConfig:
    """Run any external OCR/VLM command and read its Markdown/Text/JSON output."""

    command: str = ""
    output_markdown: str = "merged_ocr.md"
    output_text: Optional[str] = None
    use_cache: bool = True
    timeout_seconds: Optional[int] = None
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class CustomReaderConfig:
    """Dynamically load a custom reader class/function."""

    class_path: str = ""
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionConfig:
    include_activity: bool = True
    split_multi_part_metrics: bool = True
    include_topic_summary: bool = True
    keep_table1_type: str = "Sustainability Disclosure Topics & Metrics"
    activity_type: str = "Activity Metrics"


@dataclass
class OutputConfig:
    indent: int = 2
    ensure_ascii: bool = False


@dataclass
class AppConfig:
    # No implicit OCR backend. Set reader in config or pass --reader explicitly.
    reader: str = ""  # paddleocr-vl / command / custom; strict no-fallback
    paddleocr: PaddleOcrConfig = field(default_factory=PaddleOcrConfig)
    command_reader: CommandReaderConfig = field(default_factory=CommandReaderConfig)
    custom_reader: CustomReaderConfig = field(default_factory=CustomReaderConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _update_dataclass(obj: Any, values: Dict[str, Any]) -> Any:
    if not values:
        return obj
    for key, value in values.items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            _update_dataclass(current, value)
        else:
            setattr(obj, key, value)
    return obj


def load_config(path: Optional[str]) -> AppConfig:
    cfg = AppConfig()
    if not path:
        return cfg
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "reader" in data:
        cfg.reader = str(data["reader"])
    _update_dataclass(cfg.paddleocr, data.get("paddleocr") or {})
    _update_dataclass(cfg.command_reader, data.get("command_reader") or {})
    _update_dataclass(cfg.custom_reader, data.get("custom_reader") or {})
    _update_dataclass(cfg.extraction, data.get("extraction") or {})
    _update_dataclass(cfg.output, data.get("output") or {})
    return cfg
