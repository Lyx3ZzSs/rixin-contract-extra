"""Tests for violation_service (save/recompute) + type-driven field loading (T2)."""
import uuid

import pytest
from sqlalchemy import select

from app.extraction.base import ExtractedField, RuleViolation as RVData, ValidationResult
from app.models.rule_violation import RuleViolation
from app.models.field_definition import FieldDefinition
from app.services import violation_service


def _fields_with_empty_party_a() -> list[ExtractedField]:
    return [
        ExtractedField(field_key="party-a-name", field_name="甲方", value=None, confidence=0.9),
        ExtractedField(field_key="party-b-name", field_name="乙方", value="某公司", confidence=0.9),
    ]


@pytest.mark.asyncio
async def test_save_violations_persists_active_rows():
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    vr = ValidationResult(passed=False, violations=[
        RVData(rule_name="required_fields", severity="error", description="必要字段值为空: 甲方", field_name="party-a-name"),
    ])
    async with test_session_factory() as db:
        n = await violation_service.save_violations(db, cid, vr)
        await db.commit()
    assert n == 1
    async with test_session_factory() as db:
        rows = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == cid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].rule_key == "required_fields"
    assert rows[0].severity == "error"
    assert rows[0].field_key == "party-a-name"
    assert rows[0].status == "active"


@pytest.mark.asyncio
async def test_save_violations_clears_old_active_but_keeps_ignored():
    """Re-save deletes prior 'active' rows but preserves 'ignored' (recompute semantics)."""
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    async with test_session_factory() as db:
        db.add(RuleViolation(contract_id=cid, field_key="x", rule_key="r1", severity="warning", message="old active", status="active"))
        db.add(RuleViolation(contract_id=cid, field_key="y", rule_key="r2", severity="warning", message="ignored stays", status="ignored"))
        await db.commit()

    vr = ValidationResult(passed=True, violations=[])  # recompute finds nothing new
    async with test_session_factory() as db:
        await violation_service.save_violations(db, cid, vr)
        await db.commit()

    async with test_session_factory() as db:
        rows = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == cid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ignored"  # active one cleared, ignored preserved


@pytest.mark.asyncio
async def test_load_field_definitions_filters_by_contract_type():
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        db.add(FieldDefinition(field_key="common-1", field_name="通用1", description="", is_active=True, sort_order=1, contract_type=None))
        db.add(FieldDefinition(field_key="lease-1", field_name="租赁专属", description="", is_active=True, sort_order=2, contract_type="lease"))
        db.add(FieldDefinition(field_key="service-1", field_name="服务专属", description="", is_active=True, sort_order=3, contract_type="service"))
        await db.commit()

    from app.services.extraction_service import load_field_definitions
    async with test_session_factory() as db:
        lease_fields = await load_field_definitions(db, contract_type="lease")
        keys = {f.field_key for f in lease_fields}
    assert keys == {"common-1", "lease-1"}  # 通用 + 该类型专属，不含 service
