from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Optional

from sasb_ocr_extractor.config import CustomReaderConfig
from sasb_ocr_extractor.models import OcrDocument
from sasb_ocr_extractor.readers.base import BaseReader


class CustomReader(BaseReader):
    """Load a user-provided reader without editing this project.

    The configured target can be a class with a read(input_path, workdir) method,
    a callable returning OcrDocument, or a class accepting `options` as its first
    constructor argument.
    """

    def __init__(self, config: Optional[CustomReaderConfig] = None):
        self.config = config or CustomReaderConfig()
        self._delegate: Any = None

    def read(self, input_path: str | Path, workdir: str | Path) -> OcrDocument:
        delegate = self._get_delegate()
        if hasattr(delegate, "read"):
            document = delegate.read(input_path, workdir)
        elif callable(delegate):
            document = delegate(input_path, workdir)
        else:
            raise TypeError("custom_reader target must be callable or implement read().")

        if not isinstance(document, OcrDocument):
            raise TypeError(
                "custom_reader must return sasb_ocr_extractor.models.OcrDocument. "
                f"Got: {type(document)!r}"
            )
        document.metadata.setdefault("reader", "custom")
        document.metadata.setdefault("custom_reader", self.config.class_path)
        return document

    def _get_delegate(self) -> Any:
        if self._delegate is not None:
            return self._delegate
        if not self.config.class_path:
            raise ValueError("custom_reader.class_path is empty.")

        target = self.config.class_path.strip()
        if ":" in target:
            module_name, attr = target.split(":", 1)
        else:
            module_name, attr = target.rsplit(".", 1)
        module = importlib.import_module(module_name)
        obj = getattr(module, attr)

        options = self.config.options or {}
        try:
            self._delegate = obj(options)
        except TypeError:
            try:
                self._delegate = obj(**options)
            except TypeError:
                self._delegate = obj()
        return self._delegate
