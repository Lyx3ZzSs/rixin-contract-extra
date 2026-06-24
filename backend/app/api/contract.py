"""Contract API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.extraction.base import FieldSpec
from app.models.contract import Contract, ContractFile
from app.schemas.contract import (
    ApiResponse,
    ContractDetail,
    ContractList,
    UploadResponse,
)
from app.services import contract_service, file_service, task_service
from app.config import settings
from app.models.ocr import OCRBlock
from app.worker import notify_task_available

# Max upload size in bytes (from settings)
_MAX_FILE_SIZE = settings.max_file_size


router = APIRouter(prefix="/contracts", tags=["contracts"])


class ExtractionStartRequest(BaseModel):
    fields: list[FieldSpec] | None = None


async def _load_contract_detail(db: AsyncSession, contract_id: uuid.UUID) -> Contract:
    """Load contract with all relationships eagerly."""
    stmt = (
        select(Contract)
        .options(
            selectinload(Contract.files),
            selectinload(Contract.fields),
            selectinload(Contract.clauses),
        )
        .where(Contract.id == contract_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


@router.post("/prepare", status_code=201)
async def prepare_contract(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Upload a contract and start OCR-only preprocessing."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    file_data = await file.read()
    if len(file_data) == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(file_data) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件大小超过限制 ({_MAX_FILE_SIZE // (1024*1024)} MB)")

    content_type = file.content_type or "application/octet-stream"

    try:
        file_path, file_type, file_size, content_hash = file_service.save_file(
            file_data, file.filename,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    try:
        contract = await contract_service.create_contract(
            db, content_hash=content_hash,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    contract_file = ContractFile(
        contract_id=contract.id,
        file_name=file.filename,
        file_path=file_path,
        file_type=file_type,
        file_size=file_size,
        content_type=content_type,
    )
    db.add(contract_file)
    await db.flush()

    task = await task_service.create_task(db, contract.id, task_type="ocr")
    await db.commit()
    notify_task_available()

    return ApiResponse(
        code=0,
        message="预处理已开始",
        data=UploadResponse(
            contract_id=contract.id,
            file_id=contract_file.id,
            task_id=task.id,
            status=contract.status,
        ),
    )


@router.post("/{contract_id}/extract", status_code=202)
async def extract_prepared_contract(
    contract_id: uuid.UUID,
    body: ExtractionStartRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Start field extraction from previously persisted OCR results."""
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(404, "Contract not found")

    file_result = await db.execute(
        select(ContractFile)
        .where(ContractFile.contract_id == contract_id)
        .order_by(ContractFile.version.desc())
        .limit(1)
    )
    contract_file = file_result.scalar_one_or_none()
    if not contract_file:
        raise HTTPException(404, "Contract file not found")

    block_result = await db.execute(
        select(OCRBlock.id)
        .where(OCRBlock.contract_id == contract_id)
        .limit(1)
    )
    if contract.ocr_completed_at is None or block_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="OCR结果尚未准备完成")

    field_specs = body.fields if body and body.fields else None
    task_payload = {
        "fields": [field.model_dump(mode="json") for field in field_specs],
    } if field_specs else None
    task = await task_service.create_task(
        db,
        contract.id,
        task_type="extraction",
        payload=task_payload,
    )
    await db.commit()
    notify_task_available()

    return ApiResponse(
        code=0,
        message="提取已开始",
        data=UploadResponse(
            contract_id=contract.id,
            file_id=contract_file.id,
            task_id=task.id,
            status=contract.status,
        ),
    )


@router.get("", response_model=ContractList)
async def list_contracts(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    return await contract_service.list_contracts(db, status, page, page_size)


@router.get("/{contract_id}", response_model=ContractDetail)
async def get_contract_detail(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    contract = await _load_contract_detail(db, contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found")
    return ContractDetail.model_validate(contract)


@router.post("/{contract_id}/approve", response_model=ContractDetail)
async def approve_contract(
    contract_id: uuid.UUID,
    reviewer_id: str | None = None,
    comment: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    contract = await _load_contract_detail(db, contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found")

    for field in contract.fields:
        if field.review_status == "pending":
            field.review_status = "approved"
            field.reviewer_id = reviewer_id
            field.reviewed_at = datetime.now(timezone.utc)

    for clause in contract.clauses:
        if clause.review_status == "pending":
            clause.review_status = "approved"

    contract.status = "approved"
    contract.updated_at = datetime.now(timezone.utc)
    await db.flush()

    from app.models.review import ReviewRecord
    record = ReviewRecord(
        contract_id=contract_id,
        target_type="contract",
        target_id=contract_id,
        action="approve",
        comment=comment,
        reviewer_id=reviewer_id,
    )
    db.add(record)
    await db.flush()

    contract = await _load_contract_detail(db, contract_id)
    return ContractDetail.model_validate(contract)


@router.post("/{contract_id}/reject", response_model=ContractDetail)
async def reject_contract(
    contract_id: uuid.UUID,
    reviewer_id: str | None = None,
    comment: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    contract = await _load_contract_detail(db, contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found")

    contract.status = "rejected"
    contract.updated_at = datetime.now(timezone.utc)
    await db.flush()

    from app.models.review import ReviewRecord
    record = ReviewRecord(
        contract_id=contract_id,
        target_type="contract",
        target_id=contract_id,
        action="reject",
        comment=comment,
        reviewer_id=reviewer_id,
    )
    db.add(record)
    await db.flush()

    contract = await _load_contract_detail(db, contract_id)
    return ContractDetail.model_validate(contract)


# ---------------------------------------------------------------------------
# File download endpoint
# ---------------------------------------------------------------------------

from fastapi.responses import FileResponse as _FileResponse


@router.get("/{contract_id}/files/download")
async def download_contract_file(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Download the latest file for a contract."""
    from sqlalchemy import select as _sel
    result = await db.execute(
        _sel(ContractFile)
        .where(ContractFile.contract_id == contract_id)
        .order_by(ContractFile.version.desc())
        .limit(1)
    )
    contract_file = result.scalar_one_or_none()
    if not contract_file:
        raise HTTPException(404, "Contract file not found")

    file_path = contract_file.file_path
    if not file_path or not Path(file_path).exists():
        raise HTTPException(404, "File not found on disk")

    return _FileResponse(
        path=file_path,
        filename=contract_file.file_name,
        media_type=contract_file.content_type or "application/octet-stream",
    )


@router.get("/{contract_id}/pages/{page_no}/image")
async def get_page_image(
    contract_id: uuid.UUID,
    page_no: int,
    db: AsyncSession = Depends(get_db),
):
    """Serve the rasterized page image (Tier 2 highlight surface)."""
    exists = await db.execute(select(Contract.id).where(Contract.id == contract_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(404, "Contract not found")

    path = file_service.page_image_path(contract_id, page_no)
    if not path.exists():
        raise HTTPException(404, "Page image not found")

    return _FileResponse(
        path=str(path),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )
