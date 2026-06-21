"""
MinerU Docker-backed report content extractor.

All report content extraction is delegated to the MinerU GPU service defined in
this project's docker-compose.yml.  The backend itself only runs the MinerU CLI
as a lightweight HTTP client; VLM inference is executed by the compose service
`mineru-openai-server` on GPU.

Compose wiring:
    backend -> mineru-openai-server:30000

The extractor calls:
    mineru -p <input.pdf> -o <output_dir> -b vlm-http-client -u http://mineru-openai-server:30000

It then reads MinerU-generated Markdown / content_list JSON and converts them
into the existing DocumentContent/TextSegment schema used by the rest of the
backend.  The old PyMuPDF/Tesseract extraction path has been removed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from loguru import logger

from .exceptions import ContentExtractionError
from .models import DocumentContent, ProcessingConfig, TextSegment


class _SimpleHTMLTableParser(HTMLParser):
    """Small dependency-free HTML table parser for MinerU table output.

    MinerU may return tables as Markdown, plain pipe tables or HTML.  The rest
    of the retrieval stack needs stable table/table_row/table_cell chunks, so we
    parse basic <table>/<tr>/<th>/<td> structure here instead of relying only on
    Markdown table syntax.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []
            self._in_cell = True
        elif tag == "br" and self._in_cell and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._in_cell and self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            text = re.sub(r"\s+", " ", unescape("".join(self._cell or []))).strip()
            if self._row is not None:
                self._row.append(text)
            self._cell = None
            self._in_cell = False
        elif tag == "tr":
            if self._row is not None and any(str(c).strip() for c in self._row):
                self.rows.append(self._row)
            self._row = None


