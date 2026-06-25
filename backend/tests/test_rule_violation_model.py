"""Tests for RuleViolation model + field_definitions.contract_type (T1)."""
import uuid

import pytest
from sqlalchemy import select

from app.models.rule_violation import RuleViolation
from app.models.field_definition import FieldDefinition


@pytest.mark.asyncio
async def test_rule_violation_roundtrip():
    """A RuleViolation row can be created and queried with all fields."""
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    async with test_session_factory() as db:
        v = RuleViolation(
            contract_id=cid, field_key="party-a-name", rule_key="required_fields",
            severity="error", message="必要字段值为空: 甲方", status="active",
        )
        db.add(v)
        await db.flush()
        vid = v.id
        await db.commit()

    async with test_session_factory() as db:
        row = (await db.execute(select(RuleViolation).where(RuleViolation.id == vid))).scalar_one()
        assert row.rule_key == "required_fields"
        assert row.severity == "error"
        assert row.status == "active"
        assert row.field_key == "party-a-name"
        assert row.contract_id == cid


@pytest.mark.asyncio
async def test_rule_violation_contract_level_null_field_key():
    """Contract-level violations use field_key=NULL (e.g. payment ratio sum)."""
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    async with test_session_factory() as db:
        db.add(RuleViolation(
            contract_id=cid, field_key=None, rule_key="payment_ratio",
            severity="warning", message="付款比例合计 90%", status="active",
        ))
        await db.commit()
    async with test_session_factory() as db:
        row = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == cid))).scalar_one()
        assert row.field_key is None


@pytest.mark.asyncio
async def test_field_definition_has_contract_type_column():
    """FieldDefinition.contract_type exists and defaults to None (通用)."""
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        fd = FieldDefinition(
            field_key="test-key-t1", field_name="测试", description="",
            value_type="string", required=False, sort_order=0, is_active=True,
        )
        db.add(fd)
        await db.commit()
    async with test_session_factory() as db:
        row = (await db.execute(select(FieldDefinition).where(FieldDefinition.field_key == "test-key-t1"))).scalar_one()
        assert row.contract_type is None
