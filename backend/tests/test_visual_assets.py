import json
from pathlib import Path

from esg_encoding.content_extractor import ContentExtractor
from esg_encoding.models import ProcessingConfig
from esg_encoding.visual_assets import (
    append_visual_markers,
    normalize_bbox,
    promote_visual_assets,
    load_visual_manifest,
    safe_asset_path,
)


def test_normalize_bbox_supports_polygon_and_clamps():
    assert normalize_bbox([[10, 20], [110, 20], [110, 220]], 200, 400) == [0.05, 0.05, 0.55, 0.55]
    assert normalize_bbox([-1, 0, 4, 2]) == [0.0, 0.0, 1.0, 1.0]


def test_assets_are_deduplicated_and_path_is_allow_listed(tmp_path: Path):
    output = tmp_path / "worker" / "page_0002_part_01"
    output.mkdir(parents=True)
    (output / "chart.png").write_bytes(b"not-a-real-png-but-stable")
    (output / "chart-copy.png").write_bytes(b"not-a-real-png-but-stable")
    (output / "layout.json").write_text(json.dumps({
        "type": "chart", "image": "chart.png", "bbox": [10, 20, 110, 220],
        "page_width": 200, "page_height": 400, "title": "Emissions",
        "data": {"2024": 12}, "confidence": 0.91,
        "page_index": 1,
        "table_res_list": [{
            "pred_html": "<table><tr><th>Metric</th><th>FY24</th></tr><tr><td>Energy</td><td>12</td></tr></table>",
            "structure_score": 0.93,
            "table_ocr_pred": {"rec_scores": [0.98, 0.96, 0.95, 0.94]},
        }],
    }), encoding="utf-8")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"pdf")

    assets = promote_visual_assets(tmp_path / "worker", pdf)
    assert len(assets) == 1
    assert assets[0]["page_number"] == 2
    assert assets[0]["bbox"] == [0.05, 0.05, 0.55, 0.55]
    assert assets[0]["chart_data"] == {"2024": 12}
    resolved = safe_asset_path(pdf, assets[0]["asset_id"])
    assert resolved and resolved[0].read_bytes() == b"not-a-real-png-but-stable"
    assert safe_asset_path(pdf, "../../report.pdf") is None
    manifest = json.loads((tmp_path / "report_visual_assets" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 3
    assert "layout_records" not in manifest
    assert (tmp_path / "report_visual_assets" / manifest["layout_audit"]).is_file()
    assert manifest["tables"][0]["page_number"] == 2
    assert manifest["tables"][0]["structure_confidence"] == 0.93
    assert manifest["tables"][0]["ocr_confidence"] == 0.94


def test_manifest_cache_reuses_and_invalidates_by_mtime(tmp_path: Path):
    pdf = tmp_path / "cached.pdf"
    pdf.write_bytes(b"pdf")
    root = tmp_path / "cached_visual_assets"
    root.mkdir()
    path = root / "manifest.json"
    path.write_text('{"version":2,"assets":[]}', encoding="utf-8")
    first = load_visual_manifest(pdf)
    second = load_visual_manifest(pdf)
    assert first is second
    path.write_text('{"version":2,"assets":[{"asset_id":"new"}]}', encoding="utf-8")
    third = load_visual_manifest(pdf)
    assert third is not first
    assert third["assets"][0]["asset_id"] == "new"


def test_visual_marker_creates_searchable_segment(tmp_path: Path):
    asset = {
        "asset_id": "va_0123456789abcdef0123", "relative_path": "x.png",
        "mime_type": "image/png", "page_number": 3, "bbox": [0.1, 0.2, 0.8, 0.9],
        "caption": "Scope 1 emissions", "summary": "Emissions fell year over year",
        "ocr_text": "2023 14; 2024 12", "chart_data": {"2023": 14, "2024": 12},
        "confidence": 0.9, "parser_version": "paddleocr-vl-v1.6",
    }
    markdown = append_visual_markers("<!-- Page 1 -->\nText", [asset])
    segments = ContentExtractor(ProcessingConfig())._segments_from_markdown(markdown, "doc_test")
    visual = next(segment for segment in segments if segment.segment_type == "chart")
    assert visual.page_number == 3
    assert "Scope 1 emissions" in visual.content
    assert visual.structured_data["asset_id"] == asset["asset_id"]
