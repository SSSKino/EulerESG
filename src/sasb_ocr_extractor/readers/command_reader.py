from __future__ import annotations

import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sasb_ocr_extractor.config import CommandReaderConfig
from sasb_ocr_extractor.models import OcrDocument, OcrPage
from sasb_ocr_extractor.readers.base import BaseReader
from sasb_ocr_extractor.utils.io import ensure_dir, write_json
from sasb_ocr_extractor.utils.text import clean_text


class CommandReader(BaseReader):
    """Strict adapter for an external OCR/VLM command.

    The external command must write the configured Markdown or Text file exactly.
    No fallback file-name scanning and no JSON text scraping are performed.
    """

    def __init__(self, config: Optional[CommandReaderConfig] = None):
        self.config = config or CommandReaderConfig()

    def read(self, input_path: str | Path, workdir: str | Path) -> OcrDocument:
        if not self.config.command:
            raise ValueError(
                "command_reader.command is empty. Provide a command that writes the configured OCR Markdown/Text output."
            )

        source = Path(input_path).resolve()
        root = ensure_dir(Path(workdir) / source.stem).resolve()
        output_markdown = self._resolve_output_path(self.config.output_markdown, source, root)
        output_text = (
            self._resolve_output_path(self.config.output_text, source, root)
            if self.config.output_text
            else None
        )
        metadata_path = root / "command_reader_metadata.json"

        if self.config.use_cache:
            cached_text = self._read_configured_output(output_markdown, output_text)
            if cached_text:
                return OcrDocument(
                    source_path=source,
                    text=cached_text,
                    markdown=cached_text,
                    pages=[OcrPage(page_number=None, text=cached_text, markdown=cached_text)],
                    metadata={
                        "reader": "command",
                        "strict_no_fallback": True,
                        "cache_used": True,
                        "workdir": str(root),
                        "output_markdown": str(output_markdown),
                        "output_text": str(output_text) if output_text else None,
                    },
                )

        output_markdown.parent.mkdir(parents=True, exist_ok=True)
        if output_text is not None:
            output_text.parent.mkdir(parents=True, exist_ok=True)

        command = self._format_command(source, root, output_markdown)
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (self.config.env or {}).items()})

        result = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.config.timeout_seconds,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "External OCR command failed with exit code "
                f"{result.returncode}.\nCommand: {command}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )

        text = self._read_configured_output(output_markdown, output_text)
        if not text:
            expected = str(output_text or output_markdown)
            raise RuntimeError(
                "External OCR command completed, but the configured output was missing or empty. "
                f"Strict mode disables fallback scanning. Expected output: {expected}"
            )

        metadata = {
            "reader": "command",
            "strict_no_fallback": True,
            "cache_used": False,
            "source": str(source),
            "workdir": str(root),
            "created_at": datetime.utcnow().isoformat() + "Z",
            "command": command,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
            "output_markdown": str(output_markdown),
            "output_text": str(output_text) if output_text else None,
        }
        write_json(metadata_path, metadata)

        return OcrDocument(
            source_path=source,
            text=text,
            markdown=text,
            pages=[OcrPage(page_number=None, text=text, markdown=text)],
            metadata=metadata,
        )

    def _resolve_output_path(self, pattern: str | None, source: Path, root: Path) -> Path:
        if not pattern:
            return root / "merged_ocr.md"
        placeholders = {
            "input": str(source),
            "workdir": str(root),
            "stem": source.stem,
        }
        rendered = str(pattern).format(**placeholders)
        path = Path(rendered)
        if path.is_absolute():
            return path
        if "{workdir}" in str(pattern):
            return path
        return root / path

    def _format_command(self, source: Path, root: Path, output_markdown: Path) -> str:
        placeholders = {
            "input": shlex.quote(str(source)),
            "workdir": shlex.quote(str(root)),
            "stem": shlex.quote(source.stem),
            "output_markdown": shlex.quote(str(output_markdown)),
        }
        return self.config.command.format(**placeholders)

    def _read_configured_output(self, output_markdown: Path, output_text: Optional[Path]) -> str:
        candidates: List[Path] = []
        if output_markdown.exists():
            candidates.append(output_markdown)
        if output_text is not None and output_text.exists():
            candidates.append(output_text)

        chunks = []
        for path in candidates:
            content = path.read_text(encoding="utf-8", errors="ignore")
            if content.strip():
                chunks.append(f"\n\n<!-- source:{path.name} -->\n{content}")
        return clean_text("\n".join(chunks))
