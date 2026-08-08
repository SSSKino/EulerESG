"""Durable, safe visual evidence produced by PaddleOCR-VL."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VISUAL_MARKER_RE = re.compile(r"<!--\s*visual-asset\s*:\s*(\{.*?\})\s*-->", re.I | re.S)
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+['\"].*?['\"])?\)")
_COPY_CHUNK_SIZE = 1024 * 1024
_manifest_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
_manifest_cache_lock = threading.RLock()


def visual_asset_dir(pdf_path: str | Path) -> Path:
    source = Path(pdf_path)
    return source.parent / f"{source.stem}_visual_assets"


def _page_from_path(path: Path) -> int:
    match = re.search(r"page[_-]?(\d+)", str(path), flags=re.I)
    return max(1, int(match.group(1))) if match else 1


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


def _json_layout_records(root: Path) -> Iterable[dict[str, Any]]:
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                yield item
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)


def _record_image_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    stack: list[Any] = [item]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            name = Path(value.replace("\\", "/")).name.lower()
            if Path(name).suffix in IMAGE_SUFFIXES:
                keys.add(name)
                keys.add(Path(name).stem)
    return keys


def _build_layout_index(layout: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in layout:
        for key in _record_image_keys(item):
            index.setdefault(key, item)
    return index


def _percentile10(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, int((len(ordered) - 1) * 0.10))]


def _extract_table_records(layout: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for parent in layout:
        candidates = parent.get("table_res_list")
        if not isinstance(candidates, list):
            candidates = [parent] if parent.get("pred_html") else []
        for table in candidates:
            if not isinstance(table, dict):
                continue
            html = str(table.get("pred_html") or "").strip()
            if not html:
                continue
            page_index = table.get("page_index", parent.get("page_index", 0))
            ocr = table.get("table_ocr_pred") if isinstance(table.get("table_ocr_pred"), dict) else {}
            raw_scores = table.get("rec_scores") or ocr.get("rec_scores") or []
            scores = []
            for value in raw_scores if isinstance(raw_scores, list) else []:
                try:
                    scores.append(float(value))
                except (TypeError, ValueError):
                    pass
            structure_score = table.get("structure_score", table.get("score"))
            try:
                structure_score = float(structure_score) if structure_score is not None else None
            except (TypeError, ValueError):
                structure_score = None
            identity = hashlib.sha256(f"{page_index}:{html}".encode("utf-8")).hexdigest()
            if identity in seen:
                continue
            seen.add(identity)
            records.append({
                "page_number": max(1, int(page_index or 0) + 1),
                "pred_html": html,
                "bbox": table.get("block_bbox") or table.get("bbox"),
                "cell_box_list": table.get("cell_box_list") or table.get("rec_boxes") or ocr.get("rec_boxes") or [],
                "rec_texts": table.get("rec_texts") or ocr.get("rec_texts") or [],
                "rec_scores": scores,
                "structure_confidence": structure_score,
                "ocr_confidence": _percentile10(scores),
                "parse_pass": 1,
            })
    return records


def _metadata_for_image(source: Path, layout_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = layout_index.get(source.name.lower()) or layout_index.get(source.stem.lower()) or {}
    width = item.get("width") or item.get("image_width") or item.get("page_width")
    height = item.get("height") or item.get("image_height") or item.get("page_height")
    bbox = None
    for key in ("bbox", "box", "coordinate", "poly", "polygon"):
        if item.get(key) is not None:
            try:
                bbox = normalize_bbox(item[key], float(width) if width else None, float(height) if height else None)
            except (TypeError, ValueError):
                bbox = None
            if bbox:
                break
    caption = str(item.get("caption") or item.get("title") or "").strip()
    summary = str(item.get("summary") or item.get("description") or "").strip()
    ocr_text = str(item.get("ocr_text") or item.get("text") or item.get("content") or "").strip()
    is_chart = "chart" in str(item.get("type") or item.get("label") or "").lower()
    chart_data = (item.get("chart_data") or item.get("data")) if is_chart else None
    try:
        confidence = float(item.get("confidence") or item.get("score") or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "bbox": bbox,
        "caption": caption,
        "summary": summary,
        "ocr_text": ocr_text,
        "chart_data": chart_data if isinstance(chart_data, (dict, list)) else None,
        "confidence": max(0.0, min(1.0, confidence)),
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


def promote_visual_assets(output_dir: str | Path, pdf_path: str | Path) -> list[dict[str, Any]]:
    """Deduplicate worker images into a report-owned directory and write a manifest."""
    root = Path(output_dir)
    destination = visual_asset_dir(pdf_path)
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    layout = list(_json_layout_records(root))
    layout_index = _build_layout_index(layout)
    table_records = _extract_table_records(layout)

    for source in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES):
        staging = destination / f".asset.{os.getpid()}.{threading.get_ident()}.tmp"
        digest = _stream_copy_and_hash(source, staging)
        if digest in seen:
            staging.unlink(missing_ok=True)
            continue
        seen.add(digest)
        asset_id = f"va_{digest[:20]}"
        target = destination / f"{asset_id}{source.suffix.lower()}"
        if not target.exists():
            os.replace(staging, target)
        else:
            staging.unlink(missing_ok=True)
        metadata = _metadata_for_image(source, layout_index)
        records.append({
            "asset_id": asset_id,
            "relative_path": target.name,
            "mime_type": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            "page_number": _page_from_path(source),
            **metadata,
            "parser_version": "paddleocr-vl-v1.6",
            "sha256": digest,
        })

    # Keep audit data separate so normal manifest reads stay small.
    audit_path = destination / "layout_audit.json"
    audit_tmp = destination / f"layout_audit.{os.getpid()}.tmp"
    audit_tmp.write_text(json.dumps(layout, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(audit_tmp, audit_path)
    manifest = {"version": 3, "assets": records, "tables": table_records, "layout_audit": audit_path.name}
    manifest_path = destination / "manifest.json"
    temporary_manifest = destination / f"manifest.{os.getpid()}.tmp"
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)
    invalidate_visual_manifest(pdf_path)
    return records


def append_visual_markers(markdown: str, assets: list[dict[str, Any]]) -> str:
    if not assets:
        return markdown
    markers = []
    for asset in assets:
        public = {k: asset.get(k) for k in (
            "asset_id", "relative_path", "mime_type", "page_number", "bbox", "caption",
            "summary", "ocr_text", "chart_data", "confidence", "parser_version"
        )}
        markers.append(f"<!-- visual-asset: {json.dumps(public, ensure_ascii=False)} -->")
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
