"""OCR service — orchestration layer.

Responsibilities:
1. Call the configured OCR provider to get block-level results.
2. Persist every ``OCRTextBlock`` as an ``OCRBlock`` row in the DB.
3. Return the ``OCRDetailedResult`` to the caller (pipeline).

Usage::

    from app.services.ocr_service import OCRService

    result = await OCRService.process(db, contract_id, file_path, file_type)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.extraction.base import BBox, OCRDetailedResult, OCRPageResult, OCRTextBlock, PageImage
from app.extraction.ocr import get_ocr_provider
from app.extraction.ocr.rasterize import rasterize_pdf_to_pages
from app.models.ocr import OCRBlock
from app.services import file_service

logger = logging.getLogger(__name__)

_SUBJECT_KEYWORDS = ("甲方名称", "乙方名称", "甲方", "乙方")
_LOG_SNIPPET_LIMIT = 200


def _truncate_for_log(text: str | None, limit: int = _LOG_SNIPPET_LIMIT) -> str | None:
    if text is None:
        return None
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _keyword_contexts(text: str, keywords: tuple[str, ...] = _SUBJECT_KEYWORDS) -> dict[str, str]:
    contexts: dict[str, str] = {}
    for keyword in keywords:
        index = text.find(keyword)
        if index < 0:
            continue
        start = max(0, index - 80)
        end = min(len(text), index + len(keyword) + 120)
        contexts[keyword] = _truncate_for_log(text[start:end]) or ""
    return contexts


class OCRService:
    """Stateless service — all methods are class-methods for convenience."""

    @classmethod
    async def process(
        cls,
        db: AsyncSession,
        contract_id: uuid.UUID,
        file_path: str,
        file_type: str,
    ) -> OCRDetailedResult:
        """Run OCR and persist block-level results + page images.

        Text PDFs are parsed through the provider's PDF-text path. Scanned PDFs
        and image uploads use page-image OCR. We still persist page images for
        preview/highlight traceability.
        """
        provider = get_ocr_provider()

        # 1. Prepare + persist page images (rasterize PDF, or use image as-is).
        #    Offloaded to a thread — rasterization (PyMuPDF get_pixmap per page)
        #    and filesystem writes are blocking and must not stall the event loop.
        page_images = await asyncio.to_thread(cls._prepare_page_images, file_path, file_type)
        if page_images:
            def _persist():
                file_service.delete_contract_pages(contract_id)
                for img in page_images:
                    file_service.save_page_image(contract_id, img.page_no, img.png_bytes)
            await asyncio.to_thread(_persist)

        # 2. OCR — text PDFs keep native text extraction; scanned PDFs/images
        #    use page-image OCR so bbox stays in the saved image pixel space.
        is_text_pdf = cls._is_text_pdf(file_path, file_type)
        if is_text_pdf:
            result = await asyncio.to_thread(provider.extract_detailed, file_path, file_type)
            cls._scale_pdf_text_result_to_page_images(result, page_images)
        elif settings.ocr_rasterize_locally and page_images:
            try:
                result = await asyncio.to_thread(
                    provider.extract_from_images, [img.png_bytes for img in page_images],
                )
            except NotImplementedError:
                result = await asyncio.to_thread(provider.extract_detailed, file_path, file_type)
        else:
            result = await asyncio.to_thread(provider.extract_detailed, file_path, file_type)

        if not result.full_text.strip():
            raise ValueError("OCR result is empty")

        # 3. Persist blocks.
        records = await cls.save_blocks(db, contract_id, result)
        if not records:
            raise ValueError("OCR result contains no text blocks")

        cls._log_ocr_diagnostics(contract_id, result, len(records))
        return result

    @classmethod
    def _prepare_page_images(cls, file_path: str, file_type: str) -> list[PageImage]:
        """Return page images for OCR + display.

        PDFs are rasterized locally (known pixel space); image files are used
        directly as a single page. Returns [] only when rasterization is off
        AND the file is a PDF (legacy path will be used instead).
        """
        if not settings.ocr_rasterize_locally:
            return []
        if file_type.lower() == "pdf":
            return rasterize_pdf_to_pages(file_path, dpi=settings.ppocr_pdf_dpi)
        # Image upload: the file itself is the (single) page image.
        # NOTE: bytes are served as image/png regardless of true encoding — this
        # assumes PNG input. Current contract uploads are PDFs, so this branch is
        # rarely hit in practice.
        return [PageImage(page_no=1, png_bytes=Path(file_path).read_bytes())]

    @classmethod
    def _is_text_pdf(cls, file_path: str, file_type: str) -> bool:
        """Return True when a PDF has enough native text to avoid image OCR."""
        if file_type.lower() != "pdf" and Path(file_path).suffix.lower() != ".pdf":
            return False
        try:
            import fitz

            doc = fitz.open(file_path)
            try:
                page_count = len(doc)
                if page_count == 0:
                    return False
                text_len = 0
                text_page_count = 0
                for page in doc:
                    text = page.get_text("text").strip()
                    if text:
                        text_page_count += 1
                        text_len += len(text)
                text_page_ratio = text_page_count / page_count
                return (
                    text_len >= settings.ppocr_pdf_text_min_chars
                    and text_page_ratio >= settings.ppocr_pdf_text_page_ratio
                )
            finally:
                doc.close()
        except Exception:
            logger.warning("PDF text-layer probe failed; falling back to image OCR", exc_info=True)
            return False

    @classmethod
    def _scale_pdf_text_result_to_page_images(
        cls,
        result: OCRDetailedResult,
        page_images: list[PageImage],
    ) -> None:
        """Scale PDF-point text bboxes into saved page-image pixel space."""
        image_by_page = {img.page_no: img for img in page_images if img.width and img.height}
        for page in result.pages:
            image = image_by_page.get(page.page_no)
            if not image or not page.width or not page.height:
                continue
            scale_x = image.width / page.width
            scale_y = image.height / page.height
            if scale_x <= 0 or scale_y <= 0:
                continue
            for block in page.blocks:
                if not block.bbox:
                    continue
                block.bbox = BBox(
                    x1=block.bbox.x1 * scale_x,
                    y1=block.bbox.y1 * scale_y,
                    x2=block.bbox.x2 * scale_x,
                    y2=block.bbox.y2 * scale_y,
                )
            page.width = image.width
            page.height = image.height

    @classmethod
    def _log_ocr_diagnostics(
        cls,
        contract_id: uuid.UUID,
        result: OCRDetailedResult,
        block_count: int,
    ) -> None:
        full_text = result.full_text
        contexts = _keyword_contexts(full_text)
        payload = {
            "contract_id": str(contract_id),
            "provider": result.provider,
            "page_count": len(result.pages),
            "block_count": block_count,
            "text_length": len(full_text),
            "subject_keyword_hits": sorted(contexts.keys()),
            "subject_keyword_contexts": contexts,
        }
        if contexts:
            logger.info("OCR diagnostics: %s", payload)
        else:
            logger.warning(
                "OCR diagnostics: no subject keywords found; possible OCR text missing or fragmented: %s",
                payload,
            )

    @classmethod
    async def save_blocks(
        cls,
        db: AsyncSession,
        contract_id: uuid.UUID,
        result: OCRDetailedResult,
    ) -> list[OCRBlock]:
        """Write every OCRTextBlock to the ``ocr_blocks`` table."""
        await db.execute(delete(OCRBlock).where(OCRBlock.contract_id == contract_id))
        records: list[OCRBlock] = []
        for page in result.pages:
            for block in page.blocks:
                record = OCRBlock(
                    contract_id=contract_id,
                    page_no=page.page_no,
                    block_type=block.block_type,
                    text=block.text,
                    confidence=block.confidence,
                    bbox=block.bbox.model_dump() if block.bbox else None,
                    sort_order=block.sort_order,
                    paragraph_id=block.paragraph_id,
                    font_size=block.font_size,
                    page_width=page.width,
                    page_height=page.height,
                )
                db.add(record)
                records.append(record)
        await db.flush()
        return records

    @classmethod
    async def load_result(
        cls,
        db: AsyncSession,
        contract_id: uuid.UUID,
        provider: str = "stored",
    ) -> OCRDetailedResult | None:
        """Rebuild an OCRDetailedResult from persisted OCRBlock rows."""
        result = await db.execute(
            select(OCRBlock)
            .where(OCRBlock.contract_id == contract_id)
            .order_by(OCRBlock.page_no, OCRBlock.sort_order, OCRBlock.id)
        )
        rows = list(result.scalars().all())
        if not rows:
            return None

        pages: list[OCRPageResult] = []
        current_page_no: int | None = None
        current_blocks: list[OCRTextBlock] = []
        current_width: int | None = None
        current_height: int | None = None

        def append_current_page() -> None:
            if current_page_no is None:
                return
            pages.append(OCRPageResult(
                page_no=current_page_no,
                blocks=list(current_blocks),
                width=current_width,
                height=current_height,
            ))

        for row in rows:
            if current_page_no is not None and row.page_no != current_page_no:
                append_current_page()
                current_blocks = []
            current_page_no = row.page_no
            current_width = row.page_width
            current_height = row.page_height
            current_blocks.append(OCRTextBlock(
                block_type=row.block_type,
                text=row.text,
                bbox=BBox.model_validate(row.bbox) if row.bbox else None,
                confidence=row.confidence or 0.0,
                sort_order=row.sort_order,
                paragraph_id=row.paragraph_id,
                font_size=row.font_size,
            ))

        append_current_page()
        return OCRDetailedResult(pages=pages, provider=provider)
