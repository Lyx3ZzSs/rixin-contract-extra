"""Task creation and status management service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import ContractTask


async def create_task(
    db: AsyncSession,
    contract_id: uuid.UUID,
    task_type: str = "full_pipeline",
) -> ContractTask:
    """Create a new task record for a contract."""
    task = ContractTask(
        contract_id=contract_id,
        task_type=task_type,
        status="pending",
        progress=0,
    )
    db.add(task)
    await db.flush()
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
