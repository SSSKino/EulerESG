from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from gri_ocr_extractor.config import ExtractionConfig
from gri_ocr_extractor.extractors.definition_bank import (
    DefinitionBank,
    enrich_records_with_definitions,
)
from gri_ocr_extractor.extractors.gri_definition_parser import GriDefinitionParser
from gri_ocr_extractor.extractors.gri_sector_parser import GriSectorParser
from gri_ocr_extractor.readers.pdf_text_reader import PdfTextReader


def _safe_stem(path: Path) -> str:
    value = path.stem.replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9_.\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "output"


def _iter_pdfs(directory: Path, pattern: str) -> list[Path]:
    return sorted(p for p in directory.glob(pattern) if p.is_file() and p.suffix.lower() == ".pdf")


def _positive_or_zero(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected integer, got: {value}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"Expected integer >= 0, got: {value}")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build definitions from input/topic GRI PDFs, then extract input/sector "
            "GRI 11/12/13/14 rows and write a direct Definition field."
        )
    )
    parser.add_argument("--topic-dir", default="data/input/topic", help="Directory containing GRI topic/universal/glossary PDFs")
    parser.add_argument("--sector-dir", default="data/input/sector", help="Directory containing GRI sector PDFs")
    parser.add_argument("--output-dir", default="data/output", help="Directory for enriched sector JSON outputs")
    parser.add_argument("--topic-pattern", default="*.pdf")
    parser.add_argument("--sector-pattern", default="*.pdf")
    parser.add_argument("--include-context", action="store_true")
    parser.add_argument("--include-mine-site", action="store_true")
    parser.add_argument(
        "--definition-selection",
        choices=["all-by-code", "term-in-record"],
        default="all-by-code",
        help="all-by-code: attach all definitions from matching GRI code; term-in-record: attach only terms appearing in the sector row",
    )
    parser.add_argument("--definition-max-chars", type=_positive_or_zero, default=0, help="0 means no truncation")
    parser.add_argument("--write-definition-bank", help="Optional debug output path for extracted topic definitions")
    args = parser.parse_args()

    topic_dir = Path(args.topic_dir)
    sector_dir = Path(args.sector_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfTextReader()
    definition_parser = GriDefinitionParser()
    bank = DefinitionBank()

    topic_files = _iter_pdfs(topic_dir, args.topic_pattern)
    print(f"[1/2] Extracting definitions from {len(topic_files)} topic files: {topic_dir}")
    if not topic_files:
        print("  WARNING: no topic PDFs found. Put GRI Topic/Universal/Glossary PDFs under data/input/topic.")
    for path in topic_files:
        try:
            doc = reader.read(path, "work")
            defs = definition_parser.parse_document(doc)
            bank.add_many(defs)
            code = defs[0].standard_code if defs else ""
            print(f"  OK {path.name}: {len(defs)} definitions" + (f" [GRI {code}]" if code else ""))
        except Exception as exc:
            print(f"  FAILED {path.name}: {exc}")

    if args.write_definition_bank:
        bank_path = Path(args.write_definition_bank)
        bank_path.parent.mkdir(parents=True, exist_ok=True)
        bank_path.write_text(json.dumps(bank.to_simple_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  definition bank -> {bank_path}")

    sector_files = _iter_pdfs(sector_dir, args.sector_pattern)
    print(f"[2/2] Extracting sector rows from {len(sector_files)} sector files: {sector_dir}")
    if not sector_files:
        print("  WARNING: no sector PDFs found. Put GRI 11/12/13/14 Sector PDFs under data/input/sector.")
    cfg = ExtractionConfig(include_context=args.include_context, include_mine_site=args.include_mine_site)
    sector_parser = GriSectorParser(cfg)
    for path in sector_files:
        try:
            doc = reader.read(path, "work")
            records = sector_parser.parse(doc.text, doc.source_path)
            enrich_records_with_definitions(
                records,
                bank,
                selection=args.definition_selection,
                max_chars=args.definition_max_chars,
            )
            out_path = output_dir / f"{_safe_stem(path)}.json"
            out_path.write_text(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
            n_def = sum(1 for r in records if r.Definition)
            print(f"  OK {path.name}: {len(records)} records, {n_def} with Definition -> {out_path}")
        except Exception as exc:
            print(f"  FAILED {path.name}: {exc}")


if __name__ == "__main__":
    main()
