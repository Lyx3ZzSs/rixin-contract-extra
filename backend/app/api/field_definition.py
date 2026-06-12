"""Field definition CRUD API."""

from __future__ import annotations

import uuid as _uuid

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pydantic import field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.field_definition import FieldDefinition

router = APIRouter(prefix="/field-definitions", tags=["field-definitions"])


# --- Schemas ---

class FieldDefinitionCreate(BaseModel):
    field_key: str
    field_name: str
    field_category: str = "party"
    description: str = ""
    value_type: str = "string"
    required: bool = False
    sort_order: int = 0


class FieldDefinitionUpdate(BaseModel):
    field_name: str | None = None
    field_category: str | None = None
    description: str | None = None
    value_type: str | None = None
    required: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class FieldDefinitionOut(BaseModel):
    id: str

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_uuid_to_str(cls, v: object) -> str:
        return str(v)

    field_key: str
    field_name: str
    field_category: str
    description: str
    value_type: str
    required: bool
    sort_order: int
    is_active: bool

    model_config = {"from_attributes": True}


# --- Endpoints ---

@router.get("", response_model=list[FieldDefinitionOut])
async def list_field_definitions(
    db: AsyncSession = Depends(get_db),
):
    """Return all active field definitions ordered by sort_order."""
    result = await db.execute(
        select(FieldDefinition)
        .where(FieldDefinition.is_active == True)
        .order_by(FieldDefinition.sort_order, FieldDefinition.field_key)
    )
    return result.scalars().all()


@router.post("", response_model=FieldDefinitionOut, status_code=201)
async def create_field_definition(
    body: FieldDefinitionCreate,
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(FieldDefinition).where(FieldDefinition.field_key == body.field_key)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"field_key '{body.field_key}' already exists")
    obj = FieldDefinition(**body.model_dump())
    db.add(obj)
    await db.flush()
    return obj


@router.put("/{field_key}", response_model=FieldDefinitionOut)
async def update_field_definition(
    field_key: str,
    body: FieldDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FieldDefinition).where(FieldDefinition.field_key == field_key)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, f"Field '{field_key}' not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.flush()
    return obj


@router.delete("/{field_key}", response_model=dict)
async def delete_field_definition(
    field_key: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FieldDefinition).where(FieldDefinition.field_key == field_key)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, f"Field '{field_key}' not found")
    obj.is_active = False
    await db.flush()
    return {"ok": True}


@router.post("/reset", response_model=list[FieldDefinitionOut])
async def reset_field_definitions(
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete all existing fields and re-seed defaults."""
    from app.main import _DEFAULT_FIELDS
    result = await db.execute(select(FieldDefinition))
    for obj in result.scalars().all():
        obj.is_active = False
    await db.flush()
    for f in _DEFAULT_FIELDS:
        # Check if field_key already exists (inactive)
        existing = await db.execute(
            select(FieldDefinition).where(FieldDefinition.field_key == f["field_key"])
        )
        obj = existing.scalar_one_or_none()
        if obj:
            for k, v in f.items():
                setattr(obj, k, v)
            obj.is_active = True
        else:
            db.add(FieldDefinition(**f))
    await db.flush()
    # Return fresh list
    result2 = await db.execute(
        select(FieldDefinition)
        .where(FieldDefinition.is_active == True)
        .order_by(FieldDefinition.sort_order, FieldDefinition.field_key)
    )
    return result2.scalars().all()
