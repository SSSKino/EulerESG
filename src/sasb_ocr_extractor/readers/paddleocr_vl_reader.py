from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from sasb_ocr_extractor.config import PaddleOcrConfig
from sasb_ocr_extractor.models import OcrDocument, OcrPage
from sasb_ocr_extractor.readers.base import BaseReader
from sasb_ocr_extractor.utils.io import ensure_dir, write_json
from sasb_ocr_extractor.utils.text import clean_text


class PaddleOCRVLReader(BaseReader):
    """Strict PaddleOCR-VL front-end reader.

    No fallback OCR/text extraction is used here:
      1. build PaddleOCRVL(pipeline_version="v1.5")
      2. call predict(input)
      3. save only normalized Markdown cache and compact metadata in workdir
      4. require Markdown output from PaddleOCR-VL

    If PaddleOCR-VL fails or does not generate Markdown text, the run fails fast.

    The workdir intentionally keeps only the normalized Markdown cache and a
    compact metadata file. Raw PaddleOCR artifacts are written to a temporary
    directory and are not retained in the project work folder.
    """

    def __init__(self, config: Optional[PaddleOcrConfig] = None):
        self.config = config or PaddleOcrConfig()
        self._pipeline = None

    def read(self, input_path: str | Path, workdir: str | Path) -> OcrDocument:
        source = Path(input_path)
        root = ensure_dir(Path(workdir) / source.stem)
        md_cache = root / "merged_ocr.md"
        meta_cache = root / "run_metadata.json"
        self._cleanup_legacy_raw_artifacts(root)

        if self.config.use_cache and md_cache.exists():
            text = clean_text(md_cache.read_text(encoding="utf-8", errors="ignore"))
            if not text:
                raise RuntimeError(f"OCR cache exists but is empty: {md_cache}")
            return OcrDocument(
                source_path=source,
                text=text,
                markdown=text,
                pages=self._build_pages_from_markdown(text),
                metadata={
                    "reader": "paddleocr-vl",
                    "pipeline_version": self.config.pipeline_version,
                    "model_name_or_path": self.config.model_name_or_path,
                    "strict_no_fallback": True,
                    "cache_used": True,
                    "workdir": str(root),
                },
            )

        pipeline = self._get_pipeline()
        output = pipeline.predict(str(source))

        with tempfile.TemporaryDirectory(prefix=f"{source.stem}_paddleocr_") as tmpdir:
            tmp_root = Path(tmpdir)
            tmp_md_dir = ensure_dir(tmp_root / "markdown")

            for res in output:
                res.save_to_markdown(save_path=str(tmp_md_dir))

            markdown = clean_text(self._merge_markdown_files(tmp_md_dir))

        if not markdown:
            raise RuntimeError(
                "PaddleOCR-VL completed but did not produce non-empty Markdown. "
                "Strict mode disables JSON/text fallback; no raw PaddleOCR artifacts are retained in the workdir."
            )

        md_cache.write_text(markdown, encoding="utf-8")

        metadata = {
            "reader": "paddleocr-vl",
            "pipeline_version": self.config.pipeline_version,
            "model_name_or_path": self.config.model_name_or_path,
            "extra_kwargs": self.config.extra_kwargs,
            "strict_no_fallback": True,
            "cache_used": False,
            "source": str(source),
            "workdir": str(root),
            "created_at": datetime.utcnow().isoformat() + "Z",
            "markdown_file": str(md_cache),
            "retained_work_files": [str(md_cache), str(meta_cache)],
            "vl_rec_backend": self.config.vl_rec_backend,
            "vl_rec_server_url": self.config.vl_rec_server_url,
        }
        write_json(meta_cache, metadata)

        return OcrDocument(
            source_path=source,
            text=markdown,
            markdown=markdown,
            pages=self._build_pages_from_markdown(markdown),
            metadata=metadata,
        )


    def _get_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline

        try:
            from paddleocr import PaddleOCRVL
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "PaddleOCR-VL is not installed. Install paddlepaddle-gpu for your CUDA version, then install paddleocr[doc-parser]."
            ) from exc

        kwargs: Dict[str, Any] = {"pipeline_version": self.config.pipeline_version}
        if self.config.model_name_or_path:
            kwargs["model_name_or_path"] = self.config.model_name_or_path
        kwargs.update(self.config.extra_kwargs or {})
        if self.config.vl_rec_backend:
            kwargs["vl_rec_backend"] = self.config.vl_rec_backend
        if self.config.vl_rec_server_url:
            kwargs["vl_rec_server_url"] = self.config.vl_rec_server_url

        self._pipeline = PaddleOCRVL(**kwargs)
        return self._pipeline

    def _cleanup_legacy_raw_artifacts(self, root: Path) -> None:
        """Remove previously retained raw PaddleOCR artifact folders from this reader.

        Only known folders created by earlier versions are removed, and only inside
        the current document work directory. The normalized cache files are kept.
        """
        for name in ("paddleocr_raw_json", "paddleocr_raw_md"):
            path = root / name
            if path.exists() and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    def _merge_markdown_files(self, raw_md_dir: Path) -> str:
        chunks = []
        for path in sorted(raw_md_dir.rglob("*.md")):
            content = path.read_text(encoding="utf-8", errors="ignore")
            if content.strip():
                chunks.append(content)
        return clean_text("\n\n".join(chunks))

    def _build_pages_from_markdown(self, markdown: str) -> List[OcrPage]:
        return [OcrPage(page_number=None, text=markdown, markdown=markdown)] if markdown else []
