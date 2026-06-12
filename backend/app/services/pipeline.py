"""Contract extraction pipeline.

Runs after upload via FastAPI BackgroundTasks (no Celery / Redis required).

Status flow:
  uploaded -> file_detecting -> text_extracting -> ocr_processing ->
  clause_splitting -> field_extracting -> validating ->
  risk_analyzing -> review_pending -> completed

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
    if status == "file_detecting":
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
        custom_fields = task.custom_fields or []

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
            await db.commit()

            # -- Step 3: ocr_processing --
            await _update_task(db, task_id, status="ocr_processing", progress=25)
            # OCR blocks already persisted by OCRService; this step is reserved
            # for post-processing (table structure, formula detection, etc.)

            # -- Step 4: clause_splitting --
            await _update_task(db, task_id, status="clause_splitting", progress=35)
            from app.services.clause_service import split_and_save_clauses
            await split_and_save_clauses(db, contract_id, ocr_result)
            await db.commit()

            # -- Step 5: field_extracting (classify + extract) --
            await _update_task(db, task_id, status="field_extracting", progress=55)
            from app.services.extraction_service import extract_and_save
            extraction = await extract_and_save(
                db, contract_id, ocr_result.full_text,
                custom_fields=custom_fields,
            )
            if extraction.contract_type:
                contract.contract_type = extraction.contract_type
                contract.contract_type_confidence = extraction.contract_type_confidence
                await db.commit()

            # -- Step 6: validating --
            await _update_task(db, task_id, status="validating", progress=70)
            from app.services.rule_validation_service import validate_contract
            from sqlalchemy import select as _sel
            from app.models.contract import ContractClause
            _clauses_q = await db.execute(
                _sel(ContractClause).where(ContractClause.contract_id == contract_id)
            )
            _clause_titles = [
                c.clause_title for c in _clauses_q.scalars().all()
                if c.clause_title
            ]
            validation = validate_contract(
                extraction.fields,
                contract_type=contract.contract_type,
                clause_titles=_clause_titles,
            )

            # -- Step 7: risk_analyzing --
            await _update_task(db, task_id, status="risk_analyzing", progress=85)
            from app.services.risk_service import identify_risks, save_risks
            _clauses_q2 = await db.execute(
                _sel(ContractClause).where(ContractClause.contract_id == contract_id)
            )
            _all_clauses = list(_clauses_q2.scalars().all())
            # Build ClauseSegment list from DB rows for risk analysis
            from app.extraction.base import ClauseSegment as _CS
            _clause_segs = [
                _CS(
                    clause_type=c.clause_type,
                    clause_title=c.clause_title,
                    content=c.content,
                    page_no=c.page_no,
                    confidence=c.confidence or 0.0,
                )
                for c in _all_clauses
            ]
            risks = identify_risks(extraction.fields, validation, clauses=_clause_segs)
            await save_risks(db, contract_id, risks, extraction.fields, clauses=_clause_segs)
            await db.commit()

            # -- Step 8: review_pending --
            await _update_task(db, task_id, status="review_pending", progress=95)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            contract.ocr_completed_at = now
            contract.extraction_completed_at = now
            contract.status = "reviewing"
            contract.updated_at = now
            await db.commit()

            # -- Step 9: completed --
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
