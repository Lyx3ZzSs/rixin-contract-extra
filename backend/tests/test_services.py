"""Tests for service layer."""

import logging

import pytest
from sqlalchemy import select

from app.extraction.base import ClauseSegment, ExtractedField, ExtractionResult, FieldSpec
from app.models.contract import ContractClause, ExtractedField as ExtractedFieldRecord
from app.models.ocr import OCRBlock
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

    async def fail_classify(_full_text: str):
        raise AssertionError("classify_contract_type should not be called")

    async def fake_extract(_full_text: str, _contract_type: str | None = None, field_definitions=None):
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

    monkeypatch.setattr(LLMService, "classify_contract_type", fail_classify)
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


@pytest.mark.asyncio
async def test_extract_and_save_derives_bbox_from_source_text_ocr_block(monkeypatch):
    from tests.conftest import test_session_factory
    from app.services.contract_service import create_contract
    from app.services.extraction_service import extract_and_save
    from app.services.llm_service import LLMService

    selected_field = FieldSpec(field_key="party-a-name", field_name="甲方名称")

    async def fake_extract(_full_text: str, _contract_type: str | None = None, field_definitions=None):
        return ExtractionResult(
            fields=[
                ExtractedField(
                    field_key="party-a-name",
                    field_name="甲方名称",
                    value="北京日新科技有限公司",
                    source_text="甲方：北京日新科技有限公司",
                    page_no=1,
                    confidence=0.96,
                ),
            ],
        )

    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="derive-field-bbox")
        db.add(OCRBlock(
            contract_id=contract.id,
            page_no=1,
            block_type="text",
            text="甲方：北京日新科技有限公司",
            bbox={"x1": 120, "y1": 140, "x2": 520, "y2": 175},
            confidence=0.98,
            sort_order=1,
            page_width=1240,
            page_height=1754,
        ))
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
        record = result.scalar_one()

    assert record.bbox == {"x1": 120, "y1": 140, "x2": 520, "y2": 175}


@pytest.mark.asyncio
async def test_extract_and_save_does_not_persist_key_clauses(monkeypatch):
    from tests.conftest import test_session_factory
    from app.services.contract_service import create_contract
    from app.services.extraction_service import extract_and_save
    from app.services.llm_service import LLMService

    async def fake_extract(_full_text: str, _contract_type: str | None = None, field_definitions=None):
        return ExtractionResult(
            contract_type="service",
            contract_type_confidence=0.8,
            fields=[],
            key_clauses=[
                ClauseSegment(
                    clause_type="payment",
                    clause_title="付款条款",
                    content="分期付款。",
                    confidence=0.9,
                ),
            ],
        )

    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="no-key-clauses")
        await extract_and_save(db, contract.id, "付款条款：分期付款。")
        await db.commit()

        result = await db.execute(
            select(ContractClause).where(ContractClause.contract_id == contract.id)
        )
        clauses = result.scalars().all()

    assert clauses == []


