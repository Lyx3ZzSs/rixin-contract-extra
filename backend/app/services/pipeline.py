"""Contract extraction pipeline.

Runs from the SQLite-backed worker queue.

Status flow:
  Task status: pending -> running -> completed/failed/cancelled/timed_out
  Task stage: file_detecting -> text_extracting -> completed
              field_extracting -> completed

On failure the status becomes ``failed`` and stage records the failed step.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import async_session_factory
from app.extraction.base import FieldSpec
from app.models.contract import Contract, ContractFile
from app.models.task import ContractTask
from app.services.ocr_service import OCRService
from app.services.task_service import utc_now

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _update_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    status: str,
    progress: int,
    error_message: str | None = None,
) -> None:
    result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        return
    terminal_statuses = {"completed", "failed", "cancelled", "timed_out"}
    if status in terminal_statuses:
        task.status = status
        task.stage = status
    elif status.endswith("_failed"):
        task.status = "failed"
        task.stage = status.removesuffix("_failed")
    else:
        task.status = "running"
        task.stage = status
    task.progress = progress
    if error_message:
        task.error_message = error_message
    now = utc_now()
    if task.status != "pending" and task.started_at is None:
        task.started_at = now
    if task.status in terminal_statuses:
        task.completed_at = now
        task.worker_id = None
        task.leased_at = None
        task.lease_expires_at = None
        task.last_heartbeat_at = None
    task.updated_at = now
    await db.commit()


async def _update_contract(
    db: AsyncSession, contract_id: uuid.UUID, **kwargs,
) -> None:
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if contract:
        for k, v in kwargs.items():
            setattr(contract, k, v)
        contract.updated_at = utc_now()
        await db.commit()


async def _raise_if_cancelled(db: AsyncSession, task_id: uuid.UUID) -> None:
    result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
    task = result.scalar_one_or_none()
    if task and task.cancel_requested_at is not None:
        await _update_task(db, task_id, status="cancelled", progress=task.progress)
        raise RuntimeError("Task cancelled")


def _extract_title(full_text: str) -> str | None:
    for line in full_text.split("\n"):
        line = line.strip()
        if line and len(line) > 3:
            return line[:200]
    return None


async def _load_task_contract_file(
    db: AsyncSession,
    task_id: uuid.UUID,
) -> tuple[ContractTask | None, Contract | None, ContractFile | None]:
    result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        logger.error("Pipeline: task %s not found; aborting", task_id)
        return None, None, None

    result = await db.execute(select(Contract).where(Contract.id == task.contract_id))
    contract = result.scalar_one_or_none()
    if contract is None:
        logger.error("Pipeline: contract %s not found; aborting task %s", task.contract_id, task_id)
        return task, None, None

    file_result = await db.execute(
        select(ContractFile)
        .where(ContractFile.contract_id == task.contract_id)
        .order_by(ContractFile.version.desc())
        .limit(1)
    )
    contract_file = file_result.scalar_one_or_none()
    if contract_file is None:
        logger.error("Pipeline: file not found for contract %s", task.contract_id)
    return task, contract, contract_file


# ---------------------------------------------------------------------------
# Core async pipeline
# ---------------------------------------------------------------------------

async def run_ocr_pipeline(
    task_id: uuid.UUID,
    session_factory: async_sessionmaker | None = None,
) -> dict:
    """Run only file detection and OCR, persisting OCR blocks for later extraction."""
    sf = session_factory if session_factory is not None else async_session_factory
    return await _run_ocr_pipeline_inner(sf, task_id)


async def run_extraction_pipeline(
    task_id: uuid.UUID,
    field_definitions: list[FieldSpec] | None = None,
    session_factory: async_sessionmaker | None = None,
) -> dict:
    """Run field extraction from already-persisted OCR blocks."""
    sf = session_factory if session_factory is not None else async_session_factory
    return await _run_extraction_pipeline_inner(sf, task_id, field_definitions=field_definitions)


async def _run_ocr_pipeline_inner(
    session_factory: async_sessionmaker,
    task_id: uuid.UUID,
) -> dict:
    async with session_factory() as db:
        task, contract, contract_file = await _load_task_contract_file(db, task_id)
        if task is None:
            return {"status": "skipped", "contract_id": None, "reason": "task_not_found"}
        if contract is None or contract_file is None:
            await _update_task(db, task_id, status="file_detecting_failed", progress=5, error_message="Contract file not found")
            return {"status": "failed", "contract_id": str(task.contract_id)}

        contract_id = task.contract_id
        try:
            await _update_task(db, task_id, status="file_detecting", progress=5)
            await _update_contract(db, contract_id, status="processing")
            await _raise_if_cancelled(db, task_id)

            await _update_task(db, task_id, status="text_extracting", progress=15)
            ocr_result = await OCRService.process(db, contract_id, contract_file.file_path, contract_file.file_type)
            await _raise_if_cancelled(db, task_id)

            contract.page_count = len(ocr_result.pages)
            contract.title = _extract_title(ocr_result.full_text)
            now = utc_now()
            contract.ocr_completed_at = now
            contract.status = "ocr_completed"
            contract.updated_at = now
            await db.commit()

            await _update_task(db, task_id, status="completed", progress=100)
            return {"status": "completed", "contract_id": str(contract_id)}

        except Exception as exc:
            logger.error("OCR pipeline failed for task %s: %s", task_id, exc, exc_info=True)
            current_status = task.stage or task.status
            current_progress = task.progress
            await db.rollback()
            step = current_status if current_status != "pending" else "unknown"
            try:
                failed_status = "cancelled" if str(exc) == "Task cancelled" else f"{step}_failed"
                await _update_task(
                    db, task_id, status=failed_status, progress=current_progress,
                    error_message=str(exc),
                )
            except Exception:
                logger.error("Failed to mark OCR task %s failed", task_id, exc_info=True)
            if str(exc) != "Task cancelled":
                try:
                    await _update_contract(db, contract_id, status="failed")
                except Exception:
                    logger.error("Failed to mark contract %s as failed", contract_id, exc_info=True)
            raise


async def _run_extraction_pipeline_inner(
    session_factory: async_sessionmaker,
    task_id: uuid.UUID,
    field_definitions: list[FieldSpec] | None = None,
) -> dict:
    async with session_factory() as db:
        task, contract, contract_file = await _load_task_contract_file(db, task_id)
        if task is None:
            return {"status": "skipped", "contract_id": None, "reason": "task_not_found"}
        if contract is None or contract_file is None:
            await _update_task(db, task_id, status="field_extracting_failed", progress=10, error_message="Contract file not found")
            return {"status": "failed", "contract_id": str(task.contract_id)}

        contract_id = task.contract_id
        try:
            await _update_contract(db, contract_id, status="processing")
            await _update_task(db, task_id, status="field_extracting", progress=60)
            await _raise_if_cancelled(db, task_id)

            ocr_result = await OCRService.load_result(db, contract_id)
            if ocr_result is None or not ocr_result.full_text.strip():
                raise ValueError("OCR result is not ready")

            from app.services.extraction_service import extract_and_save
            extraction = await extract_and_save(
                db,
                contract_id,
                ocr_result.full_text,
                field_definitions=field_definitions,
            )
            await _raise_if_cancelled(db, task_id)
            if extraction.contract_type:
                contract.contract_type = extraction.contract_type
                contract.contract_type_confidence = extraction.contract_type_confidence

            now = utc_now()
            contract.extraction_completed_at = now
            contract.status = "reviewing"
            contract.updated_at = now
            await db.commit()

            await _update_task(db, task_id, status="completed", progress=100)
            return {"status": "completed", "contract_id": str(contract_id)}

        except Exception as exc:
            logger.error("Extraction pipeline failed for task %s: %s", task_id, exc, exc_info=True)
            current_status = task.stage or task.status
            current_progress = task.progress
            await db.rollback()
            step = current_status if current_status != "pending" else "unknown"
            try:
                failed_status = "cancelled" if str(exc) == "Task cancelled" else f"{step}_failed"
                await _update_task(
                    db, task_id, status=failed_status, progress=current_progress,
                    error_message=str(exc),
                )
            except Exception:
                logger.error("Failed to mark extraction task %s failed", task_id, exc_info=True)
            if str(exc) != "Task cancelled":
                try:
                    await _update_contract(db, contract_id, status="failed")
                except Exception:
                    logger.error("Failed to mark contract %s as failed", contract_id, exc_info=True)
            raise
