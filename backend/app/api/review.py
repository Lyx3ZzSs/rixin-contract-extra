"""Review API endpoints — field correction, clause review, audit trail.

Key endpoints:
  POST   /contracts/{id}/review              — correct field values (Phase 9)
  GET    /contracts/{id}/review/records       — list all review records
  GET    /contracts/{id}/fields               — list extracted fields
  PATCH  /contracts/{id}/fields/{fid}/review  — single field review
  GET    /contracts/{id}/clauses              — list clauses
  PATCH  /contracts/{id}/clauses/{cid}/review — single clause review
  POST   /contracts/{id}/review/batch         — batch review
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.contract import ContractClause, ExtractedField
from app.models.review import ReviewRecord
from app.schemas.contract import ClauseDetail, FieldDetail
from app.schemas.review import (
    BatchReviewRequest,
    ContractReviewRequest,
    ContractReviewResponse,
    CorrectedFieldOut,
    ReviewAction,
    ReviewRecordListResponse,
    ReviewRecordOut,
)

router = APIRouter(prefix="/contracts/{contract_id}", tags=["review"])


# ---------------------------------------------------------------------------
# Phase 9: POST /review — correct field values
# ---------------------------------------------------------------------------

@router.post("/review", response_model=ContractReviewResponse)
async def review_contract(
    contract_id: uuid.UUID,
    body: ContractReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Correct extracted field values and update review status.

    Workflow:
    1. For each correction, load the field and save old_value / new_value.
    2. Update field.reviewed_value (never overwrite field.value).
    3. Set field.review_status to 'corrected' or 'reviewed'.
    4. Write a ReviewRecord for each correction.
    5. Return updated field info + audit records.
    """
    # Verify contract exists
    from app.models.contract import Contract
    contract_q = await db.execute(
        select(Contract).where(Contract.id == contract_id)
    )
    contract = contract_q.scalar_one_or_none()
    if not contract:
        raise HTTPException(404, "Contract not found")

    corrected_fields: list[CorrectedFieldOut] = []
    review_records: list[ReviewRecord] = []

    for correction in body.corrections:
        # Load field
        result = await db.execute(
            select(ExtractedField).where(
                ExtractedField.id == correction.field_id,
                ExtractedField.contract_id == contract_id,
            )
        )
        field = result.scalar_one_or_none()
        if not field:
            raise HTTPException(
                404,
                f"Field {correction.field_id} not found in contract {contract_id}",
            )

        # Capture old value before change
        old_value = field.value

        # Set corrected value — never overwrite the original `value`
        field.reviewed_value = correction.corrected_value
        field.review_status = body.action  # "corrected" or "reviewed"
        field.reviewer_id = body.reviewer_id
        field.reviewed_at = datetime.now(timezone.utc)

        # Build audit record
        record = ReviewRecord(
            contract_id=contract_id,
            target_type="field",
            target_id=field.id,
            action="modify",
            old_value=old_value,
            new_value=correction.corrected_value,
            comment=correction.comment,
            reviewer_id=body.reviewer_id,
        )
        db.add(record)
        review_records.append(record)

        corrected_fields.append(CorrectedFieldOut(
            field_id=field.id,
            field_name=field.field_name,
            old_value=old_value,
            corrected_value=correction.corrected_value,
            review_status=field.review_status,
            reviewer_id=field.reviewer_id,
            reviewed_at=field.reviewed_at,
        ))

    # If no corrections but action is approve/reject, log at contract level
    if not body.corrections and body.action in ("approved", "reviewed"):
        record = ReviewRecord(
            contract_id=contract_id,
            target_type="contract",
            target_id=contract_id,
            action=body.action,
            comment=body.comment,
            reviewer_id=body.reviewer_id,
        )
        db.add(record)
        review_records.append(record)

    # If there's a top-level comment without corrections, still log it
    if body.comment and not body.corrections and body.action not in ("approved", "reviewed"):
        record = ReviewRecord(
            contract_id=contract_id,
            target_type="contract",
            target_id=contract_id,
            action=body.action,
            comment=body.comment,
            reviewer_id=body.reviewer_id,
        )
        db.add(record)
        review_records.append(record)

    await db.flush()

    return ContractReviewResponse(
        contract_id=contract_id,
        review_action=body.action,
        corrected_fields=corrected_fields,
        review_records=[ReviewRecordOut.model_validate(r) for r in review_records],
        message=f"已完成复核，修正了 {len(corrected_fields)} 个字段" if corrected_fields else "复核记录已保存",
    )


# ---------------------------------------------------------------------------
# GET /review/records — audit trail
# ---------------------------------------------------------------------------

