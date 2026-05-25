from __future__ import annotations

from gri_ocr_extractor.config import ExtractionConfig
from gri_ocr_extractor.extractors.gri_sector_parser import GriSectorParser
from gri_ocr_extractor.models import GriDisclosureRecord, OcrDocument


class GriSectorExtractionPipeline:
    def __init__(self, config: ExtractionConfig | None = None):
        self.config = config or ExtractionConfig()
        self.parser = GriSectorParser(self.config)

    def extract(self, document: OcrDocument) -> list[GriDisclosureRecord]:
        return self.parser.parse(document.text or document.markdown, source_path=document.source_path)
