from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class TaskCreate(BaseModel):
    contract_id: uuid.UUID
    task_type: str  # ocr / extraction / rule_validation


class TaskDetail(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    task_type: str
    status: str
    stage: str | None = None
    progress: int
    error_message: str | None
    error_code: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    priority: int = 0
    task_payload: dict | None = None
    queued_at: datetime | None = None
    started_at: datetime | None
    leased_at: datetime | None = None
    lease_expires_at: datetime | None = None
    worker_id: str | None = None
    last_heartbeat_at: datetime | None = None
    timeout_seconds: int = 1800
    cancel_requested_at: datetime | None = None
    next_run_at: datetime | None = None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskList(BaseModel):
    items: list[TaskDetail]
