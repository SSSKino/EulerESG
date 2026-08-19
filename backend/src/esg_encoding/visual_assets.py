"""Durable, safe visual evidence produced by PaddleOCR-VL."""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VISUAL_MARKER_RE = re.compile(r"<!--\s*visual-asset\s*:\s*(\{.*?\})\s*-->", re.I | re.S)
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"].*?['\"])?\)")
HTML_IMAGE_RE = re.compile(
    r"<img\b[^>]*?\bsrc\s*=\s*(?:['\"]([^'\"]+)['\"]|([^\s>]+))[^>]*>",
    re.I | re.S,
)
HTML_TAG_RE = re.compile(r"<[^>]+>", re.S)
_PAGE_DIR_RE = re.compile(r"^page[_-]?(\d+)(?:[_-].*)?$", re.I)
_BATCH_DIR_RE = re.compile(r"^(?P<batch>.+?)_pages_(?P<start>\d+)_(?P<end>\d+)$", re.I)
_SEMANTIC_VISUAL_TYPES = {
    "chart", "figure", "image", "illustration", "picture", "table",
}
_DECORATIVE_TYPES = {
    "background", "background_image", "footer", "footer_image", "header",
    "header_image", "icon", "logo", "number", "page_number", "seal",
    "watermark",
}
_CAPTION_TYPES = {
    "caption", "chart_caption", "chart_title", "figure_caption", "figure_title",
    "image_caption", "image_title", "table_caption", "table_title",
}
_DIAGNOSTIC_IMAGE_RE = re.compile(
    r"(?:layout(?:_det|_order)?_res|overall_ocr_res|text_paragraphs_ocr_res|"
    r"doc_preprocessor_res|formula_res_region|seal_res_region|table_cell_img)",
    re.I,
)
_SEMANTIC_CROP_RE = re.compile(
    r"img_in_(?P<label>[a-z0-9_]+)_box_"
    r"(?P<x1>-?\d+(?:\.\d+)?)_(?P<y1>-?\d+(?:\.\d+)?)_"
    r"(?P<x2>-?\d+(?:\.\d+)?)_(?P<y2>-?\d+(?:\.\d+)?)",
    re.I,
)
_COPY_CHUNK_SIZE = 1024 * 1024
_manifest_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
_manifest_cache_lock = threading.RLock()


def visual_asset_dir(pdf_path: str | Path) -> Path:
    source = Path(pdf_path)
    return source.parent / f"{source.stem}_visual_assets"


def _page_number_from_path(path: Path) -> int | None:
    """Read the worker's *global* page number from its page directory.

    PaddleOCR receives a split batch PDF, so the JSON ``page_index`` starts at
    zero for every batch.  ``parse_core`` deliberately persists every result
    below ``page_XXXX_part_YY``; that directory is the authoritative report
    page and must win over the batch-local JSON value.
    """
    for part in reversed(path.parts):
        match = _PAGE_DIR_RE.match(part)
        if match:
            return max(1, int(match.group(1)))
    return None


def _page_from_path(path: Path) -> int:
    return _page_number_from_path(path) or 1


def _batch_context(path: Path) -> dict[str, Any]:
    for part in reversed(path.parts):
        match = _BATCH_DIR_RE.match(part)
        if match:
            return {
                "batch_id": match.group("batch"),
                "batch_start_page": int(match.group("start")),
                "batch_end_page": int(match.group("end")),
            }
    return {"batch_id": None, "batch_start_page": None, "batch_end_page": None}


