from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class TaskCreate(BaseModel):
    contract_id: uuid.UUID
    task_type: str  # ocr / extraction / rule_validation / risk_identification


class TaskDetail(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    task_type: str
    status: str
    progress: int
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskList(BaseModel):
    items: list[TaskDetail]
