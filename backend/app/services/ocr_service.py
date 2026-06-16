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
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import BBox, OCRDetailedResult, OCRPageResult, OCRTextBlock
from app.extraction.ocr import get_ocr_provider
from app.models.ocr import OCRBlock


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
        """Run OCR and persist the block-level results.

        Returns the ``OCRDetailedResult`` from the provider so callers
        (the pipeline) can continue using it without an extra DB read.
        """
        # 1. Call provider
        provider = get_ocr_provider()
        result = await asyncio.to_thread(provider.extract_detailed, file_path, file_type)
        if not result.full_text.strip():
            raise ValueError("OCR result is empty")

        # 2. Persist blocks
        records = await cls.save_blocks(db, contract_id, result)
        if not records:
            raise ValueError("OCR result contains no text blocks")

        return result

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
