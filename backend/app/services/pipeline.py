"""Contract extraction pipeline.

Runs after upload via FastAPI BackgroundTasks (no Celery / Redis required).

Status flow:
  uploaded -> file_detecting -> text_extracting -> ocr_processing ->
  field_extracting -> validating -> review_pending -> completed

On failure the status becomes ``{step}_failed`` (e.g. ``ocr_processing_failed``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import async_session_factory
from app.models.contract import Contract, ContractFile
from app.models.task import ContractTask
from app.services.ocr_service import OCRService

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
    task.status = status
    task.progress = progress
    if error_message:
        task.error_message = error_message
    if status != "pending" and task.started_at is None:
        task.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if status == "completed" or status.endswith("_failed"):
        task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()


async def _update_contract(
    db: AsyncSession, contract_id: uuid.UUID, **kwargs,
) -> None:
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if contract:
        for k, v in kwargs.items():
            setattr(contract, k, v)
        contract.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()


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

async def run_pipeline(
    task_id: uuid.UUID,
    session_factory: async_sessionmaker | None = None,
) -> dict:
    """Execute the full pipeline for a given task.

    Designed to run as a FastAPI BackgroundTask (awaited inside the app's
    event loop) or to be awaited directly in tests.

    When ``session_factory`` is None, the global engine from ``app.database``
    is reused.  Tests pass their own factory pointing at the test DB.
    """
    sf = session_factory if session_factory is not None else async_session_factory
    return await _run_pipeline_inner(sf, task_id)


async def run_ocr_pipeline(
    task_id: uuid.UUID,
    session_factory: async_sessionmaker | None = None,
) -> dict:
    """Run only file detection and OCR, persisting OCR blocks for later extraction."""
    sf = session_factory if session_factory is not None else async_session_factory
    return await _run_ocr_pipeline_inner(sf, task_id)


async def run_extraction_pipeline(
    task_id: uuid.UUID,
    session_factory: async_sessionmaker | None = None,
) -> dict:
    """Run field extraction from already-persisted OCR blocks."""
    sf = session_factory if session_factory is not None else async_session_factory
    return await _run_extraction_pipeline_inner(sf, task_id)


async def _run_pipeline_inner(
    session_factory: async_sessionmaker,
    task_id: uuid.UUID,
) -> dict:
    async with session_factory() as db:
        # Load task & contract
        result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            # The task row isn't committed yet (or was rolled back). The
            # upload endpoint commits before scheduling us, so reaching
            # here indicates a race or a manually-deleted task. Fail
            # gracefully instead of crashing the background handler.
            logger.error("Pipeline: task %s not found; aborting", task_id)
            return {"status": "skipped", "contract_id": None, "reason": "task_not_found"}
        contract_id = task.contract_id

        try:
            # -- Step 1: file_detecting --
            await _update_task(db, task_id, status="file_detecting", progress=5)

            result = await db.execute(select(Contract).where(Contract.id == contract_id))
            contract = result.scalar_one()

            file_result = await db.execute(
                select(ContractFile)
                .where(ContractFile.contract_id == contract_id)
                .order_by(ContractFile.version.desc())
                .limit(1)
            )
            contract_file = file_result.scalar_one()
            file_path = contract_file.file_path
            file_type = contract_file.file_type

            await _update_contract(db, contract_id, status="processing")

            # -- Step 2: text_extracting --
            await _update_task(db, task_id, status="text_extracting", progress=15)
            ocr_result = await OCRService.process(db, contract_id, file_path, file_type)

            contract.page_count = len(ocr_result.pages)
            contract.title = _extract_title(ocr_result.full_text)

            # -- Step 3: ocr_processing --
            await _update_task(db, task_id, status="ocr_processing", progress=30)
            # OCR blocks already persisted by OCRService; this step is reserved
            # for post-processing (table structure, formula detection, etc.)

            # -- Step 4: field_extracting (classify + extract) --
            await _update_task(db, task_id, status="field_extracting", progress=60)
            from app.services.extraction_service import extract_and_save
            extraction = await extract_and_save(
                db, contract_id, ocr_result.full_text,
            )
            if extraction.contract_type:
                contract.contract_type = extraction.contract_type
                contract.contract_type_confidence = extraction.contract_type_confidence

            # -- Step 5: validating --
            await _update_task(db, task_id, status="validating", progress=80)
            from app.services.rule_validation_service import validate_fields
            validate_fields(extraction.fields)

            # -- Step 6: review_pending --
            await _update_task(db, task_id, status="review_pending", progress=95)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            contract.ocr_completed_at = now
            contract.extraction_completed_at = now
            contract.status = "reviewing"
            contract.updated_at = now

            # -- Commit all domain data in one transaction --
            await db.commit()

            # -- Step 8: completed --
            await _update_task(db, task_id, status="completed", progress=100)

            return {"status": "completed", "contract_id": str(contract_id)}

        except Exception as exc:
            logger.error("Pipeline failed for task %s: %s", task_id, exc, exc_info=True)
            current_status = task.status
            current_progress = task.progress
            await db.rollback()
            # Determine which step failed
            step = current_status if current_status != "pending" else "unknown"
            failed_status = f"{step}_failed"
            try:
                await _update_task(
                    db, task_id, status=failed_status, progress=current_progress,
                    error_message=str(exc),
                )
            except Exception:
                logger.error("Failed to mark task %s as %s", task_id, failed_status, exc_info=True)
            try:
                await _update_contract(db, contract_id, status="failed")
            except Exception:
                logger.error("Failed to mark contract %s as failed", contract_id, exc_info=True)
            raise


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

            await _update_task(db, task_id, status="text_extracting", progress=15)
            ocr_result = await OCRService.process(db, contract_id, contract_file.file_path, contract_file.file_type)

            await _update_task(db, task_id, status="ocr_processing", progress=80)
            contract.page_count = len(ocr_result.pages)
            contract.title = _extract_title(ocr_result.full_text)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            contract.ocr_completed_at = now
            contract.status = "ocr_completed"
            contract.updated_at = now
            await db.commit()

            await _update_task(db, task_id, status="completed", progress=100)
            return {"status": "completed", "contract_id": str(contract_id)}

        except Exception as exc:
            logger.error("OCR pipeline failed for task %s: %s", task_id, exc, exc_info=True)
            current_status = task.status
            current_progress = task.progress
            await db.rollback()
            step = current_status if current_status != "pending" else "unknown"
            try:
                await _update_task(
                    db, task_id, status=f"{step}_failed", progress=current_progress,
                    error_message=str(exc),
                )
            except Exception:
                logger.error("Failed to mark OCR task %s failed", task_id, exc_info=True)
            try:
                await _update_contract(db, contract_id, status="failed")
            except Exception:
                logger.error("Failed to mark contract %s as failed", contract_id, exc_info=True)
            raise


async def _run_extraction_pipeline_inner(
    session_factory: async_sessionmaker,
    task_id: uuid.UUID,
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

            ocr_result = await OCRService.load_result(db, contract_id)
            if ocr_result is None or not ocr_result.full_text.strip():
                raise ValueError("OCR result is not ready")

            from app.services.extraction_service import extract_and_save
            extraction = await extract_and_save(db, contract_id, ocr_result.full_text)
            if extraction.contract_type:
                contract.contract_type = extraction.contract_type
                contract.contract_type_confidence = extraction.contract_type_confidence

            await _update_task(db, task_id, status="validating", progress=80)
            from app.services.rule_validation_service import validate_fields
            validate_fields(extraction.fields)

            await _update_task(db, task_id, status="review_pending", progress=95)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            contract.extraction_completed_at = now
            contract.status = "reviewing"
            contract.updated_at = now
            await db.commit()

            await _update_task(db, task_id, status="completed", progress=100)
            return {"status": "completed", "contract_id": str(contract_id)}

        except Exception as exc:
            logger.error("Extraction pipeline failed for task %s: %s", task_id, exc, exc_info=True)
            current_status = task.status
            current_progress = task.progress
            await db.rollback()
            step = current_status if current_status != "pending" else "unknown"
            try:
                await _update_task(
                    db, task_id, status=f"{step}_failed", progress=current_progress,
                    error_message=str(exc),
                )
            except Exception:
                logger.error("Failed to mark extraction task %s failed", task_id, exc_info=True)
            try:
                await _update_contract(db, contract_id, status="failed")
            except Exception:
                logger.error("Failed to mark contract %s as failed", contract_id, exc_info=True)
            raise
