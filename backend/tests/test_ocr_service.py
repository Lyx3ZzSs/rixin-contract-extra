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
async def test_ocr_service_persists_page_images_for_scanned_pdf(sample_pdf_content, tmp_upload_dir, monkeypatch):
    """Scanned PDFs must rasterize and persist page images before image OCR."""
    from tests.conftest import test_session_factory
    from app.services import ocr_service
    from app.services.file_service import save_file, page_image_path
    from app.services.contract_service import create_contract
    from app.models.contract import ContractFile

    monkeypatch.setattr(ocr_service.OCRService, "_is_text_pdf", classmethod(lambda cls, _fp, _ft: False))

    file_path, file_type, _size, content_hash = save_file(sample_pdf_content, "pages.pdf")
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        db.add(ContractFile(contract_id=contract.id, file_name="pages.pdf", file_path=file_path,
                            file_type=file_type, file_size=len(sample_pdf_content),
                            content_type="application/pdf"))
        await db.flush()
        await OCRService.process(db, contract.id, file_path, file_type)
        await db.commit()
        contract_id = contract.id

    # at least page 1 must be persisted as an image
    assert page_image_path(contract_id, 1).exists()


@pytest.mark.asyncio
async def test_ocr_service_uses_text_pdf_path_when_text_layer_is_available(tmp_upload_dir, monkeypatch):
    """Text PDFs should avoid image OCR and not block on preview rasterization."""
    fitz = pytest.importorskip("fitz")
    from tests.conftest import test_session_factory
    from app.services import ocr_service
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.models.contract import ContractFile
    from app.models.ocr import OCRBlock
    from sqlalchemy import select

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "甲方名称：北京日新科技有限公司\n乙方名称：上海恒信信息技术有限公司\n" * 8)
    pdf_bytes = doc.tobytes()
    doc.close()
    file_path, file_type, file_size, content_hash = save_file(pdf_bytes, "text-layer.pdf")
    calls: list[str] = []

    class TextPdfProvider:
        def extract_detailed(self, _file_path, _file_type):
            calls.append("extract_detailed")
            return OCRDetailedResult(pages=[OCRPageResult(page_no=1, width=612, height=792, blocks=[
                OCRTextBlock(
                    block_type="text",
                    text="甲方名称：北京日新科技有限公司\n乙方名称：上海恒信信息技术有限公司",
                    bbox=BBox(x1=72, y1=72, x2=420, y2=110),
                    confidence=1.0,
                    sort_order=1,
                ),
            ])], provider="text-pdf")

        def extract_from_images(self, _page_images):
            calls.append("extract_from_images")
            raise AssertionError("image OCR should not run for text PDFs")

    background_jobs: list[tuple[str, str]] = []

    def fake_start_background_pages(_contract_id, bg_file_path, bg_file_type):
        background_jobs.append((bg_file_path, bg_file_type))

    def fail_sync_prepare(_file_path, _file_type):
        raise AssertionError("text PDF OCR should not synchronously rasterize preview images")

    monkeypatch.setattr(ocr_service, "get_ocr_provider", lambda: TextPdfProvider())
    monkeypatch.setattr(
        ocr_service.OCRService,
        "_start_background_page_image_generation",
        classmethod(lambda cls, contract_id, bg_file_path, bg_file_type: fake_start_background_pages(contract_id, bg_file_path, bg_file_type)),
    )
    monkeypatch.setattr(
        ocr_service.OCRService,
        "_prepare_page_images",
        classmethod(lambda cls, prep_file_path, prep_file_type: fail_sync_prepare(prep_file_path, prep_file_type)),
    )

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        db.add(ContractFile(
            contract_id=contract.id,
            file_name="text-layer.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        ))
        await db.flush()
        await OCRService.process(db, contract.id, file_path, file_type)
        await db.commit()
        contract_id = contract.id

    assert calls == ["extract_detailed"]
    assert background_jobs == [(file_path, file_type)]

    async with test_session_factory() as db:
        block = (await db.execute(select(OCRBlock).where(OCRBlock.contract_id == contract_id))).scalar_one()

    assert block.page_width is not None
    assert block.page_width > 612
    assert block.bbox is not None
    assert block.bbox["x1"] > 72


@pytest.mark.asyncio
async def test_ocr_service_rejects_empty_results(monkeypatch):
    """process() must fail when the provider returns no text (new image path)."""
    from app.services import ocr_service
    from app.extraction.base import OCRDetailedResult, OCRPageResult, PageImage

    class EmptyProvider:
        def extract_from_images(self, _imgs):
            return OCRDetailedResult(pages=[OCRPageResult(page_no=1, blocks=[])], provider="empty")

    monkeypatch.setattr(ocr_service, "get_ocr_provider", lambda: EmptyProvider())
    # skip real rasterization — feed a dummy page image
    monkeypatch.setattr(
        ocr_service.OCRService, "_prepare_page_images",
        classmethod(lambda cls, fp, ft: [PageImage(page_no=1, png_bytes=b"x", width=1, height=1)]),
    )

    with pytest.raises(ValueError, match="OCR result is empty"):
        await ocr_service.OCRService.process(
            None, "00000000-0000-0000-0000-000000000000", "/tmp/empty.pdf", "pdf",
        )


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
