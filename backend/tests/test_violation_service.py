"""Tests for violation_service (save/recompute) + type-driven field loading (T2)."""
import uuid

import pytest
from sqlalchemy import select

from app.extraction.base import RuleViolation as RVData, ValidationResult
from app.models.rule_violation import RuleViolation
from app.models.field_definition import FieldDefinition
from app.services import violation_service


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


@pytest.mark.asyncio
async def test_recompute_violations_uses_reviewed_value():
    """recompute_violations honours the EFFECTIVE value (reviewed_value wins
    over value). A field whose raw value would pass but whose reviewed_value
    is whitespace-only must surface a required_fields violation; and the
    inverse (raw empty, reviewed filled) must NOT.
    """
    from tests.conftest import test_session_factory
    from app.models.contract import Contract, ExtractedField as EFRow
    from app.services.contract_service import create_contract

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="recompute-eff-1")
        contract_id = contract.id
        # party-a-name: raw value present (passes), but reviewer set it to
        # whitespace -> effective value empty -> required_fields violation.
        db.add(EFRow(
            contract_id=contract_id, field_key="party-a-name", field_name="甲方",
            value="某公司", reviewed_value="   ", confidence=0.9,
        ))
        # party-b-name: raw empty (would fail), but reviewer filled it ->
        # effective value present -> no violation.
        db.add(EFRow(
            contract_id=contract_id, field_key="party-b-name", field_name="乙方",
            value=None, reviewed_value="乙公司", confidence=0.9,
        ))
        await db.commit()

    async with test_session_factory() as db:
        n = await violation_service.recompute_violations(db, contract_id)
        await db.commit()

    async with test_session_factory() as db:
        rows = (await db.execute(
            select(RuleViolation).where(RuleViolation.contract_id == contract_id)
        )).scalars().all()

    # Exactly one required_fields violation, on party-a-name (the reviewed-away
    # one). party-b-name must NOT appear because reviewed_value rescued it.
    rv = [r for r in rows if r.rule_key == "required_fields"]
    assert len(rv) == 1
    assert rv[0].field_key == "party-a-name"
    assert n >= 1
