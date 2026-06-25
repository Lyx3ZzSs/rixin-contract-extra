"""Tests for extraction-pipeline wiring (classify/rule, no clause split by default)."""
import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_extraction_pipeline_writes_violations_without_default_clause_split(monkeypatch):
    """End-to-end: extraction pipeline runs classify (authoritative type) +
    field extraction + rule validation, without adding clause-management output."""
    from tests.conftest import test_session_factory
    from app.services import pipeline, task_service
    from app.services.contract_service import create_contract
    from app.services.llm_service import LLMService
    from app.services.ocr_service import OCRService
    from app.models.contract import Contract, ContractFile, ContractClause
    from app.models.rule_violation import RuleViolation
    from app.extraction.base import (
        OCRDetailedResult, OCRPageResult, OCRTextBlock,
        ExtractedField, ExtractionResult,
    )

    # Canned OCR result -- monkeypatch load_result so no OCR blocks need seeding.
    fake_ocr = OCRDetailedResult(pages=[OCRPageResult(page_no=1, blocks=[
        OCRTextBlock(block_type="title", text="合同", sort_order=1),
        OCRTextBlock(block_type="text", text="第一条 付款方式 分期付款。", sort_order=2),
    ])])
    monkeypatch.setattr(
        OCRService, "load_result",
        classmethod(lambda cls, db, contract_id, provider="stored": _async_return(fake_ocr)),
    )

    async def fake_classify(_full_text):
        return ("service", 0.9)
    monkeypatch.setattr(LLMService, "classify_contract_type", fake_classify)

    async def fake_extract(_full_text, field_definitions=None):
        # Inline contract_type intentionally DIFFERS from classify's ("service"):
        # the final contract_type must be "service" (classify authoritative),
        # NOT this inline "lease" — this is what the assertion below guards.
        # party-a-name empty -> triggers required_fields violation (Track A)
        return ExtractionResult(contract_type="lease", fields=[
            ExtractedField(field_key="party-a-name", field_name="甲方", value=None, confidence=0.9),
            ExtractedField(field_key="party-b-name", field_name="乙方", value="某公司", confidence=0.9),
        ])
    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="pipe-wiring-t3")
        db.add(ContractFile(
            contract_id=contract.id, file_name="c.pdf", file_path="/tmp/c.pdf",
            file_type="pdf", file_size=10, content_type="application/pdf", version=1,
        ))
        task = await task_service.create_task(db, contract.id, task_type="extraction")
        await db.commit()
        contract_id = contract.id
        task_id = task.id

    await pipeline.run_extraction_pipeline(task_id, session_factory=test_session_factory)

    async with test_session_factory() as db:
        clauses = (await db.execute(select(ContractClause).where(ContractClause.contract_id == contract_id))).scalars().all()
        violations = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == contract_id))).scalars().all()
        c = (await db.execute(select(Contract).where(Contract.id == contract_id))).scalar_one()

    assert clauses == []                                      # clause split is not part of extraction UX
    assert any(v.rule_key == "required_fields" for v in violations)  # Track A rule ran
    assert c.contract_type == "service"                       # classify authoritative


async def _async_return(value):
    return value
