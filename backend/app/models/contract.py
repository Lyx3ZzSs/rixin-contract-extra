"""Contract, ContractFile, ExtractedField, ContractClause, ContractRisk models."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType


# ---------------------------------------------------------------------------
# Contract — the core entity
# ---------------------------------------------------------------------------

class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    contract_type: Mapped[str | None] = mapped_column(String(50))
    contract_type_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploaded", index=True,
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    extraction_completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    # relationships
    files: Mapped[list["ContractFile"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    fields: Mapped[list["ExtractedField"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    clauses: Mapped[list["ContractClause"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    risks: Mapped[list["ContractRisk"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    ocr_blocks: Mapped[list["OCRBlock"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    tasks: Mapped[list["ContractTask"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    review_records: Mapped[list["ReviewRecord"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# ContractFile — file storage metadata (1 contract : N files)
# ---------------------------------------------------------------------------

class ContractFile(Base):
    __tablename__ = "contract_files"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True,
    )
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )

    contract: Mapped["Contract"] = relationship(back_populates="files")

    __table_args__ = (
        Index("ix_cfile_contract_version", "contract_id", "version"),
    )


# ---------------------------------------------------------------------------
# ExtractedField — every field extracted from a contract
# ---------------------------------------------------------------------------

class ExtractedField(Base):
    __tablename__ = "extracted_fields"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True,
    )
    field_key: Mapped[str] = mapped_column(String(100), nullable=False)
    field_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    field_category: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False, default="string")
    source_text: Mapped[str | None] = mapped_column(Text)
    page_no: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(JSONType)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
    )
    reviewed_value: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    contract: Mapped["Contract"] = relationship(back_populates="fields")

    __table_args__ = (
        Index("ix_ef_contract_field", "contract_id", "field_key"),
        Index("ix_ef_contract_review", "contract_id", "review_status"),
    )




# ---------------------------------------------------------------------------
# ContractClause — a single clause segment
# ---------------------------------------------------------------------------

class ContractClause(Base):
    __tablename__ = "contract_clauses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True,
    )
    clause_type: Mapped[str | None] = mapped_column(String(50))
    clause_title: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(JSONType)
    start_char: Mapped[int | None] = mapped_column(Integer)
    end_char: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    contract: Mapped["Contract"] = relationship(back_populates="clauses")

    __table_args__ = (
        Index("ix_cc_contract_type", "contract_id", "clause_type"),
    )


# ---------------------------------------------------------------------------
# ContractRisk — a single identified risk
# ---------------------------------------------------------------------------

class ContractRisk(Base):
    __tablename__ = "contract_risks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True,
    )
    field_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extracted_fields.id"), nullable=True,
    )
    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contract_clauses.id"), nullable=True,
    )
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str | None] = mapped_column(Text)
    page_no: Mapped[int | None] = mapped_column(Integer)
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
    )
    reviewer_id: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    contract: Mapped["Contract"] = relationship(back_populates="risks")

    __table_args__ = (
        Index("ix_cr_contract_level", "contract_id", "risk_level"),
    )
