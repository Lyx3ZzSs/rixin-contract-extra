"""Persist rule-validation findings and refresh them on field review.

save_violations: write a fresh ValidationResult, clearing prior 'active'
rows for the contract while preserving 'ignored' ones (reviewer dismissals).
recompute_violations: rebuild from the contract's current (possibly reviewed)
field values — called after a field correction.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import ExtractedField as EFData, ValidationResult
from app.models.contract import Contract, ExtractedField
from app.models.rule_violation import RuleViolation
from app.services import rule_validation_service

logger = logging.getLogger(__name__)


async def save_violations(
    db: AsyncSession, contract_id: uuid.UUID, result: ValidationResult,
) -> int:
    """Persist validation results: clear prior 'active' rows, insert fresh.

    Preserves 'ignored' rows so reviewer dismissals survive recompute.
    Returns the number of rows inserted.
    """
    await db.execute(
        delete(RuleViolation).where(
            RuleViolation.contract_id == contract_id,
            RuleViolation.status == "active",
        )
    )
    count = 0
    for v in result.violations:
        db.add(RuleViolation(
            contract_id=contract_id,
            rule_key=v.rule_name,
            severity=v.severity,
            message=v.description,
            field_key=v.field_name,
            status="active",
        ))
        count += 1
    await db.flush()
    return count


async def recompute_violations(db: AsyncSession, contract_id: uuid.UUID) -> int:
    """Re-run validation against current field values (reviewed_value wins)
    and persist. Returns the number of active violations written.
    """
    contract_row = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = contract_row.scalar_one_or_none()
    contract_type = contract.contract_type if contract else None

    rows = await db.execute(
        select(ExtractedField).where(ExtractedField.contract_id == contract_id)
    )
    fields = [
        EFData(
            field_key=r.field_key,
            field_name=r.field_name,
            value=(r.reviewed_value or r.value),
            value_type=r.value_type,
            confidence=r.confidence or 0.0,
            source_text=r.source_text,
            page_no=r.page_no,
        )
        for r in rows.scalars()
    ]
    result = rule_validation_service.validate_contract(fields, contract_type=contract_type)
    return await save_violations(db, contract_id, result)
