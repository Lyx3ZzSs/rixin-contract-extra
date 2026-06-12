from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# --- Unified response wrapper ---

class ApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: object = None


# --- Contract ---

class UploadResponse(BaseModel):
    contract_id: uuid.UUID
    file_id: uuid.UUID
    task_id: uuid.UUID
    status: str


class ContractBrief(BaseModel):
    id: uuid.UUID
    title: str | None
    file_name: str = ""
    file_type: str = ""
    contract_type: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FileBrief(BaseModel):
    id: uuid.UUID
    file_name: str
    file_type: str
    file_size: int
    content_type: str | None
    version: int

    model_config = {"from_attributes": True}


class ContractList(BaseModel):
    items: list[ContractBrief]
    total: int
    page: int
    page_size: int


class FieldDetail(BaseModel):
    id: uuid.UUID
    field_name: str
    field_key: str = ""
    field_category: str
    value: str | None
    value_type: str
    source_text: str | None
    page_no: int | None
    bbox: dict | None
    confidence: float | None
    review_status: str
    reviewed_value: str | None
    reviewer_id: str | None
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}

class ClauseDetail(BaseModel):
    id: uuid.UUID
    clause_type: str | None
    clause_title: str | None
    content: str
    page_no: int | None
    page_end: int | None = None
    bbox: dict | None
    confidence: float | None
    review_status: str

    model_config = {"from_attributes": True}


class RiskDetail(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID | None
    clause_id: uuid.UUID | None
    risk_level: str
    risk_type: str
    description: str
    evidence: str | None = None
    suggestion: str | None
    source_text: str | None
    page_no: int | None
    review_status: str
    reviewer_id: str | None
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}


class ContractDetail(BaseModel):
    id: uuid.UUID
    title: str | None
    content_hash: str | None
    contract_type: str | None
    contract_type_confidence: float | None
    status: str
    page_count: int | None
    ocr_completed_at: datetime | None
    extraction_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    files: list[FileBrief] = []
    fields: list[FieldDetail] = []
    clauses: list[ClauseDetail] = []
    risks: list[RiskDetail] = []

    model_config = {"from_attributes": True}