@router.get("/review/records", response_model=ReviewRecordListResponse)
async def list_review_records(
    contract_id: uuid.UUID,
    target_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List all review records for a contract, with optional type filter."""
    stmt = select(ReviewRecord).where(
        ReviewRecord.contract_id == contract_id,
    )
    if target_type:
        stmt = stmt.where(ReviewRecord.target_type == target_type)

    # Count
    count_stmt = select(sa_func.count()).select_from(ReviewRecord).where(
        ReviewRecord.contract_id == contract_id,
    )
    if target_type:
        count_stmt = count_stmt.where(ReviewRecord.target_type == target_type)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Paginate
    stmt = stmt.order_by(ReviewRecord.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    records = result.scalars().all()

    return ReviewRecordListResponse(
        items=[ReviewRecordOut.model_validate(r) for r in records],
        total=total,
    )


# ---------------------------------------------------------------------------
# Field listing and review
# ---------------------------------------------------------------------------

@router.get("/fields", response_model=list[FieldDetail])
async def list_fields(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ExtractedField).where(ExtractedField.contract_id == contract_id)
    )
    return [FieldDetail.model_validate(f) for f in result.scalars().all()]


@router.patch("/fields/{field_id}/review", response_model=FieldDetail)
async def review_field(
    contract_id: uuid.UUID,
    field_id: uuid.UUID,
    body: ReviewAction,
    reviewer_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ExtractedField).where(
            ExtractedField.id == field_id,
            ExtractedField.contract_id == contract_id,
        )
    )
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(404, "Field not found")

    old_value = field.value

    if body.action == "modify" and body.new_value is not None:
        field.reviewed_value = body.new_value
        field.review_status = "corrected"
    elif body.action == "approve":
        field.review_status = "approved"
    elif body.action == "reject":
        field.review_status = "rejected"
    else:
        field.review_status = body.action

    field.reviewer_id = reviewer_id
    field.reviewed_at = datetime.now(timezone.utc)

    # Log
    record = ReviewRecord(
        contract_id=contract_id,
        target_type="field",
        target_id=field_id,
        action=body.action,
        old_value=old_value,
        new_value=body.new_value,
        comment=body.comment,
        reviewer_id=reviewer_id,
    )
    db.add(record)
    await db.flush()

    return FieldDetail.model_validate(field)


# ---------------------------------------------------------------------------
# Clause listing and review
# ---------------------------------------------------------------------------

@router.get("/clauses", response_model=list[ClauseDetail])
async def list_clauses(
    contract_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ContractClause).where(ContractClause.contract_id == contract_id)
    )
    return [ClauseDetail.model_validate(c) for c in result.scalars().all()]


@router.patch("/clauses/{clause_id}/review", response_model=ClauseDetail)
async def review_clause(
    contract_id: uuid.UUID,
    clause_id: uuid.UUID,
    body: ReviewAction,
    reviewer_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ContractClause).where(
            ContractClause.id == clause_id,
            ContractClause.contract_id == contract_id,
        )
    )
    clause = result.scalar_one_or_none()
    if not clause:
        raise HTTPException(404, "Clause not found")

    clause.review_status = body.action if body.action in ("approved", "rejected") else "reviewed"
    record = ReviewRecord(
        contract_id=contract_id,
        target_type="clause",
        target_id=clause_id,
        action=body.action,
        comment=body.comment,
        reviewer_id=reviewer_id,
    )
    db.add(record)
    await db.flush()

    return ClauseDetail.model_validate(clause)


# ---------------------------------------------------------------------------
# Batch review
# ---------------------------------------------------------------------------

@router.post("/review/batch", response_model=list[ReviewRecordOut])
async def batch_review(
    contract_id: uuid.UUID,
    body: BatchReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    records = []
    for item in body.items:
        record = ReviewRecord(
            contract_id=contract_id,
            target_type=item.target_type,
            target_id=item.target_id,
            action=item.action,
            new_value=item.new_value,
            comment=item.comment,
            reviewer_id=body.reviewer_id,
        )
        db.add(record)
        records.append(record)

        if item.target_type == "field":
            result = await db.execute(
                select(ExtractedField).where(ExtractedField.id == item.target_id)
            )
            target = result.scalar_one_or_none()
            if target:
                if item.action == "modify" and item.new_value:
                    target.reviewed_value = item.new_value
                    target.review_status = "corrected"
                elif item.action == "approve":
                    target.review_status = "approved"
                elif item.action == "reject":
                    target.review_status = "rejected"
                else:
                    target.review_status = item.action
                target.reviewer_id = body.reviewer_id
                target.reviewed_at = datetime.now(timezone.utc)

        elif item.target_type == "clause":
            result = await db.execute(
                select(ContractClause).where(ContractClause.id == item.target_id)
            )
            target = result.scalar_one_or_none()
            if target:
                target.review_status = item.action if item.action in ("approved", "rejected") else "reviewed"

    await db.flush()
    return [ReviewRecordOut.model_validate(r) for r in records]