class ContentExtractor:
    """Extract report content by invoking MinerU and adapting its output.

    Environment variables:
        MINERU_CLI_BIN: MinerU CLI executable inside the backend container. Default: mineru
        MINERU_BACKEND: MinerU backend. Default: vlm-http-client
        MINERU_SERVER_URL: MinerU OpenAI-compatible VLM service URL. Default: http://mineru-openai-server:30000
        MINERU_OUTPUT_DIR: root directory for MinerU outputs. Default: <pdf_dir>/.mineru_output
        MINERU_METHOD: auto | txt | ocr. Default: auto
        MINERU_FORMULA: true/false. Default: true
        MINERU_TABLE: true/false. Default: true
        MINERU_IMAGE_ANALYSIS: true/false. Default: false
        MINERU_CLEAN_OUTPUT: true/false. Default: false
        MINERU_CLI_TIMEOUT: seconds, 0 means no timeout. Default: 0
        MINERU_EXTRA_ARGS: extra CLI args, e.g. "--lang en"
    """

    def __init__(self, config: ProcessingConfig | None = None):
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ContentExtractor")

    def extract_pdf(self, file_path: str) -> DocumentContent:
        """Run MinerU and return DocumentContent compatible with the old pipeline."""
        source_path = Path(file_path).resolve()
        if not source_path.exists():
            raise ContentExtractionError(f"文件不存在: {source_path}", file_path=str(source_path))

        start = time.time()
        try:
            self.logger.info(f"开始使用 MinerU 提取报告内容: {source_path}")
            run_dir = self._run_mineru(source_path)
            markdown_path = self._find_best_markdown(run_dir, source_path.stem)
            if markdown_path is None:
                raise ContentExtractionError(
                    f"MinerU 已运行但没有生成 Markdown 文件。输出目录: {run_dir}",
                    file_path=str(source_path),
                )

            markdown = markdown_path.read_text(encoding="utf-8", errors="ignore").strip()
            if not markdown:
                raise ContentExtractionError(
                    f"MinerU 生成的 Markdown 为空: {markdown_path}",
                    file_path=str(source_path),
                )

            document_id = self._document_id(source_path)
            content_list_path = self._find_content_list_json(run_dir)
            if content_list_path:
                segments = self._segments_from_content_list(content_list_path, document_id)
            else:
                segments = []

            if not segments:
                segments = self._segments_from_markdown(markdown, document_id)

            if not segments:
                # Keep the downstream embedding pipeline alive, while surfacing a clear warning.
                segments = [
                    TextSegment(
                        segment_id=f"{document_id}_p1_s1",
                        content=markdown,
                        page_number=1,
                        position_y=0.0,
                        position_x=0.0,
                        segment_type="text",
                        structured_data={"source": "mineru_markdown_fallback"},
                    )
                ]

            document = DocumentContent(
                document_id=document_id,
                file_path=str(source_path),
                segments=segments,
                markdown_content=markdown,
                created_at=datetime.now(),
            )

            elapsed = time.time() - start
            self.logger.info(
                f"MinerU 内容提取完成: file={source_path.name}, segments={len(segments)}, elapsed={elapsed:.2f}s, output={run_dir}"
            )
            return document

        except ContentExtractionError:
            raise
        except Exception as exc:
            self.logger.exception(f"MinerU 内容提取失败: {source_path}")
            raise ContentExtractionError(f"MinerU 内容提取失败: {exc}", file_path=str(source_path)) from exc

    def save_markdown(self, document_content: DocumentContent, output_path: str | None = None) -> str:
        """Save the MinerU Markdown produced during extraction."""
        if output_path is None:
            pdf_path = Path(document_content.file_path)
            output_path = str(pdf_path.parent / f"{pdf_path.stem}_extracted.md")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(document_content.markdown_content or "", encoding="utf-8")
        return str(out)

    # ------------------------------------------------------------------
    # MinerU execution
    # ------------------------------------------------------------------

    def _run_mineru(self, source_path: Path) -> Path:
        output_root = self._mineru_output_root(source_path)
        run_dir = output_root / self._safe_name(source_path.stem)
        clean = self._env_bool("MINERU_CLEAN_OUTPUT", False)
        if clean and run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        # Reuse successful prior output unless explicitly cleaned.
        if not clean and self._find_best_markdown(run_dir, source_path.stem) is not None:
            self.logger.info(f"复用已有 MinerU 输出: {run_dir}")
            return run_dir

        # In docker-compose deployment the backend runs the MinerU CLI as an
        # HTTP client.  The actual VLM inference is handled by the GPU-only
        # MinerU service `mineru-openai-server`.
        command = self._build_local_mineru_command(source_path, run_dir)

        log_file = run_dir / "mineru_extract.log"
        self.logger.info(f"运行 MinerU 命令: {' '.join(shlex.quote(x) for x in command)}")
        self.logger.info(f"MinerU 日志: {log_file}")

        timeout = int(os.getenv("MINERU_CLI_TIMEOUT", "0") or "0")
        with log_file.open("w", encoding="utf-8") as lf:
            lf.write("COMMAND: " + " ".join(shlex.quote(x) for x in command) + "\n\n")
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            start = time.time()
            try:
                for line in process.stdout:
                    lf.write(line)
                    lf.flush()
                    lower = line.lower()
                    if any(k in lower for k in ("error", "failed", "traceback", "done", "output", "warning")):
                        self.logger.info(f"[MinerU] {line.rstrip()}")
                    if timeout > 0 and time.time() - start > timeout:
                        process.kill()
                        raise TimeoutError(f"MinerU CLI 超时: {timeout}s")
                return_code = process.wait()
            finally:
                if process.poll() is None:
                    process.kill()

        if return_code != 0:
            tail = self._tail_text(log_file, max_lines=120)
            raise ContentExtractionError(
                f"MinerU CLI 执行失败，exit_code={return_code}。日志: {log_file}\n{tail}",
                file_path=str(source_path),
            )

        return run_dir

    def _build_local_mineru_command(self, source_path: Path, run_dir: Path) -> List[str]:
        mineru_bin = os.getenv("MINERU_CLI_BIN", "mineru").strip() or "mineru"
        if shutil.which(mineru_bin) is None:
            raise ContentExtractionError(
                "未找到 MinerU CLI。请先安装 MinerU 客户端，并确保 backend 镜像通过 requirements.docker.txt 安装了 mineru。",
                file_path=str(source_path),
            )

        command = [
            mineru_bin,
            "-p",
            str(source_path),
            "-o",
            str(run_dir),
        ]
        command.extend(self._mineru_backend_args())
        command.extend(self._mineru_common_args())
        command.extend(self._extra_args())
        return command

    def _mineru_backend_args(self) -> List[str]:
        backend = os.getenv("MINERU_BACKEND", "vlm-http-client").strip() or "vlm-http-client"
        args = ["-b", backend]
        if backend in {"vlm-http-client", "hybrid-http-client"}:
            server_url = os.getenv("MINERU_SERVER_URL", "http://mineru-openai-server:30000").strip()
            if not server_url:
                raise ContentExtractionError("MINERU_SERVER_URL 不能为空。")
            args.extend(["-u", server_url])
        return args

    def _mineru_common_args(self) -> List[str]:
        method = os.getenv("MINERU_METHOD", "auto").strip() or "auto"
        args = [
            "-m",
            method,
            "--formula",
            self._bool_cli("MINERU_FORMULA", True),
            "--table",
            self._bool_cli("MINERU_TABLE", True),
            "--image-analysis",
            self._bool_cli("MINERU_IMAGE_ANALYSIS", False),
        ]
        lang = os.getenv("MINERU_LANG", "").strip()
        if lang:
            args.extend(["-l", lang])
        return args

    def _extra_args(self) -> List[str]:
        raw = os.getenv("MINERU_EXTRA_ARGS", "").strip()
        return shlex.split(raw) if raw else []

    def _mineru_output_root(self, source_path: Path) -> Path:
        env_root = os.getenv("MINERU_OUTPUT_DIR", "").strip()
        root = Path(env_root).expanduser() if env_root else source_path.parent / ".mineru_output"
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()

    # ------------------------------------------------------------------
    # MinerU output discovery
    # ------------------------------------------------------------------

    def _find_best_markdown(self, root: Path, preferred_stem: str) -> Optional[Path]:
        candidates = [p for p in root.rglob("*.md") if p.is_file()]
        if not candidates:
            return None

        stem = self._safe_name(preferred_stem).lower()
        preferred = [p for p in candidates if p.stem.lower() == stem or stem in p.stem.lower()]
        pool = preferred or candidates
        non_empty = [p for p in pool if p.stat().st_size > 0]
        if not non_empty:
            return None
        return max(non_empty, key=lambda p: p.stat().st_size)

    def _find_content_list_json(self, root: Path) -> Optional[Path]:
        patterns = ["*content_list*.json", "*middle*.json", "*.json"]
        candidates: List[Path] = []
        for pattern in patterns:
            candidates.extend(p for p in root.rglob(pattern) if p.is_file())
        # Prefer explicit content_list files; ignore logs/manifests if possible.
        scored: List[Tuple[int, int, Path]] = []
        for p in set(candidates):
            name = p.name.lower()
            if "log" in name or "manifest" in name:
                continue
            score = 0
            if "content_list" in name:
                score += 100
            if "middle" in name:
                score += 20
            scored.append((score, p.stat().st_size, p))
        if not scored:
            return None
        scored.sort(reverse=True)
        return scored[0][2]

    # ------------------------------------------------------------------
    # Segment construction from MinerU output
    # ------------------------------------------------------------------

    def _segments_from_content_list(self, content_list_path: Path, document_id: str) -> List[TextSegment]:
        try:
            data = json.loads(content_list_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception as exc:
            self.logger.warning(f"无法读取 MinerU content_list JSON: {content_list_path}: {exc}")
            return []

        records = self._flatten_json_records(data)
        segments: List[TextSegment] = []
        table_index = 0
        seq = 0

        for item in records:
            if not isinstance(item, dict):
                continue
            page = self._page_number_from_item(item)
            item_type = str(item.get("type") or item.get("category") or item.get("layout_type") or "text").lower()
            content = self._content_from_item(item)
            if not content:
                continue

            if "table" in item_type:
                table_index += 1
                seq += 1
                table_id = f"{document_id}_table_{table_index:04d}"
                table_title = self._table_title_from_item(item)
                table_segment = TextSegment(
                    segment_id=f"{document_id}_p{page}_s{seq}",
                    content=content,
                    page_number=page,
                    position_y=float(item.get("y") or item.get("top") or 0.0),
                    position_x=float(item.get("x") or item.get("left") or 0.0),
                    segment_type="table",
                    source_table_id=table_id,
                    structured_data={
                        "source": "mineru_content_list",
                        "table_id": table_id,
                        "raw_type": item_type,
                        "table_title": table_title,
                    },
                )
                segments.append(table_segment)
                row_cell_segments = self._table_segments_from_markdown(
                    content,
                    document_id,
                    page,
                    table_id,
                    start_seq=seq,
                    table_title=table_title,
                )
                if row_cell_segments:
                    segments.extend(row_cell_segments)
                    seq += len(row_cell_segments)
                continue

            segment_type = "text"
            if "title" in item_type or "heading" in item_type:
                segment_type = "heading"
            elif "image" in item_type or "figure" in item_type:
                segment_type = "image"
            elif "equation" in item_type or "formula" in item_type:
                segment_type = "formula"

            if segment_type == "text" and len(content.strip()) < int(getattr(self.config, "min_text_length", 10) or 10):
                continue

            seq += 1
            segments.append(
                TextSegment(
                    segment_id=f"{document_id}_p{page}_s{seq}",
                    content=content,
                    page_number=page,
                    position_y=float(item.get("y") or item.get("top") or 0.0),
                    position_x=float(item.get("x") or item.get("left") or 0.0),
                    segment_type=segment_type,
                    structured_data={"source": "mineru_content_list", "raw_type": item_type},
                )
            )

        return segments

    def _flatten_json_records(self, data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            out: List[Dict[str, Any]] = []
            for x in data:
                if isinstance(x, dict):
                    out.append(x)
                elif isinstance(x, list):
                    out.extend(self._flatten_json_records(x))
            return out
        if isinstance(data, dict):
            for key in ("content_list", "pdf_info", "pages", "blocks", "elements"):
                val = data.get(key)
                if isinstance(val, list):
                    return self._flatten_json_records(val)
            # MinerU middle JSON sometimes stores pages with blocks nested.
            out: List[Dict[str, Any]] = []
            for value in data.values():
                if isinstance(value, list):
                    out.extend(self._flatten_json_records(value))
                elif isinstance(value, dict):
                    out.extend(self._flatten_json_records(value))
            return out
        return []

    def _content_from_item(self, item: Dict[str, Any]) -> str:
        keys = [
            "text",
            "content",
            "md",
            "markdown",
            "table_body",
            "table_html",
            "html",
            "latex",
            "caption",
            "img_caption",
        ]
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                value = "\n".join(str(x) for x in value if str(x).strip())
            text = str(value).strip()
            if text:
                return text
        return ""

    def _table_title_from_item(self, item: Dict[str, Any]) -> str:
        """Extract a concise table title/caption without changing table content."""
        for key in ("table_title", "title", "caption", "table_caption", "img_caption"):
            value = item.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                value = " ".join(str(x) for x in value if str(x).strip())
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if text:
                return text[:240]
        return ""

    def _page_number_from_item(self, item: Dict[str, Any]) -> int:
        for key in ("page_number", "page_no", "page", "page_id", "page_idx"):
            value = item.get(key)
            if value is None:
                continue
            try:
                n = int(value)
                if key == "page_idx":
                    n += 1
                return max(1, n)
            except Exception:
                continue
        return 1

    def _segments_from_markdown(self, markdown: str, document_id: str) -> List[TextSegment]:
        blocks = self._split_markdown_blocks(markdown)
        segments: List[TextSegment] = []
        seq = 0
        table_index = 0
        current_page = 1
        current_heading = ""

        for block in blocks:
            marker = self._page_marker(block)
            if marker is not None:
                current_page = marker
                continue

            block = block.strip()
            if not block:
                continue

            if self._looks_like_markdown_table(block):
                table_index += 1
                table_id = f"{document_id}_table_{table_index:04d}"
                seq += 1
                segments.append(
                    TextSegment(
                        segment_id=f"{document_id}_p{current_page}_s{seq}",
                        content=block,
                        page_number=current_page,
                        position_y=float(seq),
                        position_x=0.0,
                        segment_type="table",
                        source_table_id=table_id,
                        structured_data={"source": "mineru_markdown", "table_id": table_id, "table_title": current_heading},
                    )
                )
                table_segments = self._table_segments_from_markdown(
                    block,
                    document_id,
                    current_page,
                    table_id,
                    start_seq=seq,
                    table_title=current_heading,
                )
                if table_segments:
                    segments.extend(table_segments)
                    seq += len(table_segments)
                continue

            segment_type = "heading" if re.match(r"^#{1,6}\s+", block) else "text"
            content = re.sub(r"^#{1,6}\s+", "", block).strip() if segment_type == "heading" else block
            if segment_type == "heading":
                current_heading = content
            if segment_type == "text" and len(content.strip()) < int(getattr(self.config, "min_text_length", 10) or 10):
                continue
            seq += 1
            segments.append(
                TextSegment(
                    segment_id=f"{document_id}_p{current_page}_s{seq}",
                    content=content,
                    page_number=current_page,
                    position_y=float(seq),
                    position_x=0.0,
                    segment_type=segment_type,
                    structured_data={"source": "mineru_markdown"},
                )
            )

        return segments

    def _split_markdown_blocks(self, markdown: str) -> List[str]:
        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        blocks: List[str] = []
        current: List[str] = []
        in_table = False
        in_html_table = False

        def flush() -> None:
            nonlocal current, in_table, in_html_table
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            in_table = False
            in_html_table = False

        for line in lines:
            stripped = line.strip()
            if self._page_marker(stripped) is not None:
                flush()
                blocks.append(stripped)
                continue

            lower = stripped.lower()
            if "<table" in lower:
                if current and not in_html_table:
                    flush()
                current.append(line)
                in_html_table = True
                if "</table" in lower:
                    flush()
                continue

            if in_html_table:
                current.append(line)
                if "</table" in lower:
                    flush()
                continue

            table_line = self._looks_like_table_line(stripped)
            if table_line:
                if current and not in_table:
                    flush()
                current.append(line)
                in_table = True
                continue

            if in_table:
                flush()

            if not stripped:
                flush()
                continue

            if re.match(r"^#{1,6}\s+", stripped):
                flush()
                current.append(line)
                flush()
                continue

            current.append(line)

        flush()
        return [b for b in blocks if b]

    def _looks_like_table_line(self, stripped: str) -> bool:
        """Stricter table-line detector than a raw pipe check."""
        if not stripped:
            return False
        if "<tr" in stripped.lower() or "<td" in stripped.lower() or "<th" in stripped.lower():
            return True
        if "|" not in stripped:
            return False
        # Standard Markdown / pipe tables should have at least two separators
        # or start/end pipes.  Avoid treating normal prose containing one pipe
        # character as a table block.
        return stripped.count("|") >= 2 or (stripped.startswith("|") and "|" in stripped[1:])

    def _table_segments_from_markdown(
        self,
        table_md: str,
        document_id: str,
        page: int,
        table_id: str,
        *,
        start_seq: int = 0,
        table_title: str = "",
    ) -> List[TextSegment]:
        rows = self._parse_table_rows(table_md)
        if not rows:
            return []

        headers = self._normalise_table_row(rows[0])
        data_rows = rows[1:] if len(rows) > 1 else []
        if not any(headers):
            max_cols = max((len(r) for r in data_rows), default=0)
            headers = [f"Column {i + 1}" for i in range(max_cols)]
        segments: List[TextSegment] = []
        seq = start_seq

        for r_idx, row in enumerate(data_rows, start=1):
            row = self._normalise_table_row(row, width=max(len(headers), len(row)))
            seq += 1
            row_header = self._infer_row_header(headers, row)
            row_text = self._format_table_row_context(headers, row, table_title=table_title, page=page)
            row_segment_id = f"{document_id}_p{page}_s{seq}"
            segments.append(
                TextSegment(
                    segment_id=row_segment_id,
                    content=row_text,
                    page_number=page,
                    position_y=float(seq),
                    position_x=0.0,
                    segment_type="table_row",
                    source_table_id=table_id,
                    row_header=row_header,
                    structured_data={
                        "source": "mineru_table_parser",
                        "table_id": table_id,
                        "table_title": table_title,
                        "row_index": r_idx,
                        "row_header": row_header,
                        "row_text": row_text,
                        "column_headers": headers,
                    },
                )
            )
            for c_idx, value in enumerate(row):
                if not str(value).strip():
                    continue
                seq += 1
                col_header = headers[c_idx] if c_idx < len(headers) else f"col_{c_idx + 1}"
                cell_content_parts = []
                if table_title:
                    cell_content_parts.append(f"[Table Title] {table_title}")
                if headers:
                    cell_content_parts.append(f"[Column Headers] {' | '.join(headers)}")
                cell_content_parts.append(f"[Row Context] {row_text}")
                cell_content_parts.append(f"{col_header}: {value}")
                segments.append(
                    TextSegment(
                        segment_id=f"{document_id}_p{page}_s{seq}",
                        content="\n".join(cell_content_parts),
                        page_number=page,
                        position_y=float(seq),
                        position_x=float(c_idx),
                        segment_type="table_cell",
                        source_table_id=table_id,
                        row_header=row_header,
                        col_header=col_header,
                        value_text=value,
                        structured_data={
                            "source": "mineru_table_parser",
                            "table_id": table_id,
                            "table_title": table_title,
                            "row_index": r_idx,
                            "col_index": c_idx,
                            "row_header": row_header,
                            "col_header": col_header,
                            "value_text": value,
                            "column_headers": headers,
                            "row_text": row_text,
                            "row_segment_id": row_segment_id,
                        },
                    )
                )
        return segments

    def _parse_table_rows(self, table_text: str) -> List[List[str]]:
        html_rows = self._parse_html_table_rows(table_text)
        if html_rows:
            return html_rows
        return self._parse_markdown_table_rows(table_text)

    def _parse_html_table_rows(self, table_html: str) -> List[List[str]]:
        if not re.search(r"<\s*(table|tr|td|th)\b", table_html or "", flags=re.IGNORECASE):
            return []
        parser = _SimpleHTMLTableParser()
        try:
            parser.feed(table_html)
            parser.close()
        except Exception:
            return []
        rows = [self._normalise_table_row(row) for row in parser.rows if any(str(c).strip() for c in row)]
        return rows

    def _normalise_table_row(self, row: Sequence[Any], width: Optional[int] = None) -> List[str]:
        cells = [re.sub(r"\s+", " ", unescape(str(cell or ""))).strip() for cell in row]
        if width is not None and len(cells) < width:
            cells.extend([""] * (width - len(cells)))
        return cells

    def _infer_row_header(self, headers: Sequence[str], row: Sequence[str]) -> str:
        header_names = [str(h or "").strip().lower() for h in headers]
        preferred_names = ("metric", "indicator", "disclosure", "topic", "code", "sasb code")
        for preferred in preferred_names:
            for idx, header in enumerate(header_names):
                if preferred in header and idx < len(row) and str(row[idx]).strip():
                    return str(row[idx]).strip()
        for value in row:
            if str(value).strip():
                return str(value).strip()
        return ""

    def _format_table_row_context(self, headers: Sequence[str], row: Sequence[str], *, table_title: str = "", page: int = 1) -> str:
        parts: List[str] = []
        if table_title:
            parts.append(f"[Table Title] {table_title}")
        if headers:
            parts.append(f"[Column Headers] {' | '.join(str(h or '').strip() for h in headers)}")
        pairs: List[str] = []
        for idx, value in enumerate(row):
            value_text = str(value or "").strip()
            if not value_text:
                continue
            header = str(headers[idx]).strip() if idx < len(headers) and str(headers[idx]).strip() else f"Column {idx + 1}"
            pairs.append(f"{header}: {value_text}")
        if pairs:
            parts.append(" | ".join(pairs))
        else:
            parts.append(" | ".join(str(x or "").strip() for x in row if str(x or "").strip()))
        parts.append(f"Page: {page}")
        return "\n".join(p for p in parts if p)

    def _parse_markdown_table_rows(self, table_md: str) -> List[List[str]]:
        rows: List[List[str]] = []
        for line in table_md.splitlines():
            stripped = line.strip()
            if not stripped or "|" not in stripped:
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not cells:
                continue
            # Markdown separator row: |---|:---:|
            if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c):
                continue
            rows.append(cells)
        return rows

    def _looks_like_markdown_table(self, block: str) -> bool:
        if re.search(r"<\s*(table|tr|td|th)\b", block or "", flags=re.IGNORECASE):
            return bool(self._parse_html_table_rows(block))
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            return False
        pipe_lines = [ln for ln in lines if self._looks_like_table_line(ln.strip())]
        if len(pipe_lines) < 2:
            return False
        parsed = self._parse_markdown_table_rows("\n".join(pipe_lines))
        if len(parsed) < 2:
            return False
        widths = [len(row) for row in parsed if row]
        return bool(widths and max(widths) >= 2 and widths.count(widths[0]) >= 2)

    def _page_marker(self, block: str) -> Optional[int]:
        patterns = [
            r"<!--\s*page\s*:?\s*(\d+)\s*-->",
            r"^\s*page\s*[:#-]?\s*(\d+)\s*$",
            r"^\s*第\s*(\d+)\s*页\s*$",
        ]
        for pattern in patterns:
            m = re.search(pattern, block, flags=re.IGNORECASE)
            if m:
                try:
                    return max(1, int(m.group(1)))
                except Exception:
                    return None
        return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _document_id(self, path: Path) -> str:
        digest = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:8]
        return f"doc_{self._safe_name(path.stem)}_{digest}"

    def _safe_name(self, text: str) -> str:
        value = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(text or "")).strip("_")
        return value or "document"

    def _env_bool(self, name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _bool_cli(self, name: str, default: bool) -> str:
        return "true" if self._env_bool(name, default) else "false"

    def _tail_text(self, path: Path, max_lines: int = 80) -> str:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(lines[-max_lines:])
        except Exception:
            return ""
