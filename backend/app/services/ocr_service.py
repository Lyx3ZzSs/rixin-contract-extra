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

from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import OCRDetailedResult, OCRTextBlock
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

        # 2. Persist blocks
        await cls.save_blocks(db, contract_id, result)

        return result

    @classmethod
    async def save_blocks(
        cls,
        db: AsyncSession,
        contract_id: uuid.UUID,
        result: OCRDetailedResult,
    ) -> list[OCRBlock]:
        """Write every OCRTextBlock to the ``ocr_blocks`` table."""
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
