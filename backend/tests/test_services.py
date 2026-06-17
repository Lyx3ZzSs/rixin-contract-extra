"""Tests for service layer."""

import pytest
from sqlalchemy import select

from app.extraction.base import ExtractedField, ExtractionResult, FieldSpec
from app.models.contract import ExtractedField as ExtractedFieldRecord
from app.services.clause_service import _classify_clause_type
from app.services.rule_validation_service import validate_fields


def test_classify_clause_type():
    assert _classify_clause_type("第二条 付款方式") == "payment"
    assert _classify_clause_type("第三条 违约责任") == "breach"
    assert _classify_clause_type("第四条 保密条款") == "confidentiality"
    assert _classify_clause_type("第五条 争议解决") == "dispute"
    assert _classify_clause_type("第九条 合同期限") == "term"
    assert _classify_clause_type("第一条 项目概述") == "project"


def test_validate_fields_all_required():
    """Should pass when all required fields are present."""
    fields = [
        ExtractedField(field_key="contract-no", value="HT-001",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="party-a-name", value="A公司",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="party-b-name", value="B公司",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="sign-date", value="2024-01-15",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="contract-amount", value="100000",
                       source_text="test", page_no=1, confidence=0.9),
    ]
    result = validate_fields(fields)
    # No required_field violations
    assert not any(v.rule_name == "required_fields" for v in result.violations)


def test_validate_fields_missing_required():
    """Should fail when required fields are missing."""
    fields = [
        ExtractedField(field_key="party-a-name", value="A公司",
                       source_text="test", page_no=1, confidence=0.9),
    ]
    result = validate_fields(fields)
    assert not result.passed
    missing = [v for v in result.violations if v.rule_name == "required_fields"]
    assert len(missing) >= 1  # At least party-b-name missing


@pytest.mark.asyncio
async def test_extract_and_save_uses_request_scoped_fields(monkeypatch):
    from tests.conftest import test_session_factory
    from app.services.contract_service import create_contract
    from app.services.extraction_service import extract_and_save
    from app.services.llm_service import LLMService

    selected_field = FieldSpec(
        field_key="party-a-name",
        field_name="甲方名称",
        description="合同甲方",
        value_type="string",
    )
    captured: dict = {}

    async def fake_classify(_full_text: str):
        return "service", 0.8

    async def fake_extract(_full_text: str, _contract_type: str | None, field_definitions=None):
        captured["field_definitions"] = field_definitions
        return ExtractionResult(
            contract_type="service",
            contract_type_confidence=0.8,
            fields=[
                ExtractedField(
                    field_key=selected_field.field_key,
                    field_name=selected_field.field_name,
                    value="北京日新科技有限公司",
                    value_type="string",
                    source_text="甲方：北京日新科技有限公司",
                    page_no=1,
                    confidence=0.95,
                ),
            ],
            key_clauses=[],
        )

    monkeypatch.setattr(LLMService, "classify_contract_type", fake_classify)
    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="request-scoped-fields")
        await extract_and_save(
            db,
            contract.id,
            "甲方：北京日新科技有限公司",
            field_definitions=[selected_field],
        )
        await db.commit()

        result = await db.execute(
            select(ExtractedFieldRecord).where(ExtractedFieldRecord.contract_id == contract.id)
        )
        records = result.scalars().all()

    assert [field.field_key for field in captured["field_definitions"]] == ["party-a-name"]
    assert len(records) == 1
    assert records[0].field_key == "party-a-name"
