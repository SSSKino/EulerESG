from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PdfTextReaderConfig:
    use_cache: bool = True


@dataclass
class ExtractionConfig:
    # Keep only options used by the sector parser.
    framework: str = "gri-sector"
    type_name_style: str = "auto"
    include_context: bool = False
    include_page: bool = False
    strict_topic_section: bool = True
    include_mine_site: bool = False


@dataclass
class OutputConfig:
    indent: int = 2
    ensure_ascii: bool = False


@dataclass
class AppConfig:
    reader: str = "pdf-text"
    pdf_text: PdfTextReaderConfig = field(default_factory=PdfTextReaderConfig)
    extraction: ExtractionConfig = field(default_factory=ExtractionConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