def normalize_bbox(value: Any, width: float | None = None, height: float | None = None) -> list[float] | None:
    """Return an [x1,y1,x2,y2] box normalized to 0..1."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    numbers: list[float] = []
    if isinstance(value[0], (list, tuple)):
        points = [p for p in value if isinstance(p, (list, tuple)) and len(p) >= 2]
        if not points:
            return None
        numbers = [min(float(p[0]) for p in points), min(float(p[1]) for p in points),
                   max(float(p[0]) for p in points), max(float(p[1]) for p in points)]
    elif len(value) >= 4:
        numbers = [float(v) for v in value[:4]]
    else:
        return None
    if width and height and max(numbers) > 1.0:
        numbers = [numbers[0] / width, numbers[1] / height, numbers[2] / width, numbers[3] / height]
    return [round(max(0.0, min(1.0, n)), 6) for n in numbers]


def _bbox_numbers(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    try:
        if isinstance(value[0], (list, tuple)):
            points = [p for p in value if isinstance(p, (list, tuple)) and len(p) >= 2]
            if not points:
                return None
            return [
                min(float(p[0]) for p in points),
                min(float(p[1]) for p in points),
                max(float(p[0]) for p in points),
                max(float(p[1]) for p in points),
            ]
        if len(value) >= 4:
            return [float(v) for v in value[:4]]
    except (TypeError, ValueError):
        return None
    return None


def _record_bbox(value: Any, width: float | None, height: float | None) -> list[float] | None:
    numbers = _bbox_numbers(value)
    if numbers is None:
        return None
    if max(numbers) > 1.0 and not (width and height):
        # Clamping pixel coordinates without dimensions silently produces
        # [1,1,1,1], which is worse than an explicit unknown box.
        return None
    return normalize_bbox(numbers, width, height)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _canonical_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_type(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "unknown").strip().lower()).strip("_") or "unknown"


def _json_payloads(root: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield top-level PaddleOCR result payloads without flattening context.

    The old recursive walk detached nested blocks from their page and source
    JSON.  It also indexed image basenames globally, so two pages containing
    ``imgs/chart.jpg`` could be assigned each other's metadata.
    """
    for path in sorted(root.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        values = payload if isinstance(payload, list) else [payload]
        for value in values:
            if not isinstance(value, dict):
                continue
            # Some serving/export variants wrap the result in ``res``.
            wrapped = value.get("res")
            if isinstance(wrapped, dict) and not value.get("parsing_res_list"):
                value = {**value, **wrapped}
            yield path, value


def _page_dimensions(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    containers = [
        payload,
        payload.get("layout_det_res"),
        payload.get("overall_ocr_res"),
        payload.get("doc_preprocessor_res"),
    ]
    for item in containers:
        if not isinstance(item, dict):
            continue
        width = _coerce_float(item.get("width") or item.get("image_width") or item.get("page_width"))
        height = _coerce_float(item.get("height") or item.get("image_height") or item.get("page_height"))
        if width and height:
            return width, height
        shape = item.get("input_img_shape") or item.get("image_shape") or item.get("shape")
        if isinstance(shape, (list, tuple)) and len(shape) >= 2:
            shape_height, shape_width = _coerce_float(shape[0]), _coerce_float(shape[1])
            if shape_width and shape_height:
                return shape_width, shape_height
    return None, None


def _image_references(item: Any) -> list[str]:
    refs: list[str] = []
    stack = [item]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            refs.extend(match.group(2) for match in MARKDOWN_IMAGE_RE.finditer(value))
            refs.extend((match.group(1) or match.group(2)) for match in HTML_IMAGE_RE.finditer(value))
            candidate = value.strip().strip("'\"")
            parsed = urlparse(candidate)
            suffix = Path(unquote(parsed.path).replace("\\", "/")).suffix.lower()
            if suffix in IMAGE_SUFFIXES and not parsed.scheme.lower() in {"data", "http", "https"}:
                refs.append(candidate)
    return list(dict.fromkeys(ref for ref in refs if ref))


def _resolve_image_reference(reference: str, json_path: Path, root: Path) -> Path | None:
    parsed = urlparse(str(reference).strip())
    if parsed.scheme.lower() in {"data", "http", "https"}:
        return None
    raw = unquote(parsed.path).replace("\\", "/")
    if not raw:
        return None
    candidate_path = Path(raw)
    candidates = (
        [candidate_path]
        if candidate_path.is_absolute()
        else [json_path.parent / candidate_path, root / candidate_path]
    )
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        if resolved.is_file() and resolved.suffix.lower() in IMAGE_SUFFIXES:
            return resolved
    return None


def _meaningful_text(value: Any) -> str:
    text = str(value or "")
    text = MARKDOWN_IMAGE_RE.sub(lambda match: match.group(1) or "", text)
    text = HTML_IMAGE_RE.sub("", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n|#*_-:")
    return text


def _text_fingerprint(value: Any) -> str | None:
    text = unicodedata.normalize("NFKC", _meaningful_text(value)).casefold()
    text = re.sub(r"[^\w%+.-]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bbox_iou(first: list[float] | None, second: list[float] | None) -> float:
    if not first or not second:
        return 0.0
    x1, y1 = max(first[0], second[0]), max(first[1], second[1])
    x2, y2 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection <= 0:
        return 0.0
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def _percentile10(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, int((len(ordered) - 1) * 0.10))]


def _page_record(root: Path, json_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    source_page_index = _coerce_int(payload.get("page_index"))
    batch = _batch_context(json_path)
    global_page = _page_number_from_path(json_path)
    if global_page is None:
        batch_start = _coerce_int(batch.get("batch_start_page"))
        global_page = max(
            1,
            (batch_start + (source_page_index or 0))
            if batch_start is not None
            else (source_page_index or 0) + 1,
        )
    width, height = _page_dimensions(payload)
    return {
        "page_number": global_page,
        "source_page_index": source_page_index,
        "page_count": _coerce_int(payload.get("page_count")),
        "page_width": width,
        "page_height": height,
        "source_json_path": _relative_path(json_path, root),
        "source_document_path": str(payload.get("input_path") or ""),
        **batch,
    }


def _block_record(
    root: Path,
    json_path: Path,
    page: dict[str, Any],
    item: dict[str, Any],
    list_order: int,
) -> dict[str, Any]:
    width, height = page.get("page_width"), page.get("page_height")
    bbox_value = next(
        (
            item.get(key)
            for key in ("block_bbox", "bbox", "box", "coordinate", "poly", "polygon")
            if item.get(key) is not None
        ),
        None,
    )
    raw_bbox = _bbox_numbers(bbox_value)
    block_type = _normalized_type(item.get("block_label") or item.get("type") or item.get("label"))
    content = item.get("block_content") or item.get("content") or item.get("text") or ""
    references = _image_references(item)
    resolved = [
        _canonical_path(path)
        for reference in references
        if (path := _resolve_image_reference(reference, json_path, root)) is not None
    ]
    reading_order = _coerce_int(item.get("block_order"))
    if reading_order is None:
        reading_order = list_order
    confidence = _coerce_float(item.get("confidence") if item.get("confidence") is not None else item.get("score"))
    chart_data = None
    if block_type == "chart":
        chart_data = item.get("chart_data") or item.get("data")
    if not isinstance(chart_data, (dict, list)):
        chart_data = None
    caption = _meaningful_text(item.get("caption") or item.get("title"))
    summary = _meaningful_text(item.get("summary") or item.get("description"))
    meaningful_content = _meaningful_text(content)
    ocr_text = _meaningful_text(item.get("ocr_text")) or meaningful_content
    return {
        **page,
        "block_id": item.get("block_id", item.get("index")),
        "block_type": block_type,
        "reading_order": reading_order,
        "bbox": _record_bbox(bbox_value, width, height),
        "bbox_pixels": raw_bbox if raw_bbox and max(raw_bbox) > 1.0 else None,
        "caption": caption,
        "summary": summary,
        "ocr_text": ocr_text,
        "chart_data": chart_data,
        "confidence": max(0.0, min(1.0, confidence)) if confidence is not None else None,
        "text_fingerprint": _text_fingerprint(content),
        "source_image_paths": references,
        "_resolved_image_paths": resolved,
    }


def _ocr_regions(
    payload: dict[str, Any],
    width: float | None,
    height: float | None,
) -> list[tuple[list[float], str, float | None]]:
    overall = payload.get("overall_ocr_res")
    if not isinstance(overall, dict):
        return []
    texts = overall.get("rec_texts") or []
    boxes = overall.get("rec_boxes") or overall.get("rec_polys") or []
    scores = overall.get("rec_scores") or []
    if not isinstance(texts, list) or not isinstance(boxes, list):
        return []
    regions: list[tuple[list[float], str, float | None]] = []
    for index, (text_value, box_value) in enumerate(zip(texts, boxes)):
        box = _record_bbox(box_value, width, height)
        text = _meaningful_text(text_value)
        if not box or not text:
            continue
        score = _coerce_float(scores[index]) if isinstance(scores, list) and index < len(scores) else None
        regions.append((box, text, score))
    return regions


def _enrich_visual_blocks(blocks: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    captions = [block for block in blocks if block.get("block_type") in _CAPTION_TYPES and block.get("ocr_text")]
    width = blocks[0].get("page_width") if blocks else None
    height = blocks[0].get("page_height") if blocks else None
    ocr_regions = _ocr_regions(payload, width, height)
    for block in blocks:
        if block.get("block_type") not in _SEMANTIC_VISUAL_TYPES:
            continue
        if not block.get("caption") and captions:
            block_type = str(block.get("block_type") or "")
            compatible = [
                candidate
                for candidate in captions
                if candidate.get("block_type") == "caption"
                or (
                    block_type == "table"
                    and str(candidate.get("block_type") or "").startswith("table_")
                )
                or (
                    block_type != "table"
                    and not str(candidate.get("block_type") or "").startswith("table_")
                )
            ]
            order = int(block.get("reading_order") or 0)
            nearby = sorted(
                compatible,
                key=lambda candidate: abs(int(candidate.get("reading_order") or 0) - order),
            )
            if nearby:
                candidate = nearby[0]
                if abs(int(candidate.get("reading_order") or 0) - order) <= 2:
                    block["caption"] = candidate.get("ocr_text") or ""
        if not block.get("ocr_text") and block.get("bbox"):
            matched = [
                (text, score)
                for box, text, score in ocr_regions
                if _bbox_iou(block["bbox"], box) > 0.0
                or (
                    block["bbox"][0] <= (box[0] + box[2]) / 2 <= block["bbox"][2]
                    and block["bbox"][1] <= (box[1] + box[3]) / 2 <= block["bbox"][3]
                )
            ]
            if matched:
                block["ocr_text"] = " ".join(text for text, _ in matched)
                scores = [score for _, score in matched if score is not None]
                if scores:
                    block["confidence"] = _percentile10(scores)


def _table_record(
    table: dict[str, Any],
    page: dict[str, Any],
    *,
    reading_order: int | None = None,
    block_id: Any = None,
) -> dict[str, Any] | None:
    ocr = table.get("table_ocr_pred") if isinstance(table.get("table_ocr_pred"), dict) else {}
    content = table.get("pred_html") or table.get("block_content") or table.get("content") or ""
    pred_html = str(content).strip()
    rec_texts = table.get("rec_texts") or ocr.get("rec_texts") or []
    if not pred_html and isinstance(rec_texts, list):
        pred_html = " ".join(_meaningful_text(value) for value in rec_texts if _meaningful_text(value))
    if not pred_html:
        return None
    bbox_value = table.get("block_bbox") or table.get("bbox") or table.get("box")
    raw_bbox = _bbox_numbers(bbox_value)
    raw_cells = table.get("cell_box_list") or table.get("rec_boxes") or ocr.get("rec_boxes") or []
    raw_scores = table.get("rec_scores") or ocr.get("rec_scores") or []
    scores = (
        [score for value in raw_scores if (score := _coerce_float(value)) is not None]
        if isinstance(raw_scores, list)
        else []
    )
    structure_score = _coerce_float(
        table.get("structure_score")
        if table.get("structure_score") is not None
        else table.get("score")
    )
    references = _image_references(table)
    json_path = Path(str(page.get("_source_json_absolute") or ""))
    root = Path(str(page.get("_root_absolute") or ""))
    resolved = [
        _canonical_path(path)
        for reference in references
        if json_path and root and (path := _resolve_image_reference(reference, json_path, root)) is not None
    ]
    return {
        **{key: value for key, value in page.items() if not key.startswith("_")},
        "block_id": table.get("block_id", block_id),
        "block_type": "table",
        "reading_order": (
            _coerce_int(table.get("block_order"))
            if table.get("block_order") is not None
            else reading_order
        ),
        "pred_html": pred_html,
        "bbox": _record_bbox(bbox_value, page.get("page_width"), page.get("page_height")),
        "bbox_pixels": raw_bbox if raw_bbox and max(raw_bbox) > 1.0 else None,
        # Paddle table cells may be relative to the table crop rather than the
        # full page. Keep their native coordinates instead of mis-normalising
        # them; only the layout block bbox is guaranteed to be page-relative.
        "cell_box_list": raw_cells if isinstance(raw_cells, list) else [],
        "rec_texts": rec_texts if isinstance(rec_texts, list) else [],
        "rec_scores": scores,
        "structure_confidence": structure_score,
        "ocr_confidence": _percentile10(scores),
        "text_fingerprint": _text_fingerprint(pred_html),
        "source_image_paths": references,
        "asset_ids": [],
        "parse_pass": 1,
        "_resolved_image_paths": resolved,
    }


def _same_table(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if first.get("page_number") != second.get("page_number"):
        return False
    if first.get("text_fingerprint") and first.get("text_fingerprint") == second.get("text_fingerprint"):
        if not first.get("bbox") or not second.get("bbox"):
            return True
        return _bbox_iou(first.get("bbox"), second.get("bbox")) >= 0.3
    return _bbox_iou(first.get("bbox"), second.get("bbox")) >= 0.6


def _merge_table(first: dict[str, Any], second: dict[str, Any]) -> None:
    for key, value in second.items():
        if key == "_resolved_image_paths":
            first[key] = list(dict.fromkeys([*first.get(key, []), *value]))
        elif key == "source_image_paths":
            first[key] = list(dict.fromkeys([*first.get(key, []), *value]))
        elif first.get(key) in (None, "", [], {}):
            first[key] = value


def _adapt_paddleocr_v16(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    pages: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for json_path, payload in _json_payloads(root):
        page = _page_record(root, json_path, payload)
        page["_source_json_absolute"] = str(json_path.resolve())
        page["_root_absolute"] = str(root.resolve())
        pages.append({key: value for key, value in page.items() if not key.startswith("_")})

        raw_blocks = payload.get("parsing_res_list")
        if not isinstance(raw_blocks, list):
            # Compatibility with older/hand-authored exports that stored one
            # visual block at the JSON root.
            raw_blocks = [payload] if (
                payload.get("block_label") or payload.get("type") or payload.get("label")
            ) else []
        page_blocks = [
            _block_record(root, json_path, page, item, order)
            for order, item in enumerate(raw_blocks)
            if isinstance(item, dict)
        ]
        _enrich_visual_blocks(page_blocks, payload)
        blocks.extend(page_blocks)

        candidates: list[dict[str, Any]] = []
        raw_tables = payload.get("table_res_list")
        if isinstance(raw_tables, list):
            for table in raw_tables:
                if isinstance(table, dict) and (record := _table_record(table, page)) is not None:
                    candidates.append(record)
        for block, raw_block in zip(page_blocks, raw_blocks):
            if block.get("block_type") != "table" or not isinstance(raw_block, dict):
                continue
            record = _table_record(
                raw_block,
                page,
                reading_order=block.get("reading_order"),
                block_id=block.get("block_id"),
            )
            if record is not None:
                candidates.append(record)
        for candidate in candidates:
            existing = next((record for record in tables if _same_table(record, candidate)), None)
            if existing is None:
                tables.append(candidate)
            else:
                _merge_table(existing, candidate)

    for table in tables:
        identity = ":".join(
            str(value or "")
            for value in (
                table.get("source_json_path"), table.get("page_number"),
                table.get("bbox"), table.get("text_fingerprint"),
            )
        )
        table["table_id"] = f"vt_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    return pages, blocks, tables


def _metadata_for_image(source: Path, block: dict[str, Any]) -> dict[str, Any]:
    return {
        key: block.get(key)
        for key in (
            "page_number", "source_page_index", "page_width", "page_height", "bbox",
            "bbox_pixels", "block_id", "block_type", "reading_order", "caption", "summary",
            "ocr_text", "chart_data", "confidence", "source_json_path", "batch_id",
            "batch_start_page", "batch_end_page", "text_fingerprint",
        )
    }


def _stream_copy_and_hash(source: Path, temporary: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as src, temporary.open("wb") as dst:
        while True:
            chunk = src.read(_COPY_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    return digest.hexdigest()


def load_visual_manifest(pdf_path: str | Path) -> dict[str, Any] | None:
    manifest_path = visual_asset_dir(pdf_path) / "manifest.json"
    try:
        stat = manifest_path.stat()
    except OSError:
        return None
    key = str(manifest_path.resolve())
    signature = (stat.st_mtime_ns, stat.st_size)
    with _manifest_cache_lock:
        cached = _manifest_cache.get(key)
        if cached and cached[:2] == signature:
            return cached[2]
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    with _manifest_cache_lock:
        _manifest_cache[key] = (signature[0], signature[1], payload)
    return payload


def invalidate_visual_manifest(pdf_path: str | Path) -> None:
    key = str((visual_asset_dir(pdf_path) / "manifest.json").resolve())
    with _manifest_cache_lock:
        _manifest_cache.pop(key, None)


def _fallback_crop_block(source: Path, pages: list[dict[str, Any]]) -> dict[str, Any] | None:
    match = _SEMANTIC_CROP_RE.search(source.stem)
    if not match:
        return None
    block_type = _normalized_type(match.group("label"))
    if block_type not in _SEMANTIC_VISUAL_TYPES or block_type in _DECORATIVE_TYPES:
        return None
    page_number = _page_from_path(source)
    page = next((item for item in pages if item.get("page_number") == page_number), None) or {
        "page_number": page_number,
        "source_page_index": None,
        "page_width": None,
        "page_height": None,
        "source_json_path": None,
        "batch_id": _batch_context(source).get("batch_id"),
        "batch_start_page": _batch_context(source).get("batch_start_page"),
        "batch_end_page": _batch_context(source).get("batch_end_page"),
    }
    bbox_pixels = [float(match.group(key)) for key in ("x1", "y1", "x2", "y2")]
    return {
        **page,
        "bbox": _record_bbox(bbox_pixels, page.get("page_width"), page.get("page_height")),
        "bbox_pixels": bbox_pixels,
        "block_id": None,
        "block_type": block_type,
        "reading_order": None,
        "caption": "",
        "summary": "",
        "ocr_text": "",
        "chart_data": None,
        "confidence": None,
        "text_fingerprint": None,
        "source_image_paths": [source.name],
        "_resolved_image_paths": [_canonical_path(source)],
    }


def _select_block_for_image(
    source: Path,
    blocks: list[dict[str, Any]],
    pages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    canonical = _canonical_path(source)
    page_number = _page_number_from_path(source)
    exact = [block for block in blocks if canonical in block.get("_resolved_image_paths", [])]
    candidates = exact
    if not candidates and page_number is not None:
        # Some Paddle exports store only a basename. Restrict fallback lookup to
        # the page so identical ``imgs/chart.jpg`` names cannot cross-link.
        basename = source.name.casefold()
        candidates = [
            block
            for block in blocks
            if block.get("page_number") == page_number
            and any(
                Path(urlparse(ref).path).name.casefold() == basename
                for ref in block.get("source_image_paths", [])
            )
        ]
    fallback = _fallback_crop_block(source, pages)
    if not candidates and fallback is not None:
        # PaddleOCR-VL v1.6 JSON intentionally omits the PIL image object. Its
        # saved crop filename carries the block label and pixel bbox, allowing
        # an exact page + IoU association back to ``parsing_res_list``.
        candidates = [
            block
            for block in blocks
            if block.get("page_number") == fallback.get("page_number")
            and block.get("block_type") == fallback.get("block_type")
            and _bbox_iou(block.get("bbox"), fallback.get("bbox")) >= 0.6
        ]
    if not candidates:
        return fallback
    candidates = [block for block in candidates if block.get("block_type") not in _DECORATIVE_TYPES]
    if not candidates:
        return None
    candidates.sort(
        key=lambda block: (
            block.get("page_number") == page_number,
            block.get("block_type") in _SEMANTIC_VISUAL_TYPES,
            bool(block.get("chart_data")),
            bool(block.get("caption") or block.get("summary") or block.get("ocr_text")),
        ),
        reverse=True,
    )
    selected = candidates[0]
    # Raw <img>/Markdown inside a text/div block is an export placeholder, not
    # a visual evidence record. Real crops receive a semantic layout label.
    if selected.get("block_type") not in _SEMANTIC_VISUAL_TYPES:
        return None
    return selected


def _is_searchable_asset(record: dict[str, Any]) -> bool:
    if record.get("searchable") is False:
        return False
    return bool(
        _meaningful_text(record.get("caption"))
        or _meaningful_text(record.get("summary"))
        or _meaningful_text(record.get("ocr_text"))
        or record.get("chart_data")
    )


def _link_tables_to_assets(tables: list[dict[str, Any]], assets: list[dict[str, Any]]) -> None:
    for table in tables:
        table_paths = set(table.get("_resolved_image_paths", []))
        for asset in assets:
            if asset.get("page_number") != table.get("page_number"):
                continue
            same_source = asset.get("_source_image_absolute") in table_paths
            same_box = _bbox_iou(asset.get("bbox"), table.get("bbox")) >= 0.6
            same_text = bool(
                asset.get("text_fingerprint")
                and asset.get("text_fingerprint") == table.get("text_fingerprint")
            )
            same_block = bool(
                asset.get("block_type") == "table"
                and asset.get("block_id") is not None
                and asset.get("block_id") == table.get("block_id")
            )
            if not (same_source or same_box or same_text or same_block):
                continue
            table.setdefault("asset_ids", []).append(asset["asset_id"])
            table["asset_ids"] = list(dict.fromkeys(table["asset_ids"]))
            source_path = asset.get("source_image_path")
            if source_path:
                table.setdefault("source_image_paths", []).append(source_path)
                table["source_image_paths"] = list(dict.fromkeys(table["source_image_paths"]))
            asset.setdefault("table_ids", []).append(table["table_id"])
            asset["table_ids"] = list(dict.fromkeys(asset["table_ids"]))


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _public_block_record(record: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "page_number", "source_page_index", "page_width", "page_height",
        "source_json_path", "batch_id", "batch_start_page", "batch_end_page",
        "block_id", "block_type", "reading_order", "bbox", "bbox_pixels",
        "confidence",
    ]
    if record.get("block_type") in _SEMANTIC_VISUAL_TYPES | _CAPTION_TYPES:
        keys.extend([
            "caption", "summary", "ocr_text", "chart_data", "text_fingerprint",
            "source_image_paths",
        ])
    return {key: record.get(key) for key in keys}


def promote_visual_assets(output_dir: str | Path, pdf_path: str | Path) -> list[dict[str, Any]]:
    """Promote semantic PaddleOCR-VL crops and persist their page provenance.

    Only images linked to a v1.6 ``parsing_res_list`` visual block (or carrying
    Paddle's semantic crop filename) are promoted. Intermediate layout/OCR
    visualisations and raw image placeholders are deliberately ignored.
    """
    root = Path(output_dir)
    destination = visual_asset_dir(pdf_path)
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    seen_occurrences: set[str] = set()
    page_records, block_records, table_records = _adapt_paddleocr_v16(root)

    for source in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES):
        if _DIAGNOSTIC_IMAGE_RE.search(source.stem):
            continue
        block = _select_block_for_image(source, block_records, page_records)
        if block is None or block.get("block_type") in _DECORATIVE_TYPES:
            continue
        try:
            if source.stat().st_size <= 0:
                continue
        except OSError:
            continue
        staging = destination / f".asset.{os.getpid()}.{threading.get_ident()}.tmp"
        digest = _stream_copy_and_hash(source, staging)
        source_relative = _relative_path(source, root)
        occurrence_identity = json.dumps(
            [
                digest,
                block.get("page_number"),
                block.get("bbox"),
                block.get("block_type"),
                block.get("reading_order"),
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        occurrence_digest = hashlib.sha256(occurrence_identity.encode("utf-8")).hexdigest()
        if occurrence_digest in seen_occurrences:
            staging.unlink(missing_ok=True)
            continue
        seen_occurrences.add(occurrence_digest)
        asset_id = f"va_{occurrence_digest[:20]}"
        # Reuse immutable bytes while keeping one metadata record per page
        # occurrence. The same chart on two pages must retain both locations.
        target = destination / f"blob_{digest[:20]}{source.suffix.lower()}"
        if not target.exists():
            os.replace(staging, target)
        else:
            staging.unlink(missing_ok=True)
        metadata = _metadata_for_image(source, block)
        record = {
            "asset_id": asset_id,
            "relative_path": target.name,
            "mime_type": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            **metadata,
            "parser_version": "paddleocr-vl-v1.6",
            "sha256": digest,
            "source_image_path": source_relative,
            "table_ids": [],
            "_source_image_absolute": _canonical_path(source),
        }
        record["searchable"] = _is_searchable_asset(record)
        records.append(record)

    _link_tables_to_assets(table_records, records)
    public_records = [_public_record(record) for record in records]
    public_blocks = [_public_block_record(record) for record in block_records]
    public_tables = [_public_record(record) for record in table_records]

    # Keep the default manifest compact but structured. Raw JSON payloads remain
    # optional audit material; the durable records always retain their source
    # JSON path, batch, global page and page dimensions.
    audit_path = destination / "layout_audit.json"
    keep_layout_audit = str(os.getenv("PADDLEOCR_KEEP_LAYOUT_AUDIT", "false")).strip().lower() in {
        "1", "true", "yes", "y", "on"
    }
    manifest = {
        "version": 4,
        "parser_schema": "paddleocr-vl-v1.6",
        "pages": page_records,
        "blocks": public_blocks,
        "assets": public_records,
        "tables": public_tables,
    }
    if keep_layout_audit:
        audit_tmp = destination / f"layout_audit.{os.getpid()}.tmp"
        audit_payload = {
            "adapter": "paddleocr-vl-v1.6",
            "pages": page_records,
            "blocks": public_blocks,
            "tables": public_tables,
        }
        audit_tmp.write_text(json.dumps(audit_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(audit_tmp, audit_path)
        manifest["layout_audit"] = audit_path.name
    else:
        audit_path.unlink(missing_ok=True)
    manifest_path = destination / "manifest.json"
    temporary_manifest = destination / f"manifest.{os.getpid()}.tmp"
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)
    invalidate_visual_manifest(pdf_path)
    return public_records


def append_visual_markers(markdown: str, assets: list[dict[str, Any]]) -> str:
    if not assets:
        return markdown
    markers = []
    for asset in assets:
        # A crop with no caption, OCR, summary or structured chart content is
        # still available in the manifest/UI, but embedding its generated
        # "Visual evidence va_..." fallback only adds retrieval noise.
        if not _is_searchable_asset(asset):
            continue
        public = {k: asset.get(k) for k in (
            "asset_id", "relative_path", "mime_type", "page_number", "bbox", "caption",
            "summary", "ocr_text", "chart_data", "confidence", "parser_version",
            "page_width", "page_height", "block_type", "reading_order", "table_ids",
        )}
        markers.append(f"<!-- visual-asset: {json.dumps(public, ensure_ascii=False)} -->")
    if not markers:
        return markdown
    return markdown.rstrip() + "\n\n" + "\n\n".join(markers)


def parse_visual_marker(block: str) -> dict[str, Any] | None:
    match = VISUAL_MARKER_RE.search(block or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
        return value if isinstance(value, dict) and value.get("asset_id") else None
    except Exception:
        return None


def safe_asset_path(pdf_path: str | Path, asset_id: str) -> tuple[Path, dict[str, Any]] | None:
    if not re.fullmatch(r"va_[a-f0-9]{20}", asset_id or ""):
        return None
    root = visual_asset_dir(pdf_path).resolve()
    manifest = load_visual_manifest(pdf_path)
    if not manifest:
        return None
    record = next((x for x in manifest.get("assets", []) if x.get("asset_id") == asset_id), None)
    if not isinstance(record, dict):
        return None
    candidate = (root / str(record.get("relative_path") or "")).resolve()
    if candidate.parent != root or not candidate.is_file():
        return None
    return candidate, record
