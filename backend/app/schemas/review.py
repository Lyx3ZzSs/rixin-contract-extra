from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReviewAction(BaseModel):
    action: str  # approve / reject / modify
    new_value: str | None = None
    comment: str | None = None


class BatchReviewItem(BaseModel):
    target_type: str  # field / clause
    target_id: uuid.UUID
    action: str  # approve / reject / modify
    new_value: str | None = None
    comment: str | None = None


class BatchReviewRequest(BaseModel):
    items: list[BatchReviewItem]
    reviewer_id: str | None = None


class ContractApproval(BaseModel):
    reviewer_id: str | None = None
    comment: str | None = None


class ReviewRecordOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    action: str
    old_value: str | None
    new_value: str | None
    comment: str | None
    reviewer_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# New: field-level correction via POST /contracts/{id}/review
# ---------------------------------------------------------------------------

class FieldCorrection(BaseModel):
    """Correct a single extracted field value."""
    field_id: uuid.UUID
    corrected_value: str
    comment: str | None = None


class ContractReviewRequest(BaseModel):
    """Review request for a contract — supports multiple field corrections."""
    corrections: list[FieldCorrection] = Field(default_factory=list)
    action: str = "corrected"  # corrected / reviewed / approved
    reviewer_id: str | None = None
    comment: str | None = None


class CorrectedFieldOut(BaseModel):
    """Returned after a field correction."""
    field_id: uuid.UUID
    field_name: str
    old_value: str | None
    corrected_value: str
    review_status: str
    reviewer_id: str | None
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}


class ContractReviewResponse(BaseModel):
    """Response for POST /contracts/{contract_id}/review."""
    contract_id: uuid.UUID
    review_action: str
    corrected_fields: list[CorrectedFieldOut] = []
    review_records: list[ReviewRecordOut] = []
    message: str = "success"


class ReviewRecordListResponse(BaseModel):
    """Response for GET /contracts/{contract_id}/review/records."""
    items: list[ReviewRecordOut]
    total: int
