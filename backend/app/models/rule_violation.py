"""RuleViolation — persisted rule-validation findings per contract.

Populated by the extraction pipeline (validate_contract) and refreshed on
field review. status='active' by default; reviewers can set 'ignored'.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType


class RuleViolation(Base):
    __tablename__ = "rule_violations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True,
    )
    field_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    detail: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime)
    ignored_by: Mapped[str | None] = mapped_column(String(100))

    contract: Mapped["Contract"] = relationship(back_populates="violations")

    __table_args__ = (
        Index("ix_rv_contract_rule_field", "contract_id", "rule_key", "field_key"),
    )
