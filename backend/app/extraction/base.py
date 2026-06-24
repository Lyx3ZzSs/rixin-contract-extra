"""Shared data types for extraction results."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, model_validator


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
    font_size: float | None = None    # font size in points (from OCR provider / pymupdf)


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

    def to_markdown(self) -> str:
        # All blocks emit plain text. We intentionally do NOT bold titles:
        # extractable values (party names, amounts) often live in title-typed
        # blocks, and markdown emphasis risks contaminating value extraction.
        return "\n\n".join(block.text for block in self.blocks)


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

    PAGE_MARKER_TEMPLATE: ClassVar[str] = "<!-- page: {page_no} -->"

    def to_markdown(self) -> str:
        """Render the whole document as markdown with one page marker per page.

        Markers are stable HTML comments so downstream chunking can split on
        them without changing the LLM-visible text semantics.
        """
        parts: list[str] = []
        for page in self.pages:
            parts.append(self.PAGE_MARKER_TEMPLATE.format(page_no=page.page_no))
            parts.append(page.to_markdown())
        return "\n\n".join(parts)


class PageImage(BaseModel):
    """A rasterized page image (Tier 2). Lives in a known pixel space so the
    bbox returned by OCR maps exactly to the page image we persist + serve."""
    page_no: int
    png_bytes: bytes
    width: int | None = None
    height: int | None = None


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
    key_clauses: list[ClauseSegment] = []


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

    @model_validator(mode="before")
    @classmethod
    def _accept_value_alias(cls, data):
        if isinstance(data, dict) and "field_value" not in data and "value" in data:
            data = dict(data)
            data["field_value"] = data.get("value")
        return data


class RawExtractionResult(BaseModel):
    """Top-level structure of what the LLM returns."""
    fields: list[RawExtractedField]
    contract_type: str | None = None
    contract_type_confidence: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _accept_top_level_field_map(cls, data):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        fields = normalized.get("fields")
        extracted_fields = list(fields) if isinstance(fields, list) else []

        if not extracted_fields:
            reserved_keys = {"fields", "contract_type", "contract_type_confidence"}
            for key, value in data.items():
                if key in reserved_keys or not isinstance(value, dict):
                    continue
                if not any(k in value for k in ("field_key", "field_value", "value", "source_text")):
                    continue
                field_data = dict(value)
                field_data.setdefault("field_key", str(key))
                extracted_fields.append(field_data)

        if extracted_fields or "fields" not in normalized:
            normalized["fields"] = extracted_fields
        return normalized


class RuleViolation(BaseModel):
    rule_name: str
    severity: str  # error / warning
    description: str
    field_name: str | None = None


class ValidationResult(BaseModel):
    passed: bool
    violations: list[RuleViolation]
