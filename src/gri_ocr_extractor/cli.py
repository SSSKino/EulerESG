from __future__ import annotations

import argparse
import json
from pathlib import Path

from gri_ocr_extractor.config import ExtractionConfig
from gri_ocr_extractor.extractors.gri_sector_parser import GriSectorParser
from gri_ocr_extractor.readers.pdf_text_reader import PdfTextReader


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract GRI sector disclosures from one official PDF.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-context", action="store_true")
    parser.add_argument("--include-mine-site", action="store_true")
    args = parser.parse_args()

    reader = PdfTextReader()
    doc = reader.read(Path(args.input), "work")
    cfg = ExtractionConfig(include_context=args.include_context, include_mine_site=args.include_mine_site)
    records = GriSectorParser(cfg).parse(doc.text, doc.source_path)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK {len(records)} records -> {out}")


if __name__ == "__main__":
    main()
