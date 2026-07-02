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

# --- bbox backfill matching tuning -----------------------------------------
# Scanned PDFs (PP-StructureV3) produce layout blocks that align with the
# paragraphs the LLM sees, so source_text lands in a single block. Text-based
# PDFs (PyMuPDF get_text("dict")) split a clause across several blocks or pack
# several clauses into one, so a pure substring match frequently lands on the
# wrong block and the highlight drifts. We therefore score every candidate
# block by character-bigram overlap and, when no single block clears the bar,
# try merging adjacent blocks (same page, near-contiguous sort_order) into a
# single bbox union.
_TOKEN_THRESHOLD = 0.85   # single-block pass mark (high: a fragment of a long
                           # clause must NOT pass, so it routes to the merge path)
_MERGE_THRESHOLD = 0.70   # merged-window pass mark (source coverage by union)
_TOP_K_FOR_MERGE = 3      # anchors to try window expansion on
_MAX_MERGE_SPAN = 4       # max blocks in one merge window
_SOURCE_MIN_LEN = 6       # skip very short source_text (unreliable to score)
_SORT_ORDER_GAP = 2       # max sort_order jump still considered adjacent


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


def _char_bigrams(text: str) -> frozenset[str]:
    """Character 2-grams over the normalized text.

    Stable for mixed CJK/Latin/digit text without any tokenization dependency.
    For single-character input returns that character alone so coverage math
    still works; for empty input returns an empty set.
    """
    if not text:
        return frozenset()
    if len(text) < 2:
        return frozenset({text})
    return frozenset(text[i:i + 2] for i in range(len(text) - 1))


def _containment(haystack: frozenset[str], needle: frozenset[str]) -> float:
    """Fraction of ``needle``'s bigrams present in ``haystack``."""
    if not needle:
        return 0.0
    return len(needle & haystack) / len(needle)


def _score_block(
    source_grams: frozenset[str], block_grams: frozenset[str],
) -> float:
    """How well a single block covers the source text, in [0, 1].

    The directional coverage ``containment(block ⊇ source)`` (what fraction of
    the source's bigrams live in this block) is the meaningful signal for a
    single-block hit: a block that fully contains the clause scores ~1.0, while
    a block that is merely a *fragment* of the clause scores low (it only
    covers part of the source). The reverse direction (block ⊆ source) is NOT
    rewarded here on purpose — a tiny fragment of a long clause is exactly the
    case we want to route into the merge path, not the single-block path.

    ``jac`` adds exactness so a coincidentally-overlapping unrelated block
    cannot clear the threshold. The 0.7/0.3 blend lets an exact hit clear
    ``_TOKEN_THRESHOLD`` without special-casing, keeping the scanned-PDF fast
    path identical in behaviour to the old substring match.
    """
    if not source_grams or not block_grams:
        return 0.0
    cont = _containment(block_grams, source_grams)
    inter = len(source_grams & block_grams)
    union = len(source_grams | block_grams)
    jac = inter / union if union else 0.0
    return cont * 0.7 + jac * 0.3


def _window_coverage(
    source_grams: frozenset[str], window_grams: frozenset[str],
) -> float:
    """How well a merged window covers the source text, in [0, 1].

    For merged windows we use pure source coverage (no jaccard): the window is
    built from several blocks so its bigram set can legitimately be a superset
    of the source, and we only care that the whole clause is spanned.
    """
    if not source_grams or not window_grams:
        return 0.0
    return _containment(window_grams, source_grams)


def _sort_order_contiguous(rows: list, indices: list[int]) -> bool:
    """True iff every consecutive pair in ``indices`` has a sort_order jump
    no larger than ``_SORT_ORDER_GAP``.

    Guards against merging blocks that are far apart on the page (different
    column / different section), which is the main source of false merges.
    """
    ordered = sorted(indices)
    for a, b in zip(ordered, ordered[1:]):
        if abs(rows[b].sort_order - rows[a].sort_order) > _SORT_ORDER_GAP:
            return False
    return True


def _union_bbox(bboxes: list[BBox | None]) -> BBox | None:
    """Axis-aligned union of bboxes, ignoring None entries."""
    present = [b for b in bboxes if b is not None]
    if not present:
        return None
    return BBox(
        x1=min(b.x1 for b in present),
        y1=min(b.y1 for b in present),
        x2=max(b.x2 for b in present),
        y2=max(b.y2 for b in present),
    )


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

    # Pre-group by page and pre-compute normalized text + bigrams per row so the
    # per-field loop only does set arithmetic, not re-normalization.
    rows_by_page: dict[int, list] = {}
    row_grams: dict[int, frozenset[str]] = {}
    for row in rows:
        rows_by_page.setdefault(row.page_no, []).append(row)
        row_grams[id(row)] = _char_bigrams(_normalize_match_text(row.text))

    for field in unresolved:
        source = _normalize_match_text(field.source_text)
        if len(source) < _SOURCE_MIN_LEN:
            continue
        source_grams = _char_bigrams(source)
        candidates = (
            rows_by_page.get(field.page_no, []) if field.page_no is not None else rows
        )

        # Score every candidate block. Scanned-PDF layout blocks contain the
        # whole clause, so the best score clears _TOKEN_THRESHOLD here and we
        # never enter the merge path.
        scored: list[tuple[float, int]] = []
        best_score = 0.0
        best_idx = -1
        for idx, row in enumerate(candidates):
            score = _score_block(source_grams, row_grams[id(row)])
            if score > best_score:
                best_score = score
                best_idx = idx
            if score > 0.0:
                scored.append((score, idx))

        if best_idx >= 0 and best_score >= _TOKEN_THRESHOLD:
            bbox = _bbox_from_stored(candidates[best_idx].bbox)
            if bbox is not None:
                field.bbox = bbox
                if field.page_no is None:
                    field.page_no = candidates[best_idx].page_no
                continue

        # No single block cleared the bar — text-based PDFs often split a
        # clause across consecutive blocks. Try expanding windows around the
        # top-scoring anchors. Requires a known page (merge needs same-page
        # adjacency).
        if field.page_no is None or not scored:
            continue
        scored.sort(key=lambda pair: pair[0], reverse=True)
        merged = False
        for _, anchor_idx in scored[:_TOP_K_FOR_MERGE]:
            if merged:
                break
            for span in range(2, _MAX_MERGE_SPAN + 1):
                window_idx = list(range(anchor_idx, anchor_idx + span))
                if window_idx[-1] >= len(candidates):
                    break
                if not _sort_order_contiguous(candidates, window_idx):
                    break
                window_text = "".join(
                    _normalize_match_text(candidates[i].text) for i in window_idx
                )
                if _window_coverage(source_grams, _char_bigrams(window_text)) >= _MERGE_THRESHOLD:
                    bbox = _union_bbox(
                        [_bbox_from_stored(candidates[i].bbox) for i in window_idx]
                    )
                    if bbox is not None:
                        field.bbox = bbox
                        merged = True
                        break
        # If still unresolved, field.bbox stays None (front-end shows page-only).


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
