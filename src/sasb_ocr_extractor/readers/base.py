from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from sasb_ocr_extractor.models import OcrDocument


class BaseReader(ABC):
    @abstractmethod
    def read(self, input_path: str | Path, workdir: str | Path) -> OcrDocument:
        raise NotImplementedError
