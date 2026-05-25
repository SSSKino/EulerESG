from __future__ import annotations

from pathlib import Path

from gri_ocr_extractor.config import PdfTextReaderConfig
from gri_ocr_extractor.models import OcrDocument, OcrPage
from gri_ocr_extractor.readers.base import BaseReader


class PdfTextReader(BaseReader):
    """Read embedded text from digitally-readable GRI PDF files.

    The official GRI sector PDFs are text-readable. PyMuPDF is preferred because it
    preserves the table reading order better than generic PDF text extraction.
    If a scanned PDF is used, switch to --reader paddleocr-vl or --reader command.
    """

    def __init__(self, config: PdfTextReaderConfig | None = None):
        self.config = config or PdfTextReaderConfig()

    def read(self, input_path: str | Path, workdir: str | Path) -> OcrDocument:
        input_path = Path(input_path)
        if input_path.suffix.lower() != ".pdf":
            raise ValueError("pdf-text reader only supports .pdf input. Use command/custom reader for other inputs.")
        text, pages = self._read_with_pymupdf(input_path)
        if not text:
            text, pages = self._read_with_pypdf(input_path)
        if not text:
            raise RuntimeError("No embedded text extracted from PDF. Use --reader paddleocr-vl or --reader command for OCR.")
        return OcrDocument(source_path=input_path, text=text, markdown=text, pages=pages, metadata={"reader": "pdf-text"})

    def _read_with_pymupdf(self, input_path: Path) -> tuple[str, list[OcrPage]]:
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return "", []
        pages: list[OcrPage] = []
        texts: list[str] = []
        with fitz.open(str(input_path)) as doc:
            for idx, page in enumerate(doc, start=1):
                try:
                    text = page.get_text("text", sort=True) or ""
                except Exception:
                    text = ""
                pages.append(OcrPage(page_number=idx, text=text, markdown=text))
                texts.append(text)
        return "\n\n".join(texts).strip(), pages

    def _read_with_pypdf(self, input_path: Path) -> tuple[str, list[OcrPage]]:
        try:
            from pypdf import PdfReader
        except ImportError:
            return "", []
        reader = PdfReader(str(input_path))
        pages: list[OcrPage] = []
        texts: list[str] = []
        for idx, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages.append(OcrPage(page_number=idx, text=text, markdown=text))
            texts.append(text)
        return "\n\n".join(texts).strip(), pages
