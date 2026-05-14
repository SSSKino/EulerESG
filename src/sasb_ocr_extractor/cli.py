from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from rich.console import Console

from sasb_ocr_extractor.config import load_config
from sasb_ocr_extractor.extractors.pipeline import SasbExtractionPipeline
from sasb_ocr_extractor.readers import build_reader
from sasb_ocr_extractor.utils.io import write_json

console = Console()


def _parse_key_value(values: list[str] | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Empty key in option: {item}")
        if value.lower() in {"true", "false"}:
            parsed[key] = value.lower() == "true"
        else:
            try:
                parsed[key] = int(value)
            except ValueError:
                try:
                    parsed[key] = float(value)
                except ValueError:
                    parsed[key] = value
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract SASB metric JSON from a PDF/image file.")
    parser.add_argument("--input", "-i", required=True, help="Input PDF/image file")
    parser.add_argument("--output", "-o", required=True, help="Output JSON path")
    parser.add_argument("--workdir", default=os.getenv("SASB_WORKDIR", "work"), help="Intermediate OCR/cache directory")
    parser.add_argument("--config", help="Optional YAML config file")
    parser.add_argument(
        "--reader",
        choices=["paddleocr-vl", "command", "custom"],
        help="Reader backend. Use command/custom to swap OCR models without editing extractor code.",
    )
    parser.add_argument("--pipeline-version", default=None, help="PaddleOCR-VL pipeline version, default v1.5")
    parser.add_argument("--model-name-or-path", default=None, help="Optional model/checkpoint name or local path for PaddleOCR-VL-compatible readers")
    parser.add_argument(
        "--model-kwarg",
        action="append",
        default=[],
        help="Extra PaddleOCR-VL model kwarg as KEY=VALUE. Can be repeated.",
    )
    parser.add_argument("--ocr-command", help="External OCR command for --reader command. Use {input}, {workdir}, {stem}, {output_markdown} placeholders.")
    parser.add_argument("--ocr-output-markdown", help="Markdown path produced by --ocr-command. Default {workdir}/merged_ocr.md")
    parser.add_argument("--custom-reader", help="Custom reader class path for --reader custom, e.g. my_pkg.reader:MyReader")
    parser.add_argument("--custom-option", action="append", default=[], help="Custom reader option as KEY=VALUE. Can be repeated.")
    parser.add_argument("--no-cache", action="store_true", help="Disable OCR/cache reuse")
    parser.add_argument("--include-activity", action="store_true", help="Include Table 2 Activity Metrics")
    parser.add_argument("--no-activity", action="store_true", help="Exclude Table 2 Activity Metrics")
    parser.add_argument("--no-split", action="store_true", help="Disable multi-part metric splitting")
    parser.add_argument("--no-topic-summary", action="store_true", help="Disable Topic Summary extraction")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    cfg = load_config(args.config)

    if args.reader:
        cfg.reader = args.reader
    if args.pipeline_version:
        cfg.paddleocr.pipeline_version = args.pipeline_version
    if args.model_name_or_path:
        cfg.paddleocr.model_name_or_path = args.model_name_or_path
    if args.model_kwarg:
        cfg.paddleocr.extra_kwargs.update(_parse_key_value(args.model_kwarg))
    if args.ocr_command:
        cfg.reader = args.reader or "command"
        cfg.command_reader.command = args.ocr_command
    if args.ocr_output_markdown:
        cfg.command_reader.output_markdown = args.ocr_output_markdown
    if args.custom_reader:
        cfg.reader = args.reader or "custom"
        cfg.custom_reader.class_path = args.custom_reader
    if args.custom_option:
        cfg.custom_reader.options.update(_parse_key_value(args.custom_option))
    if args.no_cache:
        cfg.paddleocr.use_cache = False
        cfg.command_reader.use_cache = False
    if args.include_activity:
        cfg.extraction.include_activity = True
    if args.no_activity:
        cfg.extraction.include_activity = False
    if args.no_split:
        cfg.extraction.split_multi_part_metrics = False
    if args.no_topic_summary:
        cfg.extraction.include_topic_summary = False

    reader = build_reader(cfg.reader, cfg)
    console.print(f"[bold]Reading[/bold] {args.input} with reader=[cyan]{cfg.reader}[/cyan]")
    document = reader.read(args.input, args.workdir)

    pipeline = SasbExtractionPipeline(cfg.extraction)
    records = pipeline.extract(document)

    data = [record.to_dict() for record in records]
    out = write_json(args.output, data, indent=cfg.output.indent, ensure_ascii=cfg.output.ensure_ascii)
    console.print(f"[green]Saved[/green] {len(data)} records -> {out}")


if __name__ == "__main__":
    main()
