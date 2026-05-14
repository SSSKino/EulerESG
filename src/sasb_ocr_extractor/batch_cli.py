from __future__ import annotations

import argparse
import concurrent.futures
import os
import threading
from typing import Any
from pathlib import Path

from rich.console import Console

from sasb_ocr_extractor.config import load_config
from sasb_ocr_extractor.extractors.pipeline import SasbExtractionPipeline
from sasb_ocr_extractor.readers import build_reader
from sasb_ocr_extractor.utils.io import iter_files, write_json

console = Console()


def _parse_key_value(values: list[str] | None) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"Expected KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.lower() in {"true", "false"}:
            parsed[key] = value.lower() == "true"
        else:
            parsed[key] = value
    return parsed



def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected a positive integer, got: {value}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"Expected a positive integer, got: {value}")
    return parsed

def _output_json_path(input_path: Path, output_dir: Path) -> Path:
    stem = input_path.stem
    for suffix in ("-standard_en-gb", "_standard_en_gb"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return output_dir / f"{stem}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch extract SASB metric JSON files.")
    parser.add_argument("--input-dir", required=True, help="Directory containing PDFs/images")
    parser.add_argument("--output-dir", required=True, help="Directory for output JSON files")
    parser.add_argument("--workdir", default=os.getenv("SASB_WORKDIR", "work"), help="Intermediate OCR/cache directory")
    parser.add_argument("--pattern", default="*.pdf", help="Glob pattern, default *.pdf")
    parser.add_argument("--workers", type=_positive_int, default=_positive_int(os.getenv("SASB_WORKERS", "1")), help="Parallel worker count, default 1")
    parser.add_argument("--config", help="Optional YAML config")
    parser.add_argument("--reader", choices=["paddleocr-vl", "command", "custom"])
    parser.add_argument("--model-name-or-path")
    parser.add_argument("--ocr-command", help="External OCR command for --reader command")
    parser.add_argument("--ocr-output-markdown")
    parser.add_argument("--custom-reader", help="Custom reader class path for --reader custom")
    parser.add_argument("--custom-option", action="append", default=[])
    parser.add_argument("--include-activity", action="store_true")
    parser.add_argument("--no-activity", action="store_true")
    parser.add_argument("--no-topic-summary", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.reader:
        cfg.reader = args.reader
    if args.model_name_or_path:
        cfg.paddleocr.model_name_or_path = args.model_name_or_path
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
    if args.include_activity:
        cfg.extraction.include_activity = True
    if args.no_activity:
        cfg.extraction.include_activity = False
    if args.no_topic_summary:
        cfg.extraction.include_topic_summary = False

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = list(iter_files(args.input_dir, args.pattern))
    console.print(f"Found {len(files)} input files")
    console.print(f"Using {args.workers} worker(s)")

    worker_state = threading.local()

    def get_worker_objects():
        reader = getattr(worker_state, "reader", None)
        pipeline = getattr(worker_state, "pipeline", None)
        if reader is None:
            reader = build_reader(cfg.reader, cfg)
            worker_state.reader = reader
        if pipeline is None:
            pipeline = SasbExtractionPipeline(cfg.extraction)
            worker_state.pipeline = pipeline
        return reader, pipeline

    def process_file(path: Path):
        try:
            reader, pipeline = get_worker_objects()
            document = reader.read(path, args.workdir)
            records = pipeline.extract(document)
            out_path = _output_json_path(path, output_dir)
            write_json(out_path, [r.to_dict() for r in records], cfg.output.indent, cfg.output.ensure_ascii)
            return path, True, len(records), out_path, None
        except Exception as exc:
            return path, False, 0, None, exc

    if args.workers == 1:
        for path in files:
            console.print(f"[bold]Processing[/bold] {path.name}")
            path, ok, count, out_path, exc = process_file(path)
            if ok:
                console.print(f"  [green]OK[/green] {count} records -> {out_path}")
            else:
                console.print(f"  [red]FAILED[/red] {path}: {exc}")
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_path = {}
        for path in files:
            console.print(f"[bold]Processing[/bold] {path.name}")
            future_to_path[executor.submit(process_file, path)] = path

        for future in concurrent.futures.as_completed(future_to_path):
            path, ok, count, out_path, exc = future.result()
            if ok:
                console.print(f"  [green]OK[/green] {count} records -> {out_path}")
            else:
                console.print(f"  [red]FAILED[/red] {path}: {exc}")


if __name__ == "__main__":
    main()
