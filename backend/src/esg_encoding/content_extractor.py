"""PaddleOCR-VL v1.6 Redis 页批次报告内容提取器。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from loguru import logger

from .exceptions import ContentExtractionError
from .models import DocumentContent, ProcessingConfig, TextSegment


def _shared_dir_mode() -> int:
    """返回 PaddleOCR 跨容器共享目录权限，默认 0777。"""
    raw = os.getenv("PADDLEOCR_SHARED_DIR_MODE", "0777")
    try:
        return int(str(raw), 8)
    except Exception:
        return 0o777


def _shared_file_mode() -> int:
    """返回 PaddleOCR 跨容器共享文件权限，默认 0666。"""
    raw = os.getenv("PADDLEOCR_SHARED_FILE_MODE", "0666")
    try:
        return int(str(raw), 8)
    except Exception:
        return 0o666


def _ensure_shared_writable_dir(path: Path) -> Path:
    """创建并放宽 backend/worker 共享目录权限。

    页级 batch 队列中，backend 负责拆页和提交任务，worker 负责写 batch 结果。
    两类容器可能使用不同 Linux 用户，因此共享目录需要允许双方读写。
    """
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, _shared_dir_mode())
    except Exception as exc:
        logger.warning(f"设置共享目录权限失败: {path} ({exc})")
    return path


def _ensure_shared_file(path: Path) -> Path:
    """尽量放宽共享文件权限，便于其他容器读取。"""
    try:
        os.chmod(path, _shared_file_mode())
    except Exception as exc:
        logger.debug(f"设置共享文件权限跳过: {path} ({exc})")
    return path


def _fsync_parent_dir(path: Path) -> None:
    """尽量刷新父目录元数据，减少 Docker Desktop 共享目录可见性竞态。"""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        # 某些平台/文件系统不支持目录 fsync，跳过即可。
        pass


def _write_shared_ready_marker(target: Path, *, payload: Optional[Dict[str, Any]] = None) -> Path:
    """为 batch PDF 写入 .ready 标记。

    backend 先原子写入 batch PDF，再写 ready 标记，worker 只有在 PDF 和
    ready 标记都可见时才开始解析。这样可以避免 Redis 入队速度快于
    bind mount 文件可见性导致的 FileNotFound。
    """
    marker = target.with_name(target.name + ".ready")
    marker_tmp = marker.with_name(marker.name + ".tmp")
    data = {"path": str(target), "size": target.stat().st_size if target.exists() else 0, "created_at": datetime.now().isoformat()}
    if payload:
        data.update(payload)
    with marker_tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(marker_tmp, marker)
    _ensure_shared_file(marker)
    _fsync_parent_dir(marker)
    return marker

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _cleanup_path_tree(path: Path, *, label: str = "") -> None:
    """删除 PaddleOCR 过程目录。

    页批次队列需要临时 PDF 和 Markdown。合并完成或失败后删除这些过程文件，
    最终只保留 `<pdf_stem>_extracted.md`。
    """
    try:
        if path.exists():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
            logger.info(f"已删除 PaddleOCR 过程目录{f'({label})' if label else ''}: {path}")
    except Exception as exc:
        logger.warning(f"删除 PaddleOCR 过程目录失败{f'({label})' if label else ''}: {path} ({exc})")


class _SimpleHTMLTableParser(HTMLParser):
    """轻量级 HTML 表格解析器，用于解析 PaddleOCR-VL Markdown 中的表格。"""

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
    """通过 PaddleOCR-VL v1.6 Redis 队列提取报告内容。

    主要环境变量：
        PADDLEOCR_PAGE_BATCH_SIZE: backend 拆分 PDF 的每批页数，默认 2。
        PADDLEOCR_VL_TIMEOUT: 解析等待超时时间，单位秒。
    """

    def __init__(self, config: ProcessingConfig | None = None):
        self.config = config or ProcessingConfig()
        self.logger = logger.bind(component="ContentExtractor")
        # 后台上传任务会在这里注入进度回调，用于 SSE 实时推送。
        self.progress_callback: Optional[Callable[..., None]] = None

    def _emit_progress(self, stage: str, message: str, progress: Optional[float] = None, **extra: Any) -> None:
        """Best-effort progress event for long OCR extraction."""
        cb = getattr(self, "progress_callback", None)
        if not cb:
            return
        try:
            cb(stage=stage, message=message, progress=progress, extra=extra or None)
        except Exception as exc:
            self.logger.debug(f"进度回调失败，已忽略: {exc}")

    def extract_pdf(self, file_path: str) -> DocumentContent:
        source_path = Path(file_path).resolve()
        if not source_path.exists():
            raise ContentExtractionError(f"文件不存在: {source_path}", file_path=str(source_path))

        start = time.time()
        try:
            self.logger.info(f"开始使用 PaddleOCR-VL v1.6 提取报告内容: {source_path}")
            self._emit_progress("ocr_start", "PaddleOCR-VL extraction started.", 10)
            result = self._run_paddleocr_vl_page_batch_queue(source_path)
            markdown = str(result.get("markdown") or "").strip()
            if not markdown:
                raise ContentExtractionError(
                    "PaddleOCR-VL 返回的 Markdown 为空",
                    file_path=str(source_path),
                )

            document_id = self._document_id(source_path)
            segments = self._segments_from_markdown(markdown, document_id)
            if not segments:
                segments = [
                    TextSegment(
                        segment_id=f"{document_id}_p1_s1",
                        content=markdown,
                        page_number=1,
                        position_y=0.0,
                        position_x=0.0,
                        segment_type="text",
                        structured_data={
                            "source": "paddleocr_vl_markdown_fallback",
                            "parser": "paddleocr-vl",
                            "pipeline_version": result.get("pipeline_version", "v1.6"),
                        },
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
                f"PaddleOCR-VL 内容提取完成: file={source_path.name}, segments={len(segments)}, "
                f"elapsed={elapsed:.2f}s, parser_output={result.get('output_dir', '')}"
            )
            self._emit_progress("ocr_done", f"OCR extraction completed with {len(segments)} segments.", 45, segments=len(segments))
            return document

        except ContentExtractionError:
            raise
        except Exception as exc:
            self.logger.exception(f"PaddleOCR-VL 内容提取失败: {source_path}")
            raise ContentExtractionError(f"PaddleOCR-VL 内容提取失败: {exc}", file_path=str(source_path)) from exc

    def save_markdown(self, document_content: DocumentContent, output_path: str | None = None) -> str:
        """保存供检索和审计使用的最终 Markdown，不包含 OCR 中间产物。"""
        if output_path is None:
            pdf_path = Path(document_content.file_path)
            output_path = str(pdf_path.parent / f"{pdf_path.stem}_extracted.md")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        final_markdown = self._format_final_extracted_markdown(document_content)
        out.write_text(final_markdown, encoding="utf-8")
        _ensure_shared_file(out)
        return str(out)

    def _format_final_extracted_markdown(self, document_content: DocumentContent) -> str:
        """只输出文件名、页码、可恢复的段落 ID 和正文。"""
        pdf_path = Path(document_content.file_path)
        display_segments = [
            seg for seg in document_content.segments
            if getattr(seg, "segment_type", "text") not in {"table_row", "table_cell"}
            and str(getattr(seg, "content", "") or "").strip()
        ]
        lines: list[str] = [f"# {pdf_path.name}"]

        current_page: int | None = None
        page_text_counts: dict[int, int] = {}
        page_table_counts: dict[int, int] = {}

        def page_num(seg: TextSegment) -> int:
            try:
                return max(1, int(getattr(seg, "page_number", 1) or 1))
            except Exception:
                return 1

        display_segments.sort(key=lambda s: (page_num(s), float(getattr(s, "position_y", 0.0) or 0.0), float(getattr(s, "position_x", 0.0) or 0.0)))

        for seg in display_segments:
            page = page_num(seg)
            if page != current_page:
                if current_page is not None:
                    lines.append("")
                lines.append(f"## 第 {page} 页")
                current_page = page

            content = str(getattr(seg, "content", "") or "").strip()
            segment_type = str(getattr(seg, "segment_type", "text") or "text").lower()

            if segment_type == "table":
                idx = page_table_counts.get(page, 0)
                page_table_counts[page] = idx + 1
                marker = f"P{page:03d}_T{idx:03d}"
                lines.extend(["", f"**{marker}**", "", content, "", "---"])
            else:
                idx = page_text_counts.get(page, 0)
                page_text_counts[page] = idx + 1
                marker = f"P{page:03d}_S{idx:03d}"
                lines.extend(["", f"**{marker}**", "", content, "", "---"])

        return "\n".join(lines).rstrip() + "\n"

    def _redis_client(self):
        try:
            import redis  # type: ignore
        except Exception as exc:
            raise ContentExtractionError(
                "PaddleOCR Redis 页批次模式需要安装 redis Python 包",
                file_path="",
            ) from exc

        redis_url = os.getenv("PADDLEOCR_TASK_QUEUE_URL", "redis://redis:6379/0").strip()
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=30, socket_connect_timeout=30)
        client.ping()
        return client

    def _redis_hash_set(self, client, key: str, mapping: Dict[str, Any]) -> None:
        safe: Dict[str, str] = {}
        for k, v in mapping.items():
            if isinstance(v, (dict, list)):
                safe[k] = json.dumps(v, ensure_ascii=False)
            else:
                safe[k] = "" if v is None else str(v)
        client.hset(key, mapping=safe)
        client.expire(key, int(os.getenv("PADDLEOCR_TASK_RESULT_TTL", "86400") or "86400"))

    def _remove_queued_batches_for_job(self, client, queue_name: str, job_id: str) -> int:
        """从 Redis 队列中移除同一 OCR job 的未开始 batch，避免失败后继续消费。"""
        try:
            items = client.lrange(queue_name, 0, -1) or []
            if not items:
                return 0
            kept = []
            removed = 0
            for raw_item in items:
                try:
                    payload = json.loads(raw_item)
                    if str(payload.get("job_id") or "") == job_id:
                        removed += 1
                        continue
                except Exception:
                    pass
                kept.append(raw_item)
            if removed:
                pipe = client.pipeline()
                pipe.delete(queue_name)
                for raw_item in kept:
                    pipe.rpush(queue_name, raw_item)
                pipe.execute()
                self.logger.warning(
                    f"已从 PaddleOCR 队列移除失败 job 的剩余 batch: job_id={job_id}, removed={removed}"
                )
            return removed
        except Exception as exc:
            self.logger.warning(f"清理失败 job 的 Redis 队列项失败: job_id={job_id}, error={exc}")
            return 0

    def _get_paddleocr_page_batch_size(self) -> int:
        """读取 PDF 拆分页数。

        注意：PDF 是在 backend 中拆分的，不是在 PaddleOCR worker 中拆分。
        因此 PADDLEOCR_PAGE_BATCH_SIZE 必须出现在 backend.environment 中。
        默认值为 2，配置只在 backend.environment 中设置。
        """
        raw = os.getenv("PADDLEOCR_PAGE_BATCH_SIZE", "2")
        try:
            value = int(str(raw).strip())
        except Exception:
            self.logger.warning(
                f"PADDLEOCR_PAGE_BATCH_SIZE={raw!r} 无法解析为整数，使用默认值 2"
            )
            value = 2
        if value < 1:
            self.logger.warning(
                f"PADDLEOCR_PAGE_BATCH_SIZE={raw!r} 小于 1，使用 1"
            )
            value = 1
        self.logger.info(
            f"PaddleOCR-VL PDF 拆分 batch size 生效: PADDLEOCR_PAGE_BATCH_SIZE={raw!r}, effective={value}"
        )
        return value

    def _split_pdf_for_page_batch_queue(
        self,
        source_path: Path,
        job_id: str,
        batch_size: int,
    ) -> tuple[list[dict], int, Path]:
        try:
            from pypdf import PdfReader, PdfWriter  # type: ignore
        except Exception as exc:
            raise ContentExtractionError(
                "页级 batch 队列需要 backend 安装 pypdf",
                file_path=str(source_path),
            ) from exc

        work_root = Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs"))
        batch_dir = work_root / job_id / "batches"
        if batch_dir.exists():
            import shutil

            shutil.rmtree(batch_dir, ignore_errors=True)
        _ensure_shared_writable_dir(batch_dir)

        reader = PdfReader(str(source_path))
        total_pages = len(reader.pages)
        if total_pages <= 0:
            raise ContentExtractionError("PDF 没有可解析页", file_path=str(source_path))

        units: list[dict] = []
        for start in range(0, total_pages, max(1, batch_size)):
            end = min(start + max(1, batch_size), total_pages)
            writer = PdfWriter()
            for page_index in range(start, end):
                writer.add_page(reader.pages[page_index])
            unit_index = len(units) + 1
            batch_path = batch_dir / f"pages_{start + 1:04d}_{end:04d}.pdf"
            tmp_path = batch_path.with_name(batch_path.name + ".tmp")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            # 先写临时文件，flush/fsync 后再原子 rename 成最终文件名。
            # 这样 worker 不会读到半写入的 PDF。
            with tmp_path.open("wb") as f:
                writer.write(f)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp_path, batch_path)
            _ensure_shared_file(batch_path)
            _fsync_parent_dir(batch_path)
            ready_path = _write_shared_ready_marker(
                batch_path,
                payload={
                    "job_id": job_id,
                    "unit_index": unit_index,
                    "start_page": start + 1,
                    "end_page": end,
                    "total_pages": total_pages,
                },
            )
            units.append(
                {
                    "unit_index": unit_index,
                    "batch_id": f"batch_{unit_index:04d}",
                    "start_page": start + 1,
                    "end_page": end,
                    "input_path": str(batch_path),
                    "ready_path": str(ready_path),
                }
            )

        # 这里先在 backend 容器内做一次可见性校验，再入 Redis 队列。
        # 这不能替代 worker 侧等待，但能提前发现目录/权限/挂载异常。
        visibility_wait = float(os.getenv("PADDLEOCR_SPLIT_VISIBILITY_WAIT_SECONDS", "60") or "60")
        deadline = time.time() + max(0.0, visibility_wait)
        while True:
            missing = []
            for unit in units:
                p = Path(unit["input_path"])
                m = Path(unit["ready_path"])
                try:
                    if not (p.exists() and p.stat().st_size > 0 and m.exists() and m.stat().st_size > 0):
                        missing.append(str(p))
                except Exception:
                    missing.append(str(p))
            if not missing:
                break
            if time.time() >= deadline:
                raise ContentExtractionError(
                    f"PaddleOCR batch 文件写入后不可见，无法入队: missing={missing[:3]}",
                    file_path=str(source_path),
                )
            time.sleep(0.25)

        return units, total_pages, batch_dir

    def _run_paddleocr_vl_page_batch_queue(self, source_path: Path) -> Dict[str, Any]:
        client = self._redis_client()

        queue_name = os.getenv("PADDLEOCR_TASK_QUEUE_NAME", "paddleocr:parse").strip()
        key_prefix = os.getenv("PADDLEOCR_TASK_KEY_PREFIX", "paddleocr:task").strip()
        timeout = int(os.getenv("PADDLEOCR_VL_TIMEOUT", "14400") or "14400")
        poll_interval = float(os.getenv("PADDLEOCR_TASK_POLL_INTERVAL", "2.0") or "2.0")
        batch_size = self._get_paddleocr_page_batch_size()
        output_root = Path(os.getenv("PADDLEOCR_OUTPUT_DIR", "/workspace/uploads/paddleocr_vl_output"))

        job_id = f"parse_{uuid.uuid4().hex}"
        task_key = f"{key_prefix}:{job_id}"
        output_dir = output_root / job_id
        _ensure_shared_writable_dir(output_root)
        _ensure_shared_writable_dir(output_dir)
        _ensure_shared_writable_dir(output_dir / "batches")

        units, total_pages, batch_dir = self._split_pdf_for_page_batch_queue(source_path, job_id, batch_size)
        total_units = len(units)

        self.logger.info(
            f"提交 PaddleOCR-VL 页级 batch 队列任务: job_id={job_id}, file={source_path.name}, "
            f"pages={total_pages}, units={total_units}, batch_size={batch_size}, queue={queue_name}"
        )
        self._emit_progress(
            "ocr_queued",
            f"PaddleOCR queued: {total_pages} pages split into {total_units} batch(es).",
            12,
            paddle_progress={
                "paddle_job_id": job_id,
                "total_pages": total_pages,
                "pages_done": 0,
                "pages_success": 0,
                "pages_failed": 0,
                "total_units": total_units,
                "units_done": 0,
                "units_success": 0,
                "units_failed": 0,
                "units_running": 0,
                "units_queued": total_units,
                "page_batch_size": batch_size,
                "running_batches": [],
            },
            paddle_job_id=job_id,
            total_pages=total_pages,
            total_units=total_units,
            page_batch_size=batch_size,
        )

        self._redis_hash_set(
            client,
            task_key,
            {
                "status": "running",
                "stage": "batch_queued",
                "queue_granularity": "page-batch",
                "filename": source_path.name,
                "input_path": str(source_path),
                "batch_dir": str(batch_dir),
                "output_dir": str(output_dir),
                "created_at": datetime.now().isoformat(),
                "total_pages": total_pages,
                "total_units": total_units,
                "units_done": 0,
                "page_batch_size": batch_size,
            },
        )

        for unit in units:
            unit_index = int(unit["unit_index"])
            batch_key = f"{task_key}:batch:{unit_index:04d}"
            payload = {
                "task_type": "page_batch",
                "job_id": job_id,
                "filename": source_path.name,
                "unit_index": unit_index,
                "batch_id": unit["batch_id"],
                "total_units": total_units,
                "start_page": unit["start_page"],
                "end_page": unit["end_page"],
                "total_pages": total_pages,
                "input_path": unit["input_path"],
                "ready_path": unit.get("ready_path", ""),
                "created_at": datetime.now().isoformat(),
            }
            self._redis_hash_set(
                client,
                batch_key,
                {
                    "status": "queued",
                    "stage": "queued",
                    "job_id": job_id,
                    "filename": source_path.name,
                    "unit_index": unit_index,
                    "total_units": total_units,
                    "start_page": unit["start_page"],
                    "end_page": unit["end_page"],
                    "input_path": unit["input_path"],
                    "ready_path": unit.get("ready_path", ""),
                    "created_at": datetime.now().isoformat(),
                },
            )
            client.rpush(queue_name, json.dumps(payload, ensure_ascii=False))

        deadline = time.time() + timeout
        last_progress_signature = None
        # 即使 batch 状态没有变化，也要定时推送“仍在处理”的进度。
        # 否则单个 PaddleOCR batch 处理较久时，前端会看起来像卡住。
        progress_emit_interval = float(os.getenv("PADDLEOCR_PROGRESS_EMIT_INTERVAL", "5.0") or "5.0")
        last_progress_emit_ts = 0.0
        while time.time() < deadline:
            batch_states = [client.hgetall(f"{task_key}:batch:{i:04d}") for i in range(1, total_units + 1)]
            statuses = [str(s.get("status", "queued")).lower() for s in batch_states]
            done_count = sum(1 for s in statuses if s in {"success", "completed"})
            failed_count = sum(1 for s in statuses if s == "failed")
            running_count = sum(1 for s in statuses if s in {"running", "predict", "processing"})
            queued_count = sum(1 for s in statuses if s in {"queued", "waiting", "waiting_model"})

            def _int_field(item: Dict[str, Any], name: str, default: int = 0) -> int:
                try:
                    return int(item.get(name) or default)
                except Exception:
                    return default

            pages_success = sum(
                max(0, _int_field(st, "end_page") - _int_field(st, "start_page") + 1)
                for st, status in zip(batch_states, statuses)
                if status in {"success", "completed"}
            )
            pages_failed = sum(
                max(0, _int_field(st, "end_page") - _int_field(st, "start_page") + 1)
                for st, status in zip(batch_states, statuses)
                if status == "failed"
            )
            pages_done = pages_success + pages_failed
            running_batches = []
            for st, status in zip(batch_states, statuses):
                if status in {"running", "predict", "processing"}:
                    running_batches.append(
                        {
                            "unit_index": _int_field(st, "unit_index"),
                            "start_page": _int_field(st, "start_page"),
                            "end_page": _int_field(st, "end_page"),
                            "worker_id": st.get("worker_id") or "",
                        }
                    )
            running_signature = tuple(
                (b.get("unit_index"), b.get("start_page"), b.get("end_page"), b.get("worker_id"))
                for b in running_batches
            )
            progress_signature = (done_count, failed_count, running_count, queued_count, running_signature)

            now_ts = time.time()
            should_emit_progress = (
                progress_signature != last_progress_signature
                or (progress_emit_interval > 0 and now_ts - last_progress_emit_ts >= progress_emit_interval)
            )

            if should_emit_progress:
                running_pages_text = ", ".join(
                    f"p{b['start_page']}-{b['end_page']}" if b["start_page"] != b["end_page"] else f"p{b['start_page']}"
                    for b in running_batches[:3]
                ) or "-"
                self.logger.info(
                    f"PaddleOCR-VL page-batch job={job_id} done={done_count}/{total_units} "
                    f"batch={done_count + failed_count}/{total_units} failed={failed_count} "
                    f"running={running_count} queued={queued_count} pages={pages_done}/{total_pages} "
                    f"running_pages={running_pages_text}"
                )
                last_progress_signature = progress_signature
                last_progress_emit_ts = now_ts
                self._redis_hash_set(
                    client,
                    task_key,
                    {
                        "status": "running",
                        "stage": "batch_processing",
                        "units_done": done_count + failed_count,
                        "units_success": done_count,
                        "units_failed": failed_count,
                        "units_running": running_count,
                        "units_queued": queued_count,
                        "pages_done": pages_done,
                        "pages_success": pages_success,
                        "pages_failed": pages_failed,
                        "updated_at": datetime.now().isoformat(),
                    },
                )
                ocr_progress = 12 + (33 * ((done_count + failed_count) / max(1, total_units)))
                running_text = ""
                if running_batches:
                    shown = ", ".join(
                        f"p{b['start_page']}-{b['end_page']}" if b["start_page"] != b["end_page"] else f"p{b['start_page']}"
                        for b in running_batches[:3]
                    )
                    running_text = f" Running: {shown}."
                self._emit_progress(
                    "ocr_batch_processing",
                    f"PaddleOCR progress: {done_count + failed_count}/{total_units} batches, {pages_done}/{total_pages} pages done.{running_text}",
                    ocr_progress,
                    paddle_progress={
                        "paddle_job_id": job_id,
                        "total_pages": total_pages,
                        "pages_done": pages_done,
                        "pages_success": pages_success,
                        "pages_failed": pages_failed,
                        "total_units": total_units,
                        "units_done": done_count + failed_count,
                        "units_success": done_count,
                        "units_failed": failed_count,
                        "units_running": running_count,
                        "units_queued": queued_count,
                        "page_batch_size": batch_size,
                        "running_batches": running_batches,
                        "updated_at": datetime.now().isoformat(),
                    },
                    paddle_job_id=job_id,
                    units_done=done_count + failed_count,
                    units_success=done_count,
                    units_failed=failed_count,
                    units_running=running_count,
                    units_queued=queued_count,
                    pages_done=pages_done,
                    pages_success=pages_success,
                    pages_failed=pages_failed,
                    total_pages=total_pages,
                    total_units=total_units,
                    running_batches=running_batches,
                )

            if failed_count:
                errors = [s.get("error", "unknown worker error") for s in batch_states if str(s.get("status", "")).lower() == "failed"]
                error_text = errors[0] if errors else "unknown worker error"
                self._redis_hash_set(
                    client,
                    task_key,
                    {
                        "status": "failed",
                        "stage": "failed",
                        "cancel_requested": "1",
                        "failed_at": datetime.now().isoformat(),
                        "error": error_text,
                    },
                )
                self._remove_queued_batches_for_job(client, queue_name, job_id)
                # 给当前 worker 一点时间退出/释放，避免 backend 立刻删除目录后 worker 又开始下一批。
                if running_count:
                    time.sleep(float(os.getenv("PADDLEOCR_FAILURE_CLEANUP_DELAY", "3.0") or "3.0"))
                if not _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False):
                    _cleanup_path_tree(output_dir, label="失败任务 worker batch 输出")
                    _cleanup_path_tree(Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs")) / job_id, label="失败任务拆页 PDF")
                raise ContentExtractionError(
                    f"PaddleOCR-VL 页级 batch 解析失败: job_id={job_id}, error={error_text}",
                    file_path=str(source_path),
                )

            if done_count + failed_count >= total_units:
                self._emit_progress("ocr_merging", "Merging OCR batch results.", 45, paddle_job_id=job_id, total_units=total_units)
                return self._merge_paddleocr_page_batch_results(
                    client=client,
                    task_key=task_key,
                    job_id=job_id,
                    source_path=source_path,
                    batch_states=batch_states,
                    output_dir=output_dir,
                    total_pages=total_pages,
                    total_units=total_units,
                    batch_size=batch_size,
                )

            time.sleep(max(0.25, poll_interval))

        self._redis_hash_set(
            client,
            task_key,
            {
                "status": "failed",
                "stage": "timeout",
                "cancel_requested": "1",
                "failed_at": datetime.now().isoformat(),
                "error": f"timeout={timeout}s",
            },
        )
        self._remove_queued_batches_for_job(client, queue_name, job_id)
        if not _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False):
            _cleanup_path_tree(output_dir, label="超时任务 worker batch 输出")
            _cleanup_path_tree(Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs")) / job_id, label="超时任务拆页 PDF")
        raise ContentExtractionError(
            f"PaddleOCR-VL 页级 batch 队列解析超时: job_id={job_id}, timeout={timeout}s",
            file_path=str(source_path),
        )

    def _merge_paddleocr_page_batch_results(
        self,
        *,
        client,
        task_key: str,
        job_id: str,
        source_path: Path,
        batch_states: list[dict],
        output_dir: Path,
        total_pages: int,
        total_units: int,
        batch_size: int,
    ) -> Dict[str, Any]:
        combined_parts: list[str] = []
        elapsed_total = 0.0

        for idx, state in enumerate(batch_states, 1):
            status = str(state.get("status", "")).lower()
            if status in {"success", "completed"}:
                try:
                    result = json.loads(state.get("result_json") or "{}")
                except Exception:
                    result = dict(state)
                md_path = Path(result.get("batch_markdown_path") or state.get("batch_markdown_path") or "")
                if not md_path.exists():
                    raise ContentExtractionError(
                        f"PaddleOCR-VL batch 完成但 batch.md 不存在: job_id={job_id}, unit={idx}, path={md_path}",
                        file_path=str(source_path),
                    )
                md = md_path.read_text(encoding="utf-8", errors="ignore").strip()
                if md:
                    combined_parts.append(md)
                try:
                    elapsed_total += float(result.get("elapsed_seconds") or 0.0)
                except Exception:
                    pass
            elif status == "failed":
                error = state.get("error") or state.get("traceback") or "unknown worker error"
                raise ContentExtractionError(
                    f"PaddleOCR-VL batch failed: job_id={job_id}, unit={idx}, error={error}",
                    file_path=str(source_path),
                )
            else:
                raise ContentExtractionError(
                    f"PaddleOCR-VL batch 状态异常: job_id={job_id}, unit={idx}, status={status}",
                    file_path=str(source_path),
                )

        markdown = "\n".join(part for part in combined_parts if str(part).strip()).strip()
        if not markdown:
            raise ContentExtractionError("PaddleOCR-VL 页级 batch 没有生成 Markdown", file_path=str(source_path))

        keep_process_output = _env_bool("PADDLEOCR_KEEP_PROCESS_OUTPUT", False)
        combined_md_path = output_dir / "combined.md"
        result_markdown_path = ""
        if keep_process_output:
            _ensure_shared_writable_dir(output_dir)
            combined_md_path.write_text(markdown, encoding="utf-8")
            _ensure_shared_file(combined_md_path)
            result_markdown_path = str(combined_md_path)

        result = {
            "status": "success",
            "parser": "paddleocr-vl",
            "pipeline_version": "v1.6",
            "queue_granularity": "page-batch",
            "mode": "page-batch",
            "page_batch_size": batch_size,
            "total_pages": total_pages,
            "units_processed": total_units,
            "elapsed_worker_seconds_sum": elapsed_total,
            "output_dir": str(output_dir) if keep_process_output else "",
            "result_markdown_path": result_markdown_path,
            "intermediate_output_removed": not keep_process_output,
            "markdown": markdown,
        }
        self._redis_hash_set(
            client,
            task_key,
            {
                "status": result["status"],
                "stage": "completed",
                "completed_at": datetime.now().isoformat(),
                "result_json": {k: v for k, v in result.items() if k != "markdown"},
                "output_dir": result["output_dir"],
                "result_markdown_path": result_markdown_path,
                "intermediate_output_removed": str(not keep_process_output).lower(),
            },
        )

        if not keep_process_output:
            _cleanup_path_tree(output_dir, label="worker batch 输出")
            work_root = Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs"))
            _cleanup_path_tree(work_root / job_id, label="backend 拆页 PDF")

        return result

    # ------------------------------------------------------------------
    # 从 PaddleOCR-VL Markdown 输出构造 TextSegment
    # ------------------------------------------------------------------

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
                        structured_data={
                            "source": "paddleocr_vl_markdown",
                            "parser": "paddleocr-vl",
                            "table_id": table_id,
                            "table_title": current_heading,
                        },
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
                    structured_data={"source": "paddleocr_vl_markdown", "parser": "paddleocr-vl"},
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
        if not stripped:
            return False
        if "<tr" in stripped.lower() or "<td" in stripped.lower() or "<th" in stripped.lower():
            return True
        if "|" not in stripped:
            return False
        return stripped.count("|") >= 2 or (stripped.startswith("|") and "|" in stripped[1:])

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
                        "source": "paddleocr_vl_table_parser",
                        "parser": "paddleocr-vl",
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
                            "source": "paddleocr_vl_table_parser",
                            "parser": "paddleocr-vl",
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
        return [self._normalise_table_row(row) for row in parser.rows if any(str(c).strip() for c in row)]

    def _parse_markdown_table_rows(self, table_md: str) -> List[List[str]]:
        rows: List[List[str]] = []
        for line in table_md.splitlines():
            stripped = line.strip()
            if not stripped or "|" not in stripped:
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not cells:
                continue
            if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c):
                continue
            rows.append(cells)
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

    def _page_marker(self, block: str) -> Optional[int]:
        patterns = [
            # 服务端页码标记格式：<!-- Page 12 | PaddleOCR-VL unit 12/116 part 1 -->
            # 只捕获紧跟在 Page 后面的页码，避免误取后面的 unit/part 数字。
            r"<!--\s*page\s+(\d+)\b",
            r"<!--\s*paddleocr-vl\s+page/part\s*:?.*?page\s*(\d+)\b",
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
    # 工具函数
    # ------------------------------------------------------------------

    def _document_id(self, path: Path) -> str:
        digest = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:8]
        return f"doc_{self._safe_name(path.stem)}_{digest}"

    def _safe_name(self, text: str) -> str:
        value = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(text or "")).strip("_")
        return value or "document"
