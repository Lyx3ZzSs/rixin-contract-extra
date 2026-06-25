"""Field extraction service -- business logic layer.

Orchestrates:
1. Load field definitions from DB.
2. Field extraction using dynamically-generated prompt (via LLMService).
3. Field persistence (save_fields).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import (
    BBox,
    FieldSpec,
    ExtractedField as ExtractedFieldData,
    ExtractionResult,
)
from app.models.contract import ExtractedField
from app.models.field_definition import FieldDefinition
from app.models.ocr import OCRBlock
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)

_SUBJECT_FIELD_KEYS = {"party-a-name", "party-b-name"}
_LOG_SNIPPET_LIMIT = 200


def _truncate_for_log(text: str | None, limit: int = _LOG_SNIPPET_LIMIT) -> str | None:
    if text is None:
        return None
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _field_summary(field: ExtractedFieldData) -> dict[str, object]:
    return {
        "field_key": field.field_key,
        "field_name": field.field_name,
        "has_value": bool(field.value),
        "value": _truncate_for_log(field.value),
        "confidence": field.confidence,
        "page_no": field.page_no,
        "source_text": _truncate_for_log(field.source_text),
    }


def _normalize_match_text(text: str | None) -> str:
    if not text:
        return ""
    return "".join(text.split())


def _bbox_from_stored(value: object) -> BBox | None:
    if value is None:
        return None
    if isinstance(value, list) and len(value) == 4:
        return BBox.from_list(value)
    try:
        return BBox.model_validate(value)
    except Exception:
        logger.warning("Invalid OCR bbox payload ignored: %r", value)
        return None


async def _attach_source_bboxes(
    db: AsyncSession,
    contract_id: uuid.UUID,
    fields: list[ExtractedFieldData],
) -> None:
    """Backfill field bbox from persisted OCR blocks using source_text/page_no.

    LLM extraction operates on text, so it can reliably return source_text and
    page_no but usually cannot know OCR pixel coordinates. The trace highlight
    should therefore reuse OCR block coordinates instead of asking the LLM to
    invent a bbox.
    """
    unresolved = [
        field for field in fields
        if field.bbox is None and field.source_text and field.value is not None
    ]
    if not unresolved:
        return

    page_numbers = {field.page_no for field in unresolved if field.page_no is not None}
    has_page_unknown = any(field.page_no is None for field in unresolved)
    stmt = (
        select(OCRBlock)
        .where(OCRBlock.contract_id == contract_id)
        .where(OCRBlock.bbox.is_not(None))
        .order_by(OCRBlock.page_no, OCRBlock.sort_order, OCRBlock.id)
    )
    if page_numbers and not has_page_unknown:
        stmt = stmt.where(OCRBlock.page_no.in_(page_numbers))

    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return

    for field in unresolved:
        source = _normalize_match_text(field.source_text)
        if not source:
            continue
        candidates = rows
        if field.page_no is not None:
            candidates = [row for row in rows if row.page_no == field.page_no]
        for row in candidates:
            block_text = _normalize_match_text(row.text)
            if not block_text:
                continue
            if source in block_text or block_text in source:
                bbox = _bbox_from_stored(row.bbox)
                if bbox is None:
                    continue
                field.bbox = bbox
                if field.page_no is None:
                    field.page_no = row.page_no
                break


# ---------------------------------------------------------------------------
# Load field definitions from DB
# ---------------------------------------------------------------------------

async def load_field_definitions(
    db: AsyncSession, contract_type: str | None = None,
) -> list[FieldDefinition]:
    """Load active field definitions, optionally filtered by contract type.

    With contract_type: returns 通用 (NULL) + that type's专属 fields.
    Without: returns all active fields (legacy behaviour).
    """
    stmt = select(FieldDefinition).where(FieldDefinition.is_active == True)
    if contract_type:
        stmt = stmt.where(
            or_(
                FieldDefinition.contract_type.is_(None),
                FieldDefinition.contract_type == contract_type,
            )
        )
    stmt = stmt.order_by(FieldDefinition.sort_order, FieldDefinition.field_key)
    result = await db.execute(stmt)
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
    field_definitions: list[FieldSpec] | None = None,
) -> ExtractionResult:
    """Run the full extraction pipeline for a contract.

    1. Load field definitions from DB.
    2. Extract fields via LLM (using dynamic prompt).
    3. Persist fields to DB.
    """
    # Step 1: Use request-scoped field definitions when provided; otherwise
    # fall back to all active DB fields for legacy/full-pipeline calls.
    if field_definitions is None:
        db_fields = await load_field_definitions(db)
        field_specs: list[FieldSpec] = [
            FieldSpec(
                field_key=f.field_key, field_name=f.field_name,
                description=f.description, value_type=f.value_type,
            )
            for f in db_fields
        ]
    else:
        field_specs = field_definitions

    # Build lookup for save_fields.
    field_map: dict[str, FieldSpec] = {fs.field_key: fs for fs in field_specs}
    requested_keys = [fs.field_key for fs in field_specs]

    logger.info(
        "Extraction request diagnostics: contract_id=%s text_length=%d requested_fields=%s",
        contract_id,
        len(full_text),
        [{"field_key": fs.field_key, "field_name": fs.field_name} for fs in field_specs],
    )

    result = await LLMService.extract_fields_from_text(
        full_text, field_definitions=field_specs,
    )

    requested_key_set = set(requested_keys)
    unknown_fields = [
        field.field_key for field in result.fields
        if field.field_key and requested_key_set and field.field_key not in requested_key_set
    ]
    if unknown_fields:
        logger.warning(
            "Extraction response diagnostics: llm_returned_unrequested_field contract_id=%s unknown_keys=%s",
            contract_id,
            unknown_fields,
        )

    normalized_fields = [
        field for field in result.fields
        if field.field_key and (not requested_key_set or field.field_key in requested_key_set)
    ]
    returned_by_key = {field.field_key: field for field in normalized_fields if field.field_key}
    returned_keys = list(returned_by_key.keys())
    missing_requested_keys = [key for key in requested_keys if key not in returned_by_key]

    if requested_keys and not returned_by_key:
        raise RuntimeError(f"LLM returned no requested fields: {requested_keys}")

    for key in missing_requested_keys:
        definition = field_map[key]
        missing_field = ExtractedFieldData(
            field_key=definition.field_key,
            field_name=definition.field_name,
            value=None,
            value_type=definition.value_type,
            source_text=None,
            page_no=None,
            confidence=0.0,
        )
        normalized_fields.append(missing_field)
        returned_by_key[key] = missing_field

    result.fields = normalized_fields
    null_value_keys = [field.field_key for field in result.fields if field.field_key and not field.value]
    non_null_keys = [field.field_key for field in result.fields if field.field_key and field.value]
    subject_fields = {
        key: _field_summary(field)
        for key, field in returned_by_key.items()
        if key in _SUBJECT_FIELD_KEYS
    }

    logger.info(
        "Extraction response diagnostics: contract_id=%s returned_keys=%s non_null_keys=%s null_value_keys=%s subject_fields=%s",
        contract_id,
        returned_keys,
        non_null_keys,
        null_value_keys,
        subject_fields,
    )
    if missing_requested_keys:
        logger.warning(
            "Extraction response diagnostics: llm_missing_requested_field contract_id=%s missing_keys=%s",
            contract_id,
            missing_requested_keys,
        )
    if null_value_keys:
        logger.warning(
            "Extraction response diagnostics: llm_returned_null contract_id=%s null_value_keys=%s",
            contract_id,
            null_value_keys,
        )

    if result.fields:
        await _attach_source_bboxes(db, contract_id, result.fields)
        await save_fields(db, contract_id, result.fields, field_map)

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

    field_keys = [f.field_key for f in fields if f.field_key]
    if field_keys:
        await db.execute(
            delete(ExtractedField)
            .where(ExtractedField.contract_id == contract_id)
            .where(ExtractedField.field_key.in_(field_keys))
        )

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
    saved_non_null_keys = [record.field_key for record in records if record.value]
    saved_null_keys = [record.field_key for record in records if not record.value]
    logger.info(
        "Field save diagnostics: contract_id=%s saved_count=%d saved_non_null_keys=%s saved_null_keys=%s",
        contract_id,
        len(records),
        saved_non_null_keys,
        saved_null_keys,
    )
    return records
