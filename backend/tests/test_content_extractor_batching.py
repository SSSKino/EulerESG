from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from esg_encoding.content_extractor import (
    ContentExtractor,
    _batch_timing_summary,
    _page_batch_ranges,
)
from esg_encoding.exceptions import ContentExtractionError


class PageBatchRangeTests(unittest.TestCase):
    def test_116_pages_create_17_contiguous_batches(self):
        ranges = _page_batch_ranges(116, 7)

        self.assertEqual(len(ranges), 17)
        self.assertEqual(ranges[0], (1, 7))
        self.assertEqual(ranges[-1], (113, 116))
        pages = [page for start, end in ranges for page in range(start, end + 1)]
        self.assertEqual(pages, list(range(1, 117)))

    def test_batch_timing_summary_uses_worker_results(self):
        states = [
            {"result_json": json.dumps({"elapsed_seconds": value, "predict_seconds": value - 1})}
            for value in (3.0, 5.0, 7.0, 9.0)
        ]

        summary = _batch_timing_summary(states)

        self.assertEqual(summary["elapsed_seconds"]["count"], 4)
        self.assertEqual(summary["elapsed_seconds"]["avg"], 6.0)
        self.assertEqual(summary["elapsed_seconds"]["p50"], 6.0)
        self.assertEqual(summary["elapsed_seconds"]["max"], 9.0)
        self.assertEqual(summary["predict_seconds"]["avg"], 5.0)


class PyMuPdfSplitTests(unittest.TestCase):
    def test_15_page_pdf_is_split_losslessly_with_ready_markers(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.pdf"
            source = fitz.open()
            for page_number in range(1, 16):
                page = source.new_page()
                page.insert_text((72, 72), f"PAGE-{page_number:02d}")
            source.save(source_path)
            source.close()

            with patch.dict(os.environ, {"PADDLEOCR_JOB_WORK_DIR": str(root / "jobs")}):
                units, total_pages, batch_dir = ContentExtractor()._split_pdf_for_page_batch_queue(
                    source_path,
                    "split_test",
                    7,
                )

            self.assertEqual(total_pages, 15)
            self.assertEqual(
                [(unit["start_page"], unit["end_page"]) for unit in units],
                [(1, 7), (8, 14), (15, 15)],
            )
            self.assertEqual(list(batch_dir.glob("*.tmp.pdf")), [])

            copied_labels: list[str] = []
            for unit, expected_count in zip(units, (7, 7, 1)):
                batch_path = Path(unit["input_path"])
                ready_path = Path(unit["ready_path"])
                self.assertTrue(batch_path.is_file())
                self.assertTrue(ready_path.is_file())

                marker = json.loads(ready_path.read_text(encoding="utf-8"))
                self.assertEqual(marker["start_page"], unit["start_page"])
                self.assertEqual(marker["end_page"], unit["end_page"])

                batch = fitz.open(batch_path)
                try:
                    self.assertEqual(len(batch), expected_count)
                    copied_labels.extend(page.get_text("text").strip() for page in batch)
                finally:
                    batch.close()

            self.assertEqual(copied_labels, [f"PAGE-{page:02d}" for page in range(1, 16)])


class PageBatchMergeValidationTests(unittest.TestCase):
    def test_success_state_with_missing_page_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_markdown = root / "batch.md"
            batch_markdown.write_text(
                "\n".join(
                    f"<!-- Page {page} | PaddleOCR-VL batch 1/1 part {page} -->\npage-{page}"
                    for page in range(1, 7)
                ),
                encoding="utf-8",
            )
            state = {
                "status": "success",
                "result_json": json.dumps(
                    {
                        "start_page": 1,
                        "end_page": 7,
                        "result_count": 7,
                        "batch_markdown_path": str(batch_markdown),
                    }
                ),
            }

            with self.assertRaisesRegex(
                ContentExtractionError,
                "incomplete or out of order",
            ):
                ContentExtractor()._merge_paddleocr_page_batch_results(
                    client=object(),
                    task_key="paddleocr:task:test",
                    job_id="test",
                    source_path=root / "source.pdf",
                    batch_states=[state],
                    output_dir=root / "output",
                    total_pages=7,
                    total_units=1,
                    batch_size=7,
                )


if __name__ == "__main__":
    unittest.main()
