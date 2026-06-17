"""Tests for OCR service abstraction layer."""

import logging

import pytest

from app.extraction.base import (
    BBox,
    OCRDetailedResult,
    OCRPageResult,
    OCRTextBlock,
)
from app.services.ocr_service import OCRService
from app.models.ocr import OCRBlock


@pytest.mark.asyncio
async def test_mock_provider_returns_detailed_results():
    """MockOCRProvider.extract_detailed returns block-level data."""
    from app.extraction.ocr.mock import MockOCRProvider

    provider = MockOCRProvider()
    result = provider.extract_detailed("/fake/path.pdf", "pdf")

    assert isinstance(result, OCRDetailedResult)
    assert result.provider == "mock"
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.page_no == 1
    assert page.width == 1024
    assert page.height == 2000
    assert len(page.blocks) > 0


@pytest.mark.asyncio
async def test_mock_provider_blocks_have_required_fields():
    """Every block must have block_type, text, bbox, confidence, sort_order."""
    from app.extraction.ocr.mock import MockOCRProvider

    provider = MockOCRProvider()
    result = provider.extract_detailed("/fake/path.pdf", "pdf")

    for block in result.all_blocks:
        assert block.block_type in ("text", "title", "table", "figure", "list")
        assert isinstance(block.text, str) and len(block.text) > 0
        assert block.bbox is not None
        assert isinstance(block.bbox, BBox)
        assert 0.0 <= block.confidence <= 1.0
        assert isinstance(block.sort_order, int)
        assert block.sort_order > 0


@pytest.mark.asyncio
async def test_mock_provider_blocks_sorted():
    """Blocks should be returned in sort_order."""
    from app.extraction.ocr.mock import MockOCRProvider

    provider = MockOCRProvider()
    result = provider.extract_detailed("/fake/path.pdf", "pdf")

    orders = [b.sort_order for b in result.all_blocks]
    assert orders == sorted(orders)


@pytest.mark.asyncio
async def test_mock_provider_full_text():
    """full_text property should concatenate all block texts."""
    from app.extraction.ocr.mock import MockOCRProvider

    provider = MockOCRProvider()
    result = provider.extract_detailed("/fake/path.pdf", "pdf")

    assert "北京日新科技有限公司" in result.full_text
    assert "上海恒信信息技术有限公司" in result.full_text
    assert "HT-2024-001" in result.full_text


@pytest.mark.asyncio
async def test_ocr_service_persists_blocks(sample_pdf_content, tmp_upload_dir):
    """OCRService.process should persist OCRBlock rows in the DB."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.models.contract import ContractFile
    from sqlalchemy import select

    file_path, file_type, file_size, content_hash = save_file(
        sample_pdf_content, "ocr_test.pdf",
    )

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        cf = ContractFile(
            contract_id=contract.id,
            file_name="ocr_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        )
        db.add(cf)
        await db.flush()

        result = await OCRService.process(db, contract.id, file_path, file_type)
        await db.commit()
        contract_id = contract.id

    assert isinstance(result, OCRDetailedResult)

    async with test_session_factory() as db:
        rows = await db.execute(
            select(OCRBlock)
            .where(OCRBlock.contract_id == contract_id)
            .order_by(OCRBlock.sort_order)
        )
        blocks = rows.scalars().all()

    assert len(blocks) > 0
    # Every block must have sort_order
    for b in blocks:
        assert b.block_type in ("text", "title")
        assert b.text
        assert isinstance(b.sort_order, int)
        assert b.sort_order > 0


@pytest.mark.asyncio
async def test_ocr_service_logs_subject_keyword_diagnostics(sample_pdf_content, tmp_upload_dir, caplog):
    """OCRService.process should log subject keyword hits without full text dumps."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.models.contract import ContractFile

    file_path, file_type, file_size, content_hash = save_file(
        sample_pdf_content, "ocr_log_test.pdf",
    )

    caplog.set_level(logging.INFO, logger="app.services.ocr_service")
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        db.add(ContractFile(
            contract_id=contract.id,
            file_name="ocr_log_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        ))
        await db.flush()

        await OCRService.process(db, contract.id, file_path, file_type)

    messages = [record.getMessage() for record in caplog.records]
    diagnostics = [message for message in messages if "OCR diagnostics" in message]
    assert diagnostics
    assert "subject_keyword_hits" in diagnostics[-1]
    assert "甲方" in diagnostics[-1]
    assert "乙方" in diagnostics[-1]


def test_ocr_log_truncate_helper_limits_text():
    from app.services.ocr_service import _truncate_for_log

    text = "甲方" + "很长" * 150
    truncated = _truncate_for_log(text)

    assert truncated is not None
    assert len(truncated) <= 203
    assert truncated.endswith("...")


@pytest.mark.asyncio
async def test_ocr_service_rejects_empty_results(monkeypatch):
    """OCRService.process should fail when a provider returns no text."""
    from app.services import ocr_service

    class EmptyProvider:
        def extract_detailed(self, _file_path: str, _file_type: str) -> OCRDetailedResult:
            return OCRDetailedResult(
                pages=[OCRPageResult(page_no=1, blocks=[])],
                provider="empty",
            )

    monkeypatch.setattr(ocr_service, "get_ocr_provider", lambda: EmptyProvider())

    with pytest.raises(ValueError, match="OCR result is empty"):
        await OCRService.process(None, "00000000-0000-0000-0000-000000000000", "/tmp/empty.pdf", "pdf")


def test_paddle_provider_is_not_available(monkeypatch):
    from app.config import settings
    from app.extraction.ocr import get_ocr_provider

    monkeypatch.setattr(settings, "ocr_provider", "paddle")

    with pytest.raises(ValueError, match="Unknown OCR provider: paddle"):
        get_ocr_provider()


@pytest.mark.asyncio
async def test_bbox_from_list_roundtrip():
    """BBox.from_list / to_list should round-trip correctly."""
    original = [120.0, 300.0, 900.0, 340.0]
    bbox = BBox.from_list(original)
    assert bbox.to_list() == original
    assert bbox.x1 == 120.0
    assert bbox.y2 == 340.0


@pytest.mark.asyncio
async def test_detailed_result_serialization():
    """OCRDetailedResult should serialize to the expected JSON shape."""
    block = OCRTextBlock(
        block_type="text",
        text="甲方：上海某某科技有限公司",
        bbox=BBox(x1=120, y1=300, x2=900, y2=340),
        confidence=0.98,
        sort_order=1,
    )
    page = OCRPageResult(page_no=1, blocks=[block], width=1024, height=2000)
    result = OCRDetailedResult(pages=[page], provider="mock")

    data = result.model_dump()

    assert data["pages"][0]["page_no"] == 1
    b = data["pages"][0]["blocks"][0]
    assert b["block_type"] == "text"
    assert b["text"] == "甲方：上海某某科技有限公司"
    assert b["bbox"]["x1"] == 120
    assert b["confidence"] == 0.98
    assert b["sort_order"] == 1
