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
    value: str | None
    value_type: str
    source_text: str | None
    page_no: int | None
    bbox: dict | None
    confidence: float | None
    source_paragraph_id: int | None = None
    source_block_start: int | None = None
    source_block_end: int | None = None
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
    level: int = 0
    parent_id: uuid.UUID | None = None
    review_status: str

    model_config = {"from_attributes": True}


class RuleViolationDetail(BaseModel):
    id: uuid.UUID
    field_key: str | None
    rule_key: str
    severity: str
    message: str
    status: str
    detail: dict | None
    created_at: datetime
    ignored_at: datetime | None
    ignored_by: str | None

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
    violations: list[RuleViolationDetail] = []

    model_config = {"from_attributes": True}
