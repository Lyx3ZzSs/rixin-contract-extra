"""Task creation and status management service."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ContractTask


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_task(
    db: AsyncSession,
    contract_id: uuid.UUID,
    task_type: str = "ocr",
    *,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
    priority: int = 0,
    timeout_seconds: int = 1800,
) -> ContractTask:
    """Create a queued task record for a contract."""
    now = utc_now()
    task = ContractTask(
        contract_id=contract_id,
        task_type=task_type,
        status="pending",
        stage="queued",
        progress=0,
        attempts=0,
        max_attempts=max_attempts,
        priority=priority,
        task_payload=payload,
        queued_at=now,
        next_run_at=now,
        timeout_seconds=timeout_seconds,
    )
    db.add(task)
    await db.flush()
    return task


async def claim_next_task(
    db: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = 1800,
) -> ContractTask | None:
    """Atomically claim one due task for a worker.

    SQLite does not support SELECT FOR UPDATE SKIP LOCKED, so claiming is a
    two-step select + conditional update. Only the worker whose UPDATE matches
    the original row state gets the task.
    """
    now = utc_now()
    due = or_(ContractTask.next_run_at.is_(None), ContractTask.next_run_at <= now)
    result = await db.execute(
        select(ContractTask)
        .where(ContractTask.status.in_(["pending", "retrying"]))
        .where(due)
        .order_by(ContractTask.priority.desc(), ContractTask.queued_at, ContractTask.created_at)
        .limit(1)
    )
    candidate = result.scalar_one_or_none()
    if candidate is None:
        return None

    lease_expires_at = now + timedelta(seconds=lease_seconds)
    stmt = (
        update(ContractTask)
        .where(ContractTask.id == candidate.id)
        .where(ContractTask.status == candidate.status)
        .values(
            status="running",
            stage="running",
            attempts=ContractTask.attempts + 1,
            worker_id=worker_id,
            leased_at=now,
            lease_expires_at=lease_expires_at,
            last_heartbeat_at=now,
            started_at=candidate.started_at or now,
            updated_at=now,
        )
    )
    result = await db.execute(stmt)
    if result.rowcount != 1:
        await db.rollback()
        return None
    await db.commit()
    return await get_task(db, candidate.id)


async def heartbeat_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    worker_id: str,
    lease_seconds: int = 1800,
) -> None:
    now = utc_now()
    await db.execute(
        update(ContractTask)
        .where(ContractTask.id == task_id)
        .where(ContractTask.worker_id == worker_id)
        .where(ContractTask.status == "running")
        .values(
            last_heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )
    )
    await db.commit()


async def request_cancel_task(db: AsyncSession, task_id: uuid.UUID) -> ContractTask | None:
    task = await get_task(db, task_id)
    if task is None:
        return None
    if task.status in {"completed", "cancelled", "timed_out"}:
        return task
    now = utc_now()
    if task.status in {"pending", "retrying"}:
        task.status = "cancelled"
        task.stage = "cancelled"
        task.progress = task.progress or 0
        task.completed_at = now
    elif task.status == "running":
        task.cancel_requested_at = now
    task.updated_at = now
    await db.flush()
    return task


async def recover_expired_tasks(db: AsyncSession) -> int:
    """Move expired running tasks back to retrying or timed_out."""
    now = utc_now()
    result = await db.execute(
        select(ContractTask)
        .where(ContractTask.status == "running")
        .where(ContractTask.lease_expires_at.is_not(None))
        .where(ContractTask.lease_expires_at < now)
    )
    expired = list(result.scalars().all())
    for task in expired:
        task.worker_id = None
        task.leased_at = None
        task.lease_expires_at = None
        task.last_heartbeat_at = None
        if task.attempts >= task.max_attempts:
            task.status = "timed_out"
            task.stage = "timed_out"
            task.completed_at = now
            task.error_message = task.error_message or "Task lease expired"
        else:
            task.status = "retrying"
            task.stage = "queued"
            task.next_run_at = now + timedelta(seconds=min(60, 2 ** max(task.attempts, 0)))
        task.updated_at = now
    if expired:
        await db.commit()
    return len(expired)


async def mark_task_retry_or_failed(
    db: AsyncSession,
    task_id: uuid.UUID,
    *,
    error_message: str,
    error_code: str | None = None,
) -> ContractTask | None:
    task = await get_task(db, task_id)
    if task is None:
        return None
    if task.status in {"completed", "cancelled", "timed_out"}:
        return task
    now = utc_now()
    task.error_message = error_message
    task.error_code = error_code
    task.worker_id = None
    task.leased_at = None
    task.lease_expires_at = None
    task.last_heartbeat_at = None
    if task.attempts >= task.max_attempts:
        task.status = "failed"
        task.stage = "failed"
        task.completed_at = now
    else:
        task.status = "retrying"
        task.stage = "queued"
        task.next_run_at = now + timedelta(seconds=min(60, 2 ** max(task.attempts, 0)))
    task.updated_at = now
    await db.commit()
    return task


async def get_task(db: AsyncSession, task_id: uuid.UUID) -> ContractTask | None:
    result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
    return result.scalar_one_or_none()


async def list_tasks_by_contract(
    db: AsyncSession, contract_id: uuid.UUID
) -> list[ContractTask]:
    result = await db.execute(
        select(ContractTask)
        .where(ContractTask.contract_id == contract_id)
        .order_by(ContractTask.created_at.desc())
    )
    return list(result.scalars().all())
