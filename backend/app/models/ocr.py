"""OCRBlock — stores OCR recognition results per page / region."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType


class OCRBlock(Base):
    __tablename__ = "ocr_blocks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True,
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="text",
        comment="text / title / table / figure / list",
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    bbox: Mapped[dict | None] = mapped_column(JSONType)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paragraph_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    font_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_width: Mapped[int | None] = mapped_column(Integer)
    page_height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )

    contract: Mapped["Contract"] = relationship(back_populates="ocr_blocks")

    __table_args__ = (
        Index("ix_ocr_contract_page", "contract_id", "page_no"),
        Index("ix_ocr_contract_page_order", "contract_id", "page_no", "sort_order"),
    )
