"""Contract CRUD and orchestration service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract, ContractFile
from app.schemas.contract import ContractBrief, ContractDetail, ContractList


async def create_contract(
    db: AsyncSession,
    content_hash: str,
) -> Contract:
    """Create a contract record."""

    contract = Contract(
        content_hash=content_hash,
        status="uploaded",
    )
    db.add(contract)
    await db.flush()

    return contract


async def get_contract(db: AsyncSession, contract_id: uuid.UUID) -> Contract | None:
    result = await db.execute(
        select(Contract).where(Contract.id == contract_id)
    )
    return result.scalar_one_or_none()


async def list_contracts(
    db: AsyncSession,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ContractList:
    query = select(Contract).order_by(Contract.created_at.desc())
    count_query = select(func.count()).select_from(Contract)

    if status:
        query = query.where(Contract.status == status)
        count_query = count_query.where(Contract.status == status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    result = await db.execute(query)
    contracts = result.scalars().all()

    briefs = []
    for c in contracts:
        file_result = await db.execute(
            select(ContractFile)
            .where(ContractFile.contract_id == c.id)
            .order_by(ContractFile.version.desc())
            .limit(1)
        )
        f = file_result.scalar_one_or_none()
        briefs.append(ContractBrief(
            id=c.id,
            title=c.title,
            file_name=f.file_name if f else "",
            file_type=f.file_type if f else "",
            contract_type=c.contract_type,
            status=c.status,
            created_at=c.created_at,
        ))

    return ContractList(
        items=briefs,
        total=total,
        page=page,
        page_size=page_size,
    )


async def update_contract_status(
    db: AsyncSession, contract_id: uuid.UUID, status: str
) -> None:
    contract = await get_contract(db, contract_id)
    if contract:
        contract.status = status
        contract.updated_at = datetime.now(timezone.utc)
        await db.flush()
