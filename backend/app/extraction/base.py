"""Shared data types for extraction results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float

    def to_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]

    @classmethod
    def from_list(cls, data: list[float]) -> BBox:
        return cls(x1=data[0], y1=data[1], x2=data[2], y2=data[3])


# ---------------------------------------------------------------------------
# OCR data types — block-level
# ---------------------------------------------------------------------------

class OCRTextBlock(BaseModel):
    """A single recognised text block within a page."""
    block_type: str = "text"          # text / title / table / figure / list
    text: str
    bbox: BBox | None = None
    confidence: float = 0.0
    sort_order: int = 0
    paragraph_id: int | None = None   # groups blocks into logical paragraphs
    font_size: float | None = None    # font size in points (from PPStructure / pymupdf)


class OCRPageResult(BaseModel):
    """One page of OCR output containing ordered blocks."""
    page_no: int
    blocks: list[OCRTextBlock] = []
    width: int | None = None
    height: int | None = None
    confidence: float = 0.0

    @property
    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


class OCRDetailedResult(BaseModel):
    """Full OCR result with per-page block-level detail."""
    pages: list[OCRPageResult]
    provider: str = "unknown"

    @property
    def full_text(self) -> str:
        texts: list[str] = []
        for p in self.pages:
            texts.append(p.full_text)
        return "\n".join(texts)

    @property
    def all_blocks(self) -> list[OCRTextBlock]:
        out: list[OCRTextBlock] = []
        for p in self.pages:
            out.extend(p.blocks)
        return out


class FieldSpec(BaseModel):
    """Unified field specification — single input type for LLM prompt construction.

    Both DB-level FieldDefinition rows and per-task custom fields are converted
    to FieldSpec before entering the prompt builder.  No duck-typing required.
    """
    field_key: str
    field_name: str
    description: str = ""
    value_type: str = "string"


class ClauseSegment(BaseModel):
    clause_type: str | None = None
    clause_title: str | None = None
    content: str
    page_no: int | None = None
    page_end: int | None = None
    start_char: int | None = None
    end_char: int | None = None
    bbox: BBox | None = None
    confidence: float = 0.0
    level: int = 0  # 0=article(条), 1=section(款), 2=sub-clause(项)


class ExtractedField(BaseModel):
    field_key: str
    field_name: str = ""
    value: str | None = None
    value_type: str = "string"
    source_text: str | None = None
    page_no: int | None = None
    bbox: BBox | None = None
    confidence: float = 0.0

class ExtractionResult(BaseModel):
    contract_type: str | None = None
    contract_type_confidence: float = 0.0
    fields: list[ExtractedField]
    key_clauses: list[ClauseSegment]


# ---------------------------------------------------------------------------
# Raw LLM response models — used as Instructor response_model
# ---------------------------------------------------------------------------

class RawExtractedField(BaseModel):
    """Single field in the raw LLM output — mirrors the JSON schema the LLM
    is instructed to follow."""
    field_key: str
    field_name: str | None = None
    field_value: Any = None
    source_text: str | None = None
    source_page: int | None = None
    source_bbox: list[float] | None = None
    confidence: float = 0.8
    review_status: str = "extracted"


class RawExtractionResult(BaseModel):
    """Top-level structure of what the LLM returns."""
    fields: list[RawExtractedField] = []
    contract_type: str | None = None
    contract_type_confidence: float = 0.0
    key_clauses: list[dict] = []


class RuleViolation(BaseModel):
    rule_name: str
    severity: str  # error / warning
    description: str
    field_name: str | None = None


class ValidationResult(BaseModel):
    passed: bool
    violations: list[RuleViolation]