@pytest.mark.asyncio
async def test_extract_and_save_logs_null_and_missing_fields(monkeypatch, caplog):
    from tests.conftest import test_session_factory
    from app.services.contract_service import create_contract
    from app.services.extraction_service import extract_and_save
    from app.services.llm_service import LLMService

    requested_fields = [
        FieldSpec(field_key="party-a-name", field_name="甲方名称"),
        FieldSpec(field_key="party-b-name", field_name="乙方名称"),
    ]

    async def fake_extract(_full_text: str, _contract_type: str | None = None, field_definitions=None):
        return ExtractionResult(
            contract_type="service",
            contract_type_confidence=0.8,
            fields=[
                ExtractedField(
                    field_key="party-a-name",
                    field_name="甲方名称",
                    value=None,
                    source_text=None,
                    page_no=None,
                    confidence=0.2,
                ),
            ],
        )

    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)
    caplog.set_level(logging.INFO, logger="app.services.extraction_service")

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="diagnostic-null-missing")
        await extract_and_save(
            db,
            contract.id,
            "甲方：北京日新科技有限公司\n乙方：上海恒信信息技术有限公司",
            field_definitions=requested_fields,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("Extraction request diagnostics" in message for message in messages)
    assert any("llm_returned_null" in message and "party-a-name" in message for message in messages)
    assert any("llm_missing_requested_field" in message and "party-b-name" in message for message in messages)


@pytest.mark.asyncio
async def test_extract_and_save_persists_missing_requested_fields_as_null(monkeypatch):
    from tests.conftest import test_session_factory
    from app.services.contract_service import create_contract
    from app.services.extraction_service import extract_and_save
    from app.services.llm_service import LLMService

    requested_fields = [
        FieldSpec(field_key="party-a-name", field_name="甲方名称"),
        FieldSpec(field_key="party-b-name", field_name="乙方名称"),
    ]

    async def fake_extract(_full_text: str, _contract_type: str | None = None, field_definitions=None):
        return ExtractionResult(
            fields=[
                ExtractedField(
                    field_key="party-a-name",
                    field_name="甲方名称",
                    value="江苏东大金智信息系统有限公司",
                    source_text="甲方：江苏东大金智信息系统有限公司",
                    page_no=2,
                    confidence=0.98,
                ),
            ],
        )

    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="persist-missing-as-null")
        await extract_and_save(
            db,
            contract.id,
            "甲方：江苏东大金智信息系统有限公司",
            field_definitions=requested_fields,
        )
        await db.commit()

        result = await db.execute(
            select(ExtractedFieldRecord)
            .where(ExtractedFieldRecord.contract_id == contract.id)
            .order_by(ExtractedFieldRecord.field_key)
        )
        records = result.scalars().all()

    assert [record.field_key for record in records] == ["party-a-name", "party-b-name"]
    assert records[0].value == "江苏东大金智信息系统有限公司"
    assert records[1].value is None
    assert records[1].confidence == 0.0


@pytest.mark.asyncio
async def test_extract_and_save_fails_when_llm_returns_no_requested_fields(monkeypatch):
    from tests.conftest import test_session_factory
    from app.services.contract_service import create_contract
    from app.services.extraction_service import extract_and_save
    from app.services.llm_service import LLMService

    requested_fields = [
        FieldSpec(field_key="party-a-name", field_name="甲方名称"),
        FieldSpec(field_key="party-b-name", field_name="乙方名称"),
    ]

    async def fake_extract(_full_text: str, _contract_type: str | None = None, field_definitions=None):
        return ExtractionResult(fields=[])

    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="fail-empty-requested-fields")
        with pytest.raises(RuntimeError, match="LLM returned no requested fields"):
            await extract_and_save(
                db,
                contract.id,
                "甲方：江苏东大金智信息系统有限公司",
                field_definitions=requested_fields,
            )

        result = await db.execute(
            select(ExtractedFieldRecord).where(ExtractedFieldRecord.contract_id == contract.id)
        )
        records = result.scalars().all()

    assert records == []


@pytest.mark.asyncio
async def test_save_fields_logs_saved_null_and_non_null_keys(caplog):
    from tests.conftest import test_session_factory
    from app.services.contract_service import create_contract
    from app.services.extraction_service import save_fields

    caplog.set_level(logging.INFO, logger="app.services.extraction_service")

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="diagnostic-save-fields")
        await save_fields(
            db,
            contract.id,
            [
                ExtractedField(
                    field_key="party-a-name",
                    field_name="甲方名称",
                    value="北京日新科技有限公司",
                    confidence=0.95,
                ),
                ExtractedField(
                    field_key="party-b-name",
                    field_name="乙方名称",
                    value=None,
                    confidence=0.2,
                ),
            ],
        )

    messages = [record.getMessage() for record in caplog.records]
    save_logs = [message for message in messages if "Field save diagnostics" in message]
    assert save_logs
    assert "party-a-name" in save_logs[-1]
    assert "party-b-name" in save_logs[-1]


def test_extraction_log_truncate_helper_limits_text():
    from app.services.extraction_service import _truncate_for_log

    text = "source" * 100
    truncated = _truncate_for_log(text)

    assert truncated is not None
    assert len(truncated) <= 203
    assert truncated.endswith("...")
