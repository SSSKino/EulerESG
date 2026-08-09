"""只处理 Redis page_batch 任务的 PaddleOCR-VL v1.6 worker。"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from loguru import logger
from logging_config import configure_logging

configure_logging(os.getenv("PADDLEOCR_WORKER_ID", "paddleocr-worker"))

from parse_core import (
    PaddleOCRModelLoadError,
    get_pipeline,
    parse_page_batch,
    release_pipeline,
    schedule_idle_unload,
    should_restart_worker_after_task,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name)
    try:
        value = int(str(raw if raw is not None else default).strip())
    except Exception:
        value = default
    return max(min_value, value)


def _env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    raw = os.getenv(name)
    try:
        value = float(str(raw if raw is not None else default).strip())
    except Exception:
        value = default
    return max(min_value, value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _redis_client():
    try:
        import redis  # type: ignore
    except Exception as exc:  # pragma: no cover - 运行时依赖
        raise RuntimeError("redis package is required for paddleocr-worker") from exc

    url = os.getenv("PADDLEOCR_TASK_QUEUE_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=30, socket_connect_timeout=30)


class PageBatchTimeoutError(TimeoutError):
    """单个 page-batch 超时。"""


class _PageBatchTimeout:
    """Linux 容器内使用 SIGALRM 限制单个 batch 的最长运行时间。

    如果 PaddleOCR 底层 C++/CUDA 调用长时间不返回，信号可能需要等到控制权回到 Python 后才触发；
    但对于大多数卡在 Python 调度/下载/后处理的情况，可以及时失败并让任务状态可见。
    """

    def __init__(self, seconds: int, label: str) -> None:
        self.seconds = max(0, int(seconds or 0))
        self.label = label
        self._old_handler = None

    def __enter__(self):
        if self.seconds <= 0:
            return self

        def _handler(signum, frame):  # noqa: ARG001
            raise PageBatchTimeoutError(f"PaddleOCR page-batch 超时: {self.label}, timeout={self.seconds}s")

        self._old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.seconds <= 0:
            return
        signal.setitimer(signal.ITIMER_REAL, 0)
        if self._old_handler is not None:
            signal.signal(signal.SIGALRM, self._old_handler)


def _task_key(job_id: str) -> str:
    prefix = os.getenv("PADDLEOCR_TASK_KEY_PREFIX", "paddleocr:task")
    return f"{prefix}:{job_id}"


def _batch_key(job_id: str, unit_index: int) -> str:
    return f"{_task_key(job_id)}:batch:{unit_index:04d}"


def _hash_set(r, key: str, mapping: Dict[str, Any]) -> None:
    safe: Dict[str, str] = {}
    for k, v in mapping.items():
        if isinstance(v, (dict, list)):
            safe[k] = json.dumps(v, ensure_ascii=False)
        else:
            safe[k] = "" if v is None else str(v)
    r.hset(key, mapping=safe)
    ttl = _env_int("PADDLEOCR_TASK_RESULT_TTL", 86400, min_value=60)
    r.expire(key, ttl)


def _set_batch_status(r, job_id: str, unit_index: int, mapping: Dict[str, Any]) -> None:
    _hash_set(r, _batch_key(job_id, unit_index), mapping)


def _increment_parent_done(r, job_id: str, *, status: str, unit_index: int) -> None:
    key = _task_key(job_id)
    try:
        r.hincrby(key, "units_done", 1)
    except Exception:
        pass
    _hash_set(
        r,
        key,
        {
            "status": "running" if status == "success" else "partial_running",
            "stage": "batch_done" if status == "success" else "batch_failed",
            "last_finished_unit": unit_index,
            "updated_at": _utc_now(),
        },
    )


def _job_cancelled(r, job_id: str) -> bool:
    """如果 backend 已经判定父 job 失败/取消，则 worker 不再继续消费该 job 的剩余 batch。"""
    try:
        parent = r.hgetall(_task_key(job_id)) or {}
    except Exception:
        return False
    status = str(parent.get("status", "")).lower()
    cancel_requested = str(parent.get("cancel_requested", "")).strip().lower() in {"1", "true", "yes", "y", "on"}
    return cancel_requested or status in {"failed", "cancelled", "cancelling"}


def _handle_page_batch(r, worker_id: str, payload: Dict[str, Any]) -> None:
    task_started = time.monotonic()
    job_id = str(payload.get("job_id") or "")
    if not job_id:
        raise ValueError("page_batch task missing job_id")

    unit_index = int(payload.get("unit_index") or 1)
    total_units = int(payload.get("total_units") or 1)
    start_page = int(payload.get("start_page") or 1)
    end_page = int(payload.get("end_page") or start_page)
    total_pages = int(payload.get("total_pages") or end_page)
    batch_id = str(payload.get("batch_id") or f"batch_{unit_index:04d}")
    input_path = str(payload.get("input_path") or "")
    ready_path = str(payload.get("ready_path") or "")
    filename = str(payload.get("filename") or "")

    logger.debug(
        "Starting page batch job={} unit={}/{} pages={}-{}",
        job_id,
        unit_index,
        total_units,
        start_page,
        end_page,
    )

    if _job_cancelled(r, job_id):
        logger.warning("Parent job cancelled; skipping page batch job={} unit={}/{}", job_id, unit_index, total_units)
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "skipped",
                "stage": "parent_cancelled",
                "worker_id": worker_id,
                "updated_at": _utc_now(),
                "unit_index": unit_index,
                "total_units": total_units,
                "start_page": start_page,
                "end_page": end_page,
            },
        )
        return

    _set_batch_status(
        r,
        job_id,
        unit_index,
        {
            "status": "running",
            "worker_id": worker_id,
            "started_at": _utc_now(),
            "filename": filename,
            "input_path": input_path,
            "ready_path": ready_path,
            "stage": "predict",
            "batch_id": batch_id,
            "unit_index": unit_index,
            "total_units": total_units,
            "start_page": start_page,
            "end_page": end_page,
            "total_pages": total_pages,
        },
    )

    batch_timeout = _env_int("PADDLEOCR_BATCH_TIMEOUT_SECONDS", 600, min_value=0)
    try:
        timeout_label = f"job={job_id} unit={unit_index}/{total_units} pages={start_page}-{end_page}"
        with _PageBatchTimeout(batch_timeout, timeout_label):
            result = parse_page_batch(
                input_path,
                filename=filename,
                job_id=job_id,
                batch_id=batch_id,
                unit_index=unit_index,
                total_units=total_units,
                start_page=start_page,
                end_page=end_page,
                total_pages=total_pages,
                ready_path=ready_path or None,
            )
        result_for_redis = dict(result)
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": result_for_redis.get("status", "success"),
                "stage": "completed",
                "completed_at": _utc_now(),
                "worker_id": worker_id,
                "result_json": result_for_redis,
                "output_dir": result_for_redis.get("output_dir", ""),
                "batch_markdown_path": result_for_redis.get("batch_markdown_path", ""),
                "elapsed_seconds": result_for_redis.get("elapsed_seconds", ""),
            },
        )
        _increment_parent_done(r, job_id, status="success", unit_index=unit_index)
        logger.debug("Completed page batch job={} unit={}/{}", job_id, unit_index, total_units)
    except PageBatchTimeoutError as exc:
        tb = traceback.format_exc()
        elapsed_seconds = time.monotonic() - task_started
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "failed",
                "stage": "timeout",
                "error_type": "batch_timeout",
                "timeout_seconds": batch_timeout,
                "elapsed_seconds": elapsed_seconds,
                "failed_at": _utc_now(),
                "worker_id": worker_id,
                "unit_index": unit_index,
                "total_units": total_units,
                "start_page": start_page,
                "end_page": end_page,
                "error": str(exc),
                "traceback": tb[-8000:],
            },
        )
        _increment_parent_done(r, job_id, status="failed", unit_index=unit_index)
        logger.error(
            "page-batch 超时: job={} unit={}/{} pages={}-{} timeout={}s elapsed={:.3f}s",
            job_id,
            unit_index,
            total_units,
            start_page,
            end_page,
            batch_timeout,
            elapsed_seconds,
        )
        raise
    except PaddleOCRModelLoadError as exc:
        # 模型没有准备好时，不把当前 batch 计为失败。
        # main() 会把 payload 重新放回队列，并退出 worker 等待 Docker 重启。
        tb = traceback.format_exc()
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "waiting_model",
                "stage": "model_load_failed",
                "updated_at": _utc_now(),
                "worker_id": worker_id,
                "error_type": "model_load_error",
                "error": str(exc),
                "traceback": tb[-8000:],
            },
        )
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "failed",
                "stage": "failed",
                "failed_at": _utc_now(),
                "worker_id": worker_id,
                "error_type": type(exc).__name__,
                "elapsed_seconds": time.monotonic() - task_started,
                "unit_index": unit_index,
                "total_units": total_units,
                "start_page": start_page,
                "end_page": end_page,
                "error": str(exc),
                "traceback": tb[-8000:],
            },
        )
        _increment_parent_done(r, job_id, status="failed", unit_index=unit_index)
        raise


def _maybe_preflight_model() -> None:
    """可选：worker 启动时先验证模型可加载，再开始消费 Redis 任务。"""
    if not _env_bool("PADDLEOCR_PREFLIGHT_ON_START", False):
        return
    logger.info("执行 PaddleOCR-VL worker 启动前模型预检")
    get_pipeline()
    schedule_idle_unload()
    logger.info("PaddleOCR-VL worker 模型预检通过")


def _requeue_payload(r, queue_name: str, payload_raw: str) -> None:
    if not _env_bool("PADDLEOCR_REQUEUE_ON_MODEL_ERROR", True):
        return
    try:
        # backend 使用 RPUSH 入队，worker 使用 BLPOP 消费。这里用 LPUSH 放回队头，
        # 这样模型修复后会优先继续当前失败的 batch。
        r.lpush(queue_name, payload_raw)
        logger.warning("模型未就绪，当前任务已重新放回队列头: queue={}", queue_name)
    except Exception as exc:
        logger.error("重新入队失败: {}", exc)


def _release_request_key() -> str:
    return os.getenv(
        "PADDLEOCR_RELEASE_REQUEST_KEY",
        "paddleocr:control:release",
    ).strip()


def _maybe_release_requested(
    r,
    *,
    worker_id: str,
    queue_name: str,
    last_request_id: str,
) -> str:
    """Release this worker only after the shared OCR queue is idle."""
    request_key = _release_request_key()
    try:
        request_id = str(r.hget(request_key, "request_id") or "").strip()
    except Exception as exc:
        logger.warning("Failed to read PaddleOCR release request: {}", exc)
        return last_request_id
    if not request_id or request_id == last_request_id:
        return last_request_id

    try:
        if int(r.llen(queue_name) or 0) > 0:
            return last_request_id
    except Exception as exc:
        logger.warning("Failed to verify PaddleOCR queue before release: {}", exc)
        return last_request_id

    release_pipeline(f"document completed request={request_id}")
    try:
        r.hset(
            request_key,
            mapping={
                f"ack:{worker_id}": request_id,
                f"ack_at:{worker_id}": _utc_now(),
            },
        )
        r.expire(
            request_key,
            _env_int("PADDLEOCR_TASK_RESULT_TTL", 86400, min_value=60),
        )
    except Exception as exc:
        # GPU memory is already released. Leave the request pending so the next
        # idle poll can retry the acknowledgement.
        logger.warning("PaddleOCR release acknowledgement failed: {}", exc)
        return last_request_id

    logger.info(
        "PaddleOCR worker pipeline released: worker_id={} request_id={}",
        worker_id,
        request_id,
    )
    return request_id


def main() -> int:
    worker_id = os.getenv("PADDLEOCR_WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    queue_name = os.getenv("PADDLEOCR_TASK_QUEUE_NAME", "paddleocr:parse")
    block_timeout = _env_int("PADDLEOCR_QUEUE_BLOCK_TIMEOUT", 5, min_value=1)
    idle_sleep = _env_float("PADDLEOCR_WORKER_IDLE_SLEEP", 0.25, min_value=0.0)

    r = _redis_client()
    logger.info(
        "PaddleOCR-VL 队列 worker 已启动: worker_id={} queue={} redis={}",
        worker_id,
        queue_name,
        os.getenv("PADDLEOCR_TASK_QUEUE_URL", "redis://redis:6379/0"),
    )

    # 如果 Redis 不可达，立即失败；compose 的 restart 策略会自动重试。
    r.ping()

    try:
        _maybe_preflight_model()
    except PaddleOCRModelLoadError:
        logger.exception("PaddleOCR-VL 模型预检失败，worker 将退出等待重启")
        release_pipeline("model preflight failed")
        return 2

    try:
        # Ignore a stale request from a previous container lifecycle. Only a
        # request created after startup should undo the worker prewarm.
        last_release_request_id = str(
            r.hget(_release_request_key(), "request_id") or ""
        ).strip()
    except Exception:
        last_release_request_id = ""

    while True:
        item = r.blpop(queue_name, timeout=block_timeout)
        if item is None:
            last_release_request_id = _maybe_release_requested(
                r,
                worker_id=worker_id,
                queue_name=queue_name,
                last_request_id=last_release_request_id,
            )
            if idle_sleep:
                time.sleep(idle_sleep)
            continue

        _, payload_raw = item
        try:
            payload = json.loads(payload_raw)
        except Exception:
            logger.error("Discarding invalid task payload payload_bytes={}", len(payload_raw.encode("utf-8", errors="replace")))
            continue

        task_type = str(payload.get("task_type") or "").strip().lower()
        if task_type not in {"page_batch", "page-batch", "batch"}:
            logger.error("Discarding unsupported task type task_type={}", task_type or "missing")
            continue
        try:
            _handle_page_batch(r, worker_id, payload)

            if should_restart_worker_after_task():
                logger.info("PADDLEOCR_RESTART_AFTER_TASKS reached; releasing model and exiting for clean restart")
                release_pipeline("restart after configured task count")
                return 0

        except PaddleOCRModelLoadError:
            logger.exception("PaddleOCR-VL 模型加载失败，当前任务不会被计为解析失败")
            _requeue_payload(r, queue_name, payload_raw)
            release_pipeline("model load failed")
            return 2
        except Exception:
            logger.exception(
                "PaddleOCR task failed task_type={} job_id={} unit_index={}",
                task_type,
                payload.get("job_id", ""),
                payload.get("unit_index", ""),
            )
            release_pipeline("task failed")
            if _env_bool("PADDLEOCR_EXIT_ON_TASK_FAILURE", True):
                logger.warning("任务失败后退出 worker，由 Docker 重启以清理 PaddleOCR/VLM 内部线程状态")
                return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logger.info("PaddleOCR worker interrupted; releasing pipeline")
        release_pipeline("keyboard interrupt")
        raise SystemExit(0)
