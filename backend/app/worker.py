"""SQLite-backed task worker.

Run with:
    python -m app.worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid

from app.database import async_session_factory
from app.extraction.base import FieldSpec
from app.models.task import ContractTask
from app.services.pipeline import run_extraction_pipeline, run_ocr_pipeline
from app.services.task_service import claim_next_task, mark_task_retry_or_failed, recover_expired_tasks

logger = logging.getLogger(__name__)

_task_wakeup_event: asyncio.Event | None = None


def _get_wakeup_event() -> asyncio.Event:
    global _task_wakeup_event
    if _task_wakeup_event is None:
        _task_wakeup_event = asyncio.Event()
    return _task_wakeup_event


def notify_task_available() -> None:
    """Wake the in-process worker after a task is committed."""
    try:
        _get_wakeup_event().set()
    except RuntimeError:
        # No running event loop in this process; standalone workers rely on fallback polling.
        return


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _field_specs_from_payload(task: ContractTask) -> list[FieldSpec] | None:
    payload = task.task_payload or {}
    fields = payload.get("fields")
    if not fields:
        return None
    return [FieldSpec.model_validate(field) for field in fields]


async def _dispatch(task: ContractTask, session_factory=async_session_factory) -> None:
    if task.task_type == "ocr":
        await run_ocr_pipeline(task.id, session_factory=session_factory)
        return
    if task.task_type == "extraction":
        await run_extraction_pipeline(
            task.id,
            _field_specs_from_payload(task),
            session_factory=session_factory,
        )
        return
    raise ValueError(f"Unsupported task type: {task.task_type}")


async def run_worker(
    *,
    poll_interval: float = 30.0,
    stop_after_idle: bool = False,
    session_factory=async_session_factory,
) -> None:
    worker_id = _worker_id()
    logger.info("Task worker started: %s", worker_id)

    try:
        while True:
            wakeup_event = _get_wakeup_event()
            wakeup_event.clear()
            try:
                async with session_factory() as db:
                    await recover_expired_tasks(db)
                    task = await claim_next_task(db, worker_id=worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Worker %s failed while polling queue", worker_id, exc_info=True)
                if stop_after_idle:
                    return
                await asyncio.sleep(poll_interval)
                continue

            if task is None:
                if stop_after_idle:
                    return
                try:
                    await asyncio.wait_for(wakeup_event.wait(), timeout=poll_interval)
                except asyncio.TimeoutError:
                    pass
                continue

            logger.info("Worker %s claimed task %s (%s)", worker_id, task.id, task.task_type)
            try:
                await _dispatch(task, session_factory=session_factory)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Worker %s task %s failed: %s", worker_id, task.id, exc, exc_info=True)
                async with session_factory() as db:
                    await mark_task_retry_or_failed(db, task.id, error_message=str(exc))
    except asyncio.CancelledError:
        logger.info("Task worker stopped: %s", worker_id)
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
