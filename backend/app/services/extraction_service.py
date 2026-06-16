"""Field extraction service -- business logic layer.

Orchestrates:
1. Load field definitions from DB.
2. Contract type classification (via LLMService).
3. Field extraction using dynamically-generated prompt (via LLMService).
4. Field persistence (save_fields).
5. Key clause persistence (delegates to clause_service).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import (
    FieldSpec,
    ExtractedField as ExtractedFieldData,
    ExtractionResult,
)
from app.models.contract import ExtractedField
from app.models.field_definition import FieldDefinition
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Load field definitions from DB
# ---------------------------------------------------------------------------

async def load_field_definitions(db: AsyncSession) -> list[FieldDefinition]:
    """Load all active field definitions from DB, ordered by sort_order."""
    result = await db.execute(
        select(FieldDefinition)
        .where(FieldDefinition.is_active == True)
        .order_by(FieldDefinition.sort_order, FieldDefinition.field_key)
    )
    return list(result.scalars().all())


def build_field_def_map(fields: list[FieldDefinition]) -> dict[str, FieldDefinition]:
    """Build a field_key -> FieldDefinition lookup from DB rows."""
    return {f.field_key: f for f in fields}


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------

async def extract_and_save(
    db: AsyncSession,
    contract_id: uuid.UUID,
    full_text: str,
) -> ExtractionResult:
    """Run the full extraction pipeline for a contract.

    1. Load field definitions from DB.
    2. Classify contract type.
    3. Extract fields via LLM (using dynamic prompt).
    4. Persist fields to DB.
    5. Persist key clauses to DB.
    """
    # Step 1: Load field definitions from DB
    db_fields = await load_field_definitions(db)

    # Convert everything to FieldSpec — single type for prompt builder
    field_specs: list[FieldSpec] = [
        FieldSpec(
            field_key=f.field_key, field_name=f.field_name,
            description=f.description, value_type=f.value_type,
        )
        for f in db_fields
    ]
    # Build lookup for save_fields.
    field_map: dict[str, FieldSpec] = {fs.field_key: fs for fs in field_specs}

    contract_type, type_confidence = await LLMService.classify_contract_type(full_text)

    # Step 3: Extract fields using dynamic prompt
    result = await LLMService.extract_fields_from_text(
        full_text, contract_type, field_definitions=field_specs,
    )

    # Override type if not set in extraction result
    if not result.contract_type:
        result.contract_type = contract_type
    if not result.contract_type_confidence:
        result.contract_type_confidence = type_confidence

    # Step 4: Save fields
    if result.fields:
        await save_fields(db, contract_id, result.fields, field_map)

    # Step 5: Save key clauses
    if result.key_clauses:
        from app.services.clause_service import save_clauses
        await save_clauses(db, contract_id, result.key_clauses)

    return result


# ---------------------------------------------------------------------------
# Field persistence
# ---------------------------------------------------------------------------

async def save_fields(
    db: AsyncSession,
    contract_id: uuid.UUID,
    fields: list[ExtractedFieldData],
    field_map: dict[str, FieldSpec] | None = None,
) -> list[ExtractedField]:
    """Persist extracted fields to DB."""
    if field_map is None:
        field_map = {}

    records: list[ExtractedField] = []
    for f in fields:
        bbox_val = None
        if f.bbox is not None:
            bbox_val = f.bbox.model_dump()

        record = ExtractedField(
            contract_id=contract_id,
            field_key=f.field_key,
            field_name=f.field_name,
            value=f.value,
            value_type=f.value_type,
            source_text=f.source_text,
            page_no=f.page_no,
            bbox=bbox_val,
            confidence=f.confidence,
            review_status="extracted",
        )
        db.add(record)
        records.append(record)
    await db.flush()
    return records
