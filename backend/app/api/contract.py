"""Contract API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, TypeAdapter
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, Response
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.contract import Contract, ContractFile
from app.schemas.contract import (
    ApiResponse,
    ContractDetail,
    ContractList,
    UploadResponse,
)
from app.services import contract_service, file_service, task_service
from app.extraction.base import FieldSpec
from app.config import settings
from app.services.pipeline import run_pipeline

# Max upload size in bytes (from settings)
_MAX_FILE_SIZE = settings.max_file_size
_ALLOWED_TYPES = {"pdf", "docx", "png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif"}


router = APIRouter(prefix="/contracts", tags=["contracts"])


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


@router.post("/upload", status_code=201)
async def upload_contract(
    background_tasks: BackgroundTasks,
    custom_fields: str = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    # 1. Basic validation
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 2. Read file content
    file_data = await file.read()
    if len(file_data) == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(file_data) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件大小超过限制 ({_MAX_FILE_SIZE // (1024*1024)} MB)")

    content_type = file.content_type or "application/octet-stream"

    # 3. Save file to local disk
    try:
        file_path, file_type, file_size, content_hash = file_service.save_file(
            file_data, file.filename,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    # 4. Create contract record
    try:
        contract = await contract_service.create_contract(
            db, content_hash=content_hash,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 5. Create file record
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

    # 6. Create task record
    if custom_fields:
        try:
            custom_fields_raw = json.loads(custom_fields)
            custom_fields_list = [cf.model_dump() for cf in TypeAdapter(list[FieldSpec]).validate_python(custom_fields_raw)]
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"自定义字段格式错误: {exc}")
    else:
        custom_fields_list = None
    task = await task_service.create_task(db, contract.id, task_type="full_pipeline", custom_fields=custom_fields_list)

    # 7. Commit so the task/contract/file rows are durable BEFORE the
    #    background pipeline reads them. run_pipeline opens an independent
    #    DB connection and cannot see flushed-but-uncommitted rows under
    #    PostgreSQL's READ COMMITTED isolation; a flush() alone is not enough.
    await db.commit()

    # 8. Run extraction pipeline in the background (after the response is sent)
    background_tasks.add_task(run_pipeline, task.id)

    # 9. Return unified response
    return ApiResponse(
        code=0,
        message="上传成功",
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
# Word to PDF preview endpoint
# ---------------------------------------------------------------------------

import subprocess
import tempfile
from pathlib import Path as _Path


@router.post("/preview")
async def preview_contract_file(
    file: UploadFile = File(...),
) -> Response:
    """Convert an uploaded Word file to PDF and return the PDF blob."""
    ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename and "." in file.filename else ""
    if ext not in ("doc", "docx"):
        raise HTTPException(
            status_code=400,
            detail="预览仅支持 Word 文件（.doc/.docx）",
        )

    file_data = await file.read()
    if len(file_data) == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(file_data) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件大小超过限制 ({_MAX_FILE_SIZE // (1024*1024)} MB)")

    with tempfile.TemporaryDirectory() as tmpdir:
        doc_path = _Path(tmpdir) / file.filename
        doc_path.write_bytes(file_data)

        try:
            proc = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to", "pdf",
                    "--outdir", tmpdir,
                    str(doc_path),
                ],
                capture_output=True,
                timeout=60,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="服务器未安装 LibreOffice，无法预览 Word 文件",
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=500,
                detail="Word 转 PDF 超时，请重试",
            )

        if proc.returncode != 0:
            detail = proc.stderr.decode(errors="replace").strip() or proc.stdout.decode(errors="replace").strip()
            raise HTTPException(
                status_code=500,
                detail=f"Word 转 PDF 失败: {detail}",
            )

        pdf_path = _Path(tmpdir) / f"{doc_path.stem}.pdf"
        if not pdf_path.exists():
            candidates = list(_Path(tmpdir).glob("*.pdf"))
            if not candidates:
                raise HTTPException(
                    status_code=500,
                    detail="Word 转 PDF 失败：未生成 PDF 文件",
                )
            pdf_path = candidates[0]

        pdf_bytes = pdf_path.read_bytes()
        return Response(content=pdf_bytes, media_type="application/pdf")


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
    if not file_path or not _Path(file_path).exists():
        raise HTTPException(404, "File not found on disk")

    return _FileResponse(
        path=file_path,
        filename=contract_file.file_name,
        media_type=contract_file.content_type or "application/octet-stream",
    )
