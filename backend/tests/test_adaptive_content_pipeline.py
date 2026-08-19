from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from esg_encoding.content_extractor import (
    ContentExtractor,
    _clean_non_table_markdown_block,
    _merge_native_and_ocr_page,
    _selected_page_batch_ranges,
)
from esg_encoding.page_parser import PageProfile, PdfAnalysis, analyze_pdf_pages
from esg_encoding.visual_assets import load_visual_manifest, promote_visual_assets


class _FakePage:
    def __init__(
        self,
        *,
        blocks: list[tuple] | None = None,
        images: list[dict] | None = None,
        drawings: int = 0,
        rotation: int = 0,
    ) -> None:
        self.rect = (0.0, 0.0, 100.0, 100.0)
        self.rotation = rotation
        self._blocks = list(blocks or [])
        self._images = list(images or [])
        self._drawings = drawings

    def get_text(self, kind: str, sort: bool = False):  # noqa: ARG002
        assert kind == "blocks"
        return list(self._blocks)

    def get_image_info(self, **_kwargs):
        return list(self._images)

    def get_cdrawings(self):
        return [{} for _ in range(self._drawings)]


class _FakeDocument:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = pages
        self.page_count = len(pages)
        self.closed = False

    def load_page(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        self.closed = True


class _FakeFitzModule:
    __name__ = "fake_fitz"
    VersionBind = "test"

    def __init__(self, pages: list[_FakePage]) -> None:
        self.document = _FakeDocument(pages)

    def open(self, _source: str) -> _FakeDocument:
        return self.document


def _text_block(text: str, bbox: tuple[float, float, float, float] = (5, 10, 95, 30)) -> tuple:
    return (*bbox, text, 0, 0)


def test_page_analyzer_routes_digital_scanned_and_mixed_pages() -> None:
    digital = _FakePage(
        blocks=[
            _text_block(
                "Employee engagement reached 82 percent in 2024 and remained stable."
            )
        ]
    )
    scanned = _FakePage(
        images=[{"xref": 2, "bbox": (0, 0, 100, 100), "width": 2000, "height": 2000}]
    )
    mixed = _FakePage(
        blocks=[
            _text_block(
                "Employee engagement reached 82 percent; the chart gives the yearly trend."
            )
        ],
        images=[{"xref": 3, "bbox": (20, 40, 80, 75), "width": 1200, "height": 700}],
    )
    backend = _FakeFitzModule([digital, scanned, mixed])

    analysis = analyze_pdf_pages("report.pdf", fitz_module=backend)

    assert analysis.available is True
    assert analysis.error is None
    assert analysis.total_pages == 3
    assert [page.page_number for page in analysis.pages] == [1, 2, 3]
    assert [page.content_kind for page in analysis.pages] == [
        "digital",
        "scanned",
        "hybrid",
    ]
    assert [page.route for page in analysis.pages] == ["native", "ocr", "hybrid"]
    assert analysis.route_counts == {"native": 1, "ocr": 1, "hybrid": 1}
    assert backend.document.closed is True


def test_selected_ocr_plan_excludes_native_pages_without_losing_global_ranges() -> None:
    # Pages 1 and 4 are native; only contiguous OCR/hybrid pages are batched.
    assert _selected_page_batch_ranges(6, 7, [2, 3, 5, 6]) == [(2, 3), (5, 6)]
    assert _selected_page_batch_ranges(6, 1, [2, 3, 5]) == [(2, 2), (3, 3), (5, 5)]


def test_all_native_analysis_skips_the_ocr_queue() -> None:
    analysis = PdfAnalysis(
        available=True,
        total_pages=2,
        pages=[
            PageProfile(
                page_number=1,
                route="native",
                content_kind="digital",
                page_width=100,
                page_height=100,
                rotation=0,
                native_markdown="Exact native text on page one.",
            ),
            PageProfile(
                page_number=2,
                route="native",
                content_kind="digital",
                page_width=100,
                page_height=100,
                rotation=0,
                native_markdown="Exact native text on page two.",
            ),
        ],
    )
    extractor = ContentExtractor()

    with patch.object(extractor, "_wake_paddleocr_vlm") as wake, patch.object(
        extractor, "_run_paddleocr_vl_page_batch_queue_active"
    ) as run_active:
        result = extractor._run_paddleocr_vl_page_batch_queue(
            Path("unused.pdf"), page_analysis=analysis
        )

    wake.assert_not_called()
    run_active.assert_not_called()
    assert result["mode"] == "native"
    assert result["native_page_count"] == 2
    assert result["ocr_page_count"] == 0
    assert result["markdown"].count("Exact native text") == 2


def test_native_and_ocr_duplicate_text_is_not_indexed_twice() -> None:
    native = "Employee engagement reached 82% in 2024."
    duplicate_ocr = "Employee engagement reached 82% in 2024."

    merged = _merge_native_and_ocr_page(native, duplicate_ocr)

    assert merged == native
    assert merged.count("82%") == 1
    assert "OCR structure supplement" not in merged

    table_supplement = """Employee engagement reached 82% in 2024.

| Metric | 2024 |
| --- | --- |
| Engagement | 82% |"""
    structured = _merge_native_and_ocr_page(native, table_supplement)
    assert "OCR structure supplement" in structured
    assert "| Engagement | 82% |" in structured


def test_empty_visual_is_not_converted_into_an_embedding_segment() -> None:
    empty_visual = {
        "asset_id": "va_empty000000000000000",
        "page_number": 1,
        "caption": "",
        "summary": "",
        "ocr_text": "",
        "chart_data": None,
        "confidence": 0.5,
    }
    meaningful_visual = {
        "asset_id": "va_chart000000000000000",
        "page_number": 1,
        "caption": "Employee engagement by year",
        "summary": "",
        "ocr_text": "",
        "chart_data": None,
        "confidence": 0.9,
    }
    markdown = "\n\n".join(
        [
            "<!-- Page 1 | adaptive parser route=hybrid -->",
            f"<!-- visual-asset: {json.dumps(empty_visual)} -->",
            f"<!-- visual-asset: {json.dumps(meaningful_visual)} -->",
        ]
    )

    segments = ContentExtractor()._segments_from_markdown(markdown, "doc")
    embedding_inputs = [segment.content for segment in segments]

    assert len(segments) == 1
    assert segments[0].segment_type == "figure"
    assert segments[0].structured_data["asset_id"] == meaningful_visual["asset_id"]
    assert all(empty_visual["asset_id"] not in text for text in embedding_inputs)
    assert all("Visual evidence" not in text for text in embedding_inputs)


def test_html_image_noise_is_removed_but_surrounding_disclosure_is_kept() -> None:
    image_only = (
        '<div style="text-align:center"><img src="imgs/cover.jpg" '
        'alt="Image" width="64%" /></div>'
    )
    with_disclosure = (
        '<div>Employee engagement reached 82% in 2024. '
        '<img src="imgs/chart.png" alt="Image" /></div>'
    )

    assert _clean_non_table_markdown_block(image_only) == ""
    assert _clean_non_table_markdown_block(with_disclosure) == (
        "Employee engagement reached 82% in 2024."
    )

    markdown = "\n\n".join(
        [
            "<!-- Page 1 | adaptive parser route=hybrid -->",
            image_only,
            with_disclosure,
        ]
    )
    segments = ContentExtractor()._segments_from_markdown(markdown, "doc")
    contents = [segment.content for segment in segments]

    assert contents == ["Employee engagement reached 82% in 2024."]
    assert all("<img" not in content and "<div" not in content for content in contents)
    assert all("imgs/" not in content for content in contents)


def test_visual_manifest_uses_global_page_directory_not_batch_local_page_index(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "worker-output"
    page_dir = (
        output_root
        / "batches"
        / "batch_0002_pages_0008_0014"
        / "page_0010_part_03"
    )
    image_dir = page_dir / "imgs"
    image_dir.mkdir(parents=True)
    image_name = "img_in_chart_box_80_100_720_900.png"
    (image_dir / image_name).write_bytes(b"\x89PNG\r\n\x1a\n")
    payload = {
        # Paddle's page index is local to the split PDF and must not become page 3.
        "page_index": 2,
        "input_img_shape": [1000, 800, 3],
        "parsing_res_list": [
            {
                "block_id": "chart-1",
                "block_label": "chart",
                "block_order": 4,
                "block_bbox": [80, 100, 720, 900],
                "block_content": f'<img src="imgs/{image_name}" />',
                "caption": "Employee engagement by year",
            }
        ],
        "table_res_list": [
            {
                "block_id": "table-1",
                "block_bbox": [80, 100, 720, 900],
                "pred_html": "<table><tr><th>Year</th><th>Rate</th></tr>"
                "<tr><td>2024</td><td>82%</td></tr></table>",
                "rec_scores": [0.98, 0.97],
            }
        ],
    }
    (page_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    assets = promote_visual_assets(output_root, pdf_path)
    manifest = load_visual_manifest(pdf_path)

    assert len(assets) == 1
    assert assets[0]["page_number"] == 10
    assert assets[0]["source_page_index"] == 2
    assert assets[0]["batch_start_page"] == 8
    assert assets[0]["batch_end_page"] == 14
    assert assets[0]["bbox"] == [0.1, 0.1, 0.9, 0.9]
    assert manifest is not None
    assert manifest["version"] == 4
    assert manifest["pages"][0]["page_number"] == 10
    assert manifest["blocks"][0]["page_number"] == 10
    assert manifest["tables"][0]["page_number"] == 10
    assert manifest["tables"][0]["source_page_index"] == 2
