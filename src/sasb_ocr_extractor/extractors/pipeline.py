from __future__ import annotations

from typing import Dict, List

from sasb_ocr_extractor.config import ExtractionConfig
from sasb_ocr_extractor.extractors.definition_extractor import DefinitionExtractor
from sasb_ocr_extractor.extractors.metric_splitter import MetricSplitter
from sasb_ocr_extractor.extractors.sections import SasbSectionLocator
from sasb_ocr_extractor.extractors.simple_definition import make_simple_definition
from sasb_ocr_extractor.extractors.table_parser import SasbTableParser
from sasb_ocr_extractor.extractors.topic_summary_extractor import TopicSummaryExtractor
from sasb_ocr_extractor.models import MetricRecord, OcrDocument
from sasb_ocr_extractor.utils.text import clean_text, single_line


class SasbExtractionPipeline:
    def __init__(self, config: ExtractionConfig | None = None):
        self.config = config or ExtractionConfig()
        self.locator = SasbSectionLocator()
        self.parser = SasbTableParser()
        self.splitter = MetricSplitter()

    def extract(self, document: OcrDocument) -> List[MetricRecord]:
        blocks = self.locator.locate(document.text or document.markdown, self.config.include_activity)
        full_text = document.text or document.markdown
        definition_extractor = DefinitionExtractor(full_text)

        raw_rows: List[Dict[str, str]] = []
        for block in blocks:
            rows = self.parser.parse(block.text, section_type=block.type)
            raw_rows.extend(rows)

        expanded_rows: List[Dict[str, str]] = []
        for row in raw_rows:
            expanded_rows.extend(self.splitter.split_row(row, self.config.split_multi_part_metrics))

        topic_summary_extractor = TopicSummaryExtractor(
            full_text,
            known_topics=[row.get("Topic", "") for row in expanded_rows],
        )

        records: List[MetricRecord] = []
        for row in expanded_rows:
            code = single_line(row.get("Code", ""))
            record_type = single_line(row.get("Type", self.config.keep_table1_type))
            metric_text = single_line(row.get("Metric", ""))
            if record_type == "Activity Metrics":
                definition = metric_text
            else:
                definition = definition_extractor.definition_for_code(code)
            topic_summary = ""
            if self.config.include_topic_summary and record_type == self.config.keep_table1_type:
                topic_summary = topic_summary_extractor.summary_for_topic(row.get("Topic", ""))
            records.append(MetricRecord(
                Metric=metric_text,
                Category=single_line(row.get("Category", "")),
                Unit=single_line(row.get("Unit", "")),
                Code=code,
                Topic=single_line(row.get("Topic", "")),
                Type=record_type,
                Value=None,
                Page=None,
                Context=None,
                definition=definition,
                simple_definition=make_simple_definition(row.get("Metric", ""), row.get("Category", ""), definition),
                topic_summary=topic_summary,
            ))

        return self._deduplicate(records)

    def _deduplicate(self, records: List[MetricRecord]) -> List[MetricRecord]:
        seen = set()
        out: List[MetricRecord] = []
        for rec in records:
            key = (rec.Type, rec.Code, rec.Metric)
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
        return out
