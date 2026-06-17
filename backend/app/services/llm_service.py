"""LLM service — model invocation abstraction.

Responsibilities:
1. Call the configured LLM provider (mock / Qwen / etc.).
2. Providers return structured ExtractionResult — no JSON parsing needed here.
3. Fallback JSON parsing retained for non-Instructor providers that return raw text.

This service is the **only** place LLM calls happen.  It never writes to DB.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from app.extraction.base import (
    BBox,
    ExtractedField,
    ExtractionResult,
    RawExtractedField,
    RawExtractionResult,
)

logger = logging.getLogger(__name__)


# JSON Schema for LLM output validation (fallback / documentation)
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
    },
}


# ---------------------------------------------------------------------------
# JSON extraction helpers (fallback for non-Instructor providers)
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

    This service calls the configured LLM provider (via the provider layer).
    All current providers (Mock, Qwen+Instructor) return ExtractionResult
    directly.  Fallback JSON parsing is retained for providers that return
    raw text (e.g. a hypothetical DeepSeek provider without Instructor).
    """

    @classmethod
    async def extract_fields_from_text(
        cls,
        full_text: str,
        contract_type: str | None = None,
        field_definitions: list | None = None,
    ) -> ExtractionResult:
        """Extract fields from contract text using the configured LLM provider."""
        from app.extraction.llm import get_llm_provider

        provider = get_llm_provider()

        try:
            raw_result = await asyncio.to_thread(
                provider.extract_fields, full_text, contract_type,
                field_definitions=field_definitions,
            )

            # Both Mock and Qwen+Instructor return ExtractionResult directly
            if isinstance(raw_result, ExtractionResult):
                return raw_result

            # Fallback: provider returned raw JSON text
            if isinstance(raw_result, str):
                _fmap = {f.field_key: f for f in field_definitions} if field_definitions else None
                return cls._parse_raw_json(raw_result, contract_type, field_map_override=_fmap)

            # Unexpected return type
            logger.error("LLM provider returned unexpected type: %s", type(raw_result))
            return ExtractionResult(
                contract_type=contract_type,
                fields=[],
            )

        except (OSError, ValueError, RuntimeError) as exc:
            logger.error("LLM extraction failed (re-raising): %s", exc, exc_info=True)
            raise
        except Exception as exc:
            logger.error(
                "LLM extraction failed with unexpected error (re-raising): %s",
                exc, exc_info=True,
            )
            raise

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

    # ------------------------------------------------------------------
    # Fallback JSON parsing (for non-Instructor providers)
    # ------------------------------------------------------------------

    @classmethod
    def _parse_raw_json(
        cls,
        raw_text: str,
        contract_type: str | None,
        field_map_override: dict | None = None,
    ) -> ExtractionResult:
        """Parse raw JSON text from LLM output into ExtractionResult.

        Only used when a provider returns a raw string instead of a
        structured ExtractionResult (e.g. legacy or non-Instructor providers).
        """
        json_str = _extract_json_from_text(raw_text)
        if json_str is None:
            logger.error(
                "Failed to extract valid JSON from LLM output (raw len=%d, preview=%.300r)",
                len(raw_text), raw_text[:300],
            )
            return ExtractionResult(contract_type=contract_type, fields=[])

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error: %s", exc)
            return ExtractionResult(contract_type=contract_type, fields=[])

        try:
            raw_result = RawExtractionResult.model_validate(data)
        except ValidationError as exc:
            logger.error("JSON schema validation failed: %s", exc)
            return ExtractionResult(contract_type=contract_type, fields=[])

        fields = cls._convert_raw_fields(raw_result.fields, field_map_override=field_map_override)
        return ExtractionResult(
            contract_type=raw_result.contract_type or contract_type,
            contract_type_confidence=raw_result.contract_type_confidence,
            fields=fields,
        )

    @classmethod
    def _convert_raw_fields(
        cls,
        raw_fields: list[RawExtractedField],
        field_map_override: dict | None = None,
    ) -> list[ExtractedField]:
        """Convert RawExtractedField list to ExtractedField list."""
        result: list[ExtractedField] = []
        lookup = field_map_override or {}
        for rf in raw_fields:
            defn = lookup.get(rf.field_key)
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
                value=str(rf.field_value) if rf.field_value is not None else None,
                value_type=value_type,
                source_text=rf.source_text,
                page_no=rf.source_page,
                bbox=bbox,
                confidence=rf.confidence,
            ))
        return result
