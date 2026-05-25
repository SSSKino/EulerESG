"""One-command runner for the minimal GRI extractor.

Default behavior:
  1. Build definitions from data/input/topic/*.pdf
  2. Extract sector rows from data/input/sector/*.pdf
  3. Fill each sector row's Definition field
  4. Write JSON files to data/output

Windows example:
  & E:/anaconda3/envs/PYG/python.exe run.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gri_ocr_extractor.batch_cli import main


if __name__ == "__main__":
    main()
