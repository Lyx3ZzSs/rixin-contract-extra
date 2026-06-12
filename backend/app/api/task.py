"""Task API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contract import Contract
from app.models.task import ContractTask
from app.schemas.task import TaskDetail, TaskList

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskDetail.model_validate(task)


@router.get("/contract/{contract_id}", response_model=TaskList)
async def get_contract_tasks(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    # Verify contract exists
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Contract not found")

    result = await db.execute(
        select(ContractTask)
        .where(ContractTask.contract_id == contract_id)
        .order_by(ContractTask.created_at.desc())
    )
    tasks = result.scalars().all()
    return TaskList(items=[TaskDetail.model_validate(t) for t in tasks])
