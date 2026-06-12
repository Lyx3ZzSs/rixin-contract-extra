"""LLM service — model invocation abstraction with JSON Schema enforcement.

Responsibilities:
1. Call the configured LLM provider (mock / OpenAI / DeepSeek / Qwen).
2. Validate LLM output against the expected JSON Schema.
3. Parse and normalise results into domain types (ExtractedField, etc.).
4. Handle JSON parse failures gracefully — never raise raw exceptions to
   the pipeline; instead return a structured error result.

This service is the **only** place LLM calls happen.  It never writes to DB.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.extraction.base import (
    BBox,
    ClauseSegment,
    ExtractedField,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


# JSON Schema for LLM output validation
# ---------------------------------------------------------------------------

EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field_key", "field_value"],
                "properties": {
                    "field_key": {"type": "string"},
                    "field_name": {"type": "string"},
                    "field_value": {},
                    "source_text": {"type": "string"},
                    "source_page": {"type": "integer"},
                    "source_bbox": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "review_status": {"type": "string"},
                },
            },
        },
        "contract_type": {"type": "string"},
        "contract_type_confidence": {"type": "number"},
        "key_clauses": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["clause_title", "content"],
                "properties": {
                    "clause_type": {"type": "string"},
                    "clause_title": {"type": "string"},
                    "content": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Raw LLM response model (what the LLM returns)
# ---------------------------------------------------------------------------

class RawExtractedField(BaseModel):
    """Single field in the raw LLM output."""
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
    key_clauses: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

def _extract_json_from_text(text: str) -> str | None:
    """Try to extract a JSON object from LLM output text.

    Handles cases where the LLM wraps JSON in markdown code blocks
    or adds extra prose before/after.
    """
    # Try the full text first
    text = text.strip()
    if text.startswith("{"):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

    # Try extracting from markdown code block
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # Try finding the outermost { ... }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidate = text[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# LLMService — the main service class
# ---------------------------------------------------------------------------

class LLMService:
    """Orchestrates LLM calls with schema validation and error handling.

    This service calls the configured LLM provider (via the provider layer),
    validates the output against the JSON schema, and converts results into
    domain types.  It never writes to the database.
    """

    @classmethod
    async def extract_fields_from_text(
        cls,
        full_text: str,
        contract_type: str | None = None,
        field_definitions: list | None = None,
    ) -> ExtractionResult:
        """Extract fields from contract text using the configured LLM provider.

        This is the primary entry point for field extraction.  It:
        1. Calls the provider to get raw JSON text.
        2. Parses and validates the JSON.
        3. Converts to domain types (ExtractedField, ClauseSegment).

        Returns an ExtractionResult even if parsing fails (with an empty
        fields list and the error logged).
        """
        from app.extraction.llm import get_llm_provider

        provider = get_llm_provider()

        try:
            # Step 1: Get raw result from provider
            # The provider can return either a structured ExtractionResult (mock)
            # or raw JSON text (real LLM providers)
            # Pass field_definitions to providers that support it
            import inspect
            _sig = inspect.signature(provider.extract_fields)
            if field_definitions is not None and 'field_definitions' in _sig.parameters:
                raw_result = await asyncio.to_thread(
                    provider.extract_fields, full_text, contract_type,
                    field_definitions=field_definitions,
                )
            else:
                raw_result = await asyncio.to_thread(
                    provider.extract_fields, full_text, contract_type,
                )

            # If the provider already returns a structured ExtractionResult (mock),
            # just pass it through
            if isinstance(raw_result, ExtractionResult):
                return raw_result

            # Step 2: If provider returns a string, parse as JSON
            if isinstance(raw_result, str):
                _fmap = {f.field_key: f for f in field_definitions} if field_definitions else None
                return cls._parse_raw_json(raw_result, contract_type, field_map_override=_fmap)

            # Unexpected return type
            logger.error("LLM provider returned unexpected type: %s", type(raw_result))
            return ExtractionResult(
                contract_type=contract_type,
                fields=[],
                key_clauses=[],
            )

        except Exception as exc:
            logger.error("LLM extraction failed: %s", exc, exc_info=True)
            return ExtractionResult(
                contract_type=contract_type,
                fields=[],
                key_clauses=[],
            )

    @classmethod
    async def classify_contract_type(cls, full_text: str) -> tuple[str, float]:
        """Classify the contract type using the configured LLM provider."""
        from app.extraction.llm import get_llm_provider

        provider = get_llm_provider()
        try:
            return await asyncio.to_thread(provider.classify_contract_type, full_text)
        except Exception as exc:
            logger.error("Contract type classification failed: %s", exc)
            return ("unknown", 0.0)

    @classmethod
    def _parse_raw_json(cls, raw_text: str, contract_type: str | None, field_map_override: dict | None = None) -> ExtractionResult:
        """Parse raw JSON text from LLM output into ExtractionResult."""
        json_str = _extract_json_from_text(raw_text)
        if json_str is None:
            logger.error("Failed to extract valid JSON from LLM output")
            return ExtractionResult(contract_type=contract_type, fields=[], key_clauses=[])

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error: %s", exc)
            return ExtractionResult(contract_type=contract_type, fields=[], key_clauses=[])

        try:
            raw_result = RawExtractionResult.model_validate(data)
        except ValidationError as exc:
            logger.error("JSON schema validation failed: %s", exc)
            return ExtractionResult(contract_type=contract_type, fields=[], key_clauses=[])

        # Convert raw fields to domain types
        fields = cls._convert_raw_fields(raw_result.fields, field_map_override=field_map_override)
        key_clauses = cls._convert_raw_clauses(raw_result.key_clauses)

        return ExtractionResult(
            contract_type=raw_result.contract_type or contract_type,
            contract_type_confidence=raw_result.contract_type_confidence,
            fields=fields,
            key_clauses=key_clauses,
        )

    @classmethod
    def _convert_raw_fields(cls, raw_fields: list[RawExtractedField], field_map_override: dict | None = None) -> list[ExtractedField]:
        """Convert RawExtractedField list to ExtractedField list."""
        result: list[ExtractedField] = []
        lookup = field_map_override or {}
        for rf in raw_fields:
            # Look up field definition for category
            defn = lookup.get(rf.field_key)
            field_category = defn.field_category if defn else "basic"
            value_type = defn.value_type if defn else "string"

            bbox = None
            if rf.source_bbox and len(rf.source_bbox) == 4:
                bbox = BBox(
                    x1=rf.source_bbox[0], y1=rf.source_bbox[1],
                    x2=rf.source_bbox[2], y2=rf.source_bbox[3],
                )

            result.append(ExtractedField(
                field_key=rf.field_key,
                field_name=rf.field_name or rf.field_key,
                field_category=field_category,
                value=str(rf.field_value) if rf.field_value is not None else None,
                value_type=value_type,
                source_text=rf.source_text,
                page_no=rf.source_page,
                bbox=bbox,
                confidence=rf.confidence,
            ))
        return result

    @classmethod
    def _convert_raw_clauses(cls, raw_clauses: list[dict[str, Any]]) -> list[ClauseSegment]:
        """Convert raw clause dicts to ClauseSegment list."""
        result: list[ClauseSegment] = []
        for rc in raw_clauses:
            result.append(ClauseSegment(
                clause_type=rc.get("clause_type"),
                clause_title=rc.get("clause_title"),
                content=rc.get("content", ""),
                confidence=rc.get("confidence", 0.8),
            ))
        return result
