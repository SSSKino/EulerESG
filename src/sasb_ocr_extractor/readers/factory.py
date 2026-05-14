from __future__ import annotations

from sasb_ocr_extractor.config import AppConfig
from sasb_ocr_extractor.readers.base import BaseReader
from sasb_ocr_extractor.readers.command_reader import CommandReader
from sasb_ocr_extractor.readers.custom_reader import CustomReader
from sasb_ocr_extractor.readers.paddleocr_vl_reader import PaddleOCRVLReader


def normalize_reader_name(name: str | None) -> str:
    return (name or "").strip().lower().replace("_", "-")


def build_reader(name: str | None, config: AppConfig) -> BaseReader:
    normalized = normalize_reader_name(name or config.reader)
    if not normalized:
        raise ValueError(
            "OCR reader backend is required. Pass --reader command, --reader custom, "
            "or --reader paddleocr-vl, or set reader in the YAML config. "
            "No reader is selected by default."
        )
    if normalized in {"paddleocr-vl", "paddleocrvl", "paddle", "paddle-vl"}:
        return PaddleOCRVLReader(config.paddleocr)
    if normalized in {"command", "external", "shell", "external-command"}:
        return CommandReader(config.command_reader)
    if normalized in {"custom", "plugin", "import"}:
        return CustomReader(config.custom_reader)
    raise ValueError(
        f"Unsupported reader: {name or config.reader}. "
        "Supported readers: command, custom, paddleocr-vl."
    )
