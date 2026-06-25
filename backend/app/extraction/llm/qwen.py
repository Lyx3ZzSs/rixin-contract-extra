"""Qwen LLM provider — calls Qwen3-30B-A3B via Instructor (OpenAI-compatible API).

Instructor provides Pydantic-validated structured output directly from the LLM,
eliminating the need for post-hoc JSON parsing and regex extraction.
"""

from __future__ import annotations

import logging

import instructor
from openai import OpenAI
from pydantic import BaseModel

from app.config import settings
from app.extraction.base import (
    BBox,
    ExtractedField,
    ExtractionResult,
    RawExtractionResult,
)
from app.extraction.llm.base import LLMProvider
from app.extraction.llm.chunking import merge_results, split_by_pages

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM_PROMPT = (
    "你是一个合同类型分类助手。请根据合同文本判断合同类型。"
    "可选类型：service（技术服务）、purchase（采购）、lease（租赁）、"
    "sale（买卖）、construction（工程）、other（其他）。"
)

# ---------------------------------------------------------------------------
# Contract type context — helps LLM understand document structure
# ---------------------------------------------------------------------------

TYPE_CONTEXT: dict[str, str] = {
    "service": "这是一份技术服务合同，重点关注技术服务内容、验收标准和付款节点。",
    "purchase": "这是一份采购合同，重点关注供货范围、交货条件和付款安排。",
    "lease": "这是一份租赁合同，重点关注租赁物、租期和租金支付。",
    "sale": "这是一份买卖合同，重点关注标的物、价格和交付条件。",
    "construction": "这是一份工程合同，重点关注工程范围、工期和付款计划。",
}


def _build_dynamic_prompt(
    field_definitions: list,
    full_text: str,
    contract_type: str | None = None,
) -> str:
    """Build extraction prompt from DB field definitions.

    Key improvements over flat prompt:
    - Fields generated from DB definitions
    - Contract type context injected when available
    - Field descriptions and value types preserved as extraction hints
    """
    field_lines: list[str] = []
    for f in field_definitions:
        value_type = getattr(f, "value_type", "string") or "string"
        line = f"- field_key={f.field_key}；field_name={f.field_name}；value_type={value_type}"
        desc = f.description or None
        if desc:
            line += f"；description={desc}"
        field_lines.append(line)

    field_list = "\n".join(field_lines)

    # --- Contract type context ---
    type_intro = ""
    if contract_type and contract_type in TYPE_CONTEXT:
        type_intro = f"\n合同类型参考：{TYPE_CONTEXT[contract_type]}\n"

    return f"""你是一位资深合同审核专家，擅长从合同文本中精准提取结构化信息。

工作方法：
1. 先通读合同全文，识别合同结构和关键条款位置
2. 按字段名称、字段描述和 value_type 逐项定位，优先匹配原文中最直接、最完整的表述
3. 根据字段名称语义选择搜索位置：甲方/乙方/当事人优先查合同首部和签章区；金额/价款/付款/比例优先查价款和付款条款；日期/期限/生效/终止优先查签署、生效和期限条款；银行/账号/税号/电话/地址优先查主体信息和签章附近
4. 对每个字段，找到原文中最直接的表述，提取原始值{type_intro}
重要规则：

1. 只能基于输入原文抽取，不得猜测或编造。
2. 如果字段在原文中没有明确出现，field_value 返回 null。
3. 每个字段必须返回 source_text（摘录原文中对应片段）。
4. 每个字段必须返回 source_page。
5. 每个字段可以返回 source_bbox，格式为 [x1, y1, x2, y2]；只有当输入原文中明确提供坐标时才返回，不能可靠定位时返回 null，不得估算或编造。
6. 每个字段必须返回 confidence（0到1之间的浮点数）。
7. 必须覆盖“需要抽取的字段”中的每一个 field_key，每个 field_key 只能出现一次。
8. 不得新增未请求的字段，不得改写 field_key。
9. source_text 必须是合同原文片段；如果 field_value 为 null，则 source_text、source_page 和 source_bbox 也返回 null。
10. confidence 按证据明确程度给分：原文直接命中且字段含义清晰时较高；需从上下文判断、存在多个候选或表述不完整时降低。
11. 可以在顶层返回 contract_type 和 contract_type_confidence；无法判断时返回 null 和 0。
12. 不允许输出 Markdown，只输出一个合法的 JSON 对象。
13. 顶层必须包含 fields 数组，不允许把 field_key 作为顶层字段名。

输出格式示例：
{{
  "fields": [
    {{
      "field_key": "party-a-name",
      "field_name": "甲方名称",
      "field_value": "示例公司",
      "source_text": "甲方：示例公司",
      "source_page": 1,
      "source_bbox": null,
      "confidence": 0.95
    }}
  ],
  "contract_type": null,
  "contract_type_confidence": 0
}}

需要抽取的字段：

{field_list}

下面是合同全文：

{full_text}"""


class QwenLLMProvider(LLMProvider):
    """LLM provider that calls Qwen via Instructor (OpenAI-compatible API).

    Instructor wraps the OpenAI client and uses response_format / tool-calling
    to guarantee structured output matching the Pydantic response_model.
    """

    def __init__(self) -> None:
        self._client = instructor.from_openai(
            OpenAI(
                base_url=settings.llm_api_url,
                api_key=settings.llm_api_key or "not-needed",
            ),
            mode=instructor.Mode.JSON_SCHEMA,
        )

    # ------------------------------------------------------------------
    # classify_contract_type
    # ------------------------------------------------------------------

    def classify_contract_type(self, full_text: str) -> tuple[str, float]:
        """Classify contract type using Instructor for structured output."""

        class ContractClassification(BaseModel):
            contract_type: str
            confidence: float

        sample = full_text[:2000]
        try:
            result: ContractClassification = self._client.chat.completions.create(
                model=settings.llm_model_name,
                response_model=ContractClassification,
                messages=[
                    {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"请判断以下合同的类型：\n\n{sample}"},
                ],
                max_tokens=256,
                temperature=0.1,
                max_retries=1,
            )
            return result.contract_type, result.confidence
        except Exception as exc:
            logger.warning("Classify call failed: %s", exc)
            return ("unknown", 0.0)

    # ------------------------------------------------------------------
    # extract_fields — returns ExtractionResult directly (like MockProvider)
    # ------------------------------------------------------------------

    def extract_fields(
        self,
        full_text: str,
        contract_type: str | None = None,
        field_definitions: list | None = None,
    ) -> ExtractionResult:
        """Extract fields, chunking long contracts by page.

        Single-chunk input (short contracts, or text without page markers)
        takes the original one-call path — byte-identical behavior. Long
        contracts are split into overlapping page windows; each window is
        extracted independently and the results merged.
        """
        if not field_definitions:
            raise ValueError("Field definitions are required for Qwen extraction")

        chunks = split_by_pages(full_text, pages_per_chunk=settings.llm_chunk_pages)
        if len(chunks) <= 1:
            return self._extract_single(full_text, contract_type, field_definitions)

        per_chunk: list[ExtractionResult] = []
        for chunk in chunks:
            try:
                per_chunk.append(self._extract_single(chunk, contract_type, field_definitions))
            except Exception as exc:  # one bad chunk must not sink the whole doc
                logger.warning("Chunk extraction failed, skipping chunk: %s", exc)
        if not per_chunk:
            raise RuntimeError("All chunk extractions failed")
        return merge_results(per_chunk)

    def _extract_single(
        self,
        full_text: str,
        contract_type: str | None,
        field_definitions: list,
    ) -> ExtractionResult:
        """Single LLM call — the pre-chunking extraction path."""
        user_msg = _build_dynamic_prompt(field_definitions, full_text, contract_type)
        system_msg = (
            "你是一位资深合同审核专家，擅长从合同文本中精准提取结构化信息。"
        )
        logger.debug("Extraction prompt (first 500 chars): %s", user_msg[:500])
        try:
            raw: RawExtractionResult = self._client.chat.completions.create(
                model=settings.llm_model_name,
                response_model=RawExtractionResult,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                max_retries=2,
            )
        except Exception as exc:
            logger.error("Instructor extraction failed: %s", exc, exc_info=True)
            raise RuntimeError(f"Instructor extraction failed: {exc}") from exc

        fields = _convert_raw_fields(raw, field_definitions)
        return ExtractionResult(
            contract_type=raw.contract_type or contract_type,
            contract_type_confidence=raw.contract_type_confidence,
            fields=fields,
        )


def _convert_raw_fields(
    raw: RawExtractionResult,
    field_definitions: list | None,
) -> list[ExtractedField]:
    lookup = {f.field_key: f for f in field_definitions} if field_definitions else {}
    requested_keys = set(lookup)
    fields: list[ExtractedField] = []

    for rf in raw.fields:
        if requested_keys and rf.field_key not in requested_keys:
            logger.warning("Ignoring unrequested LLM field: %s", rf.field_key)
            continue

        defn = lookup.get(rf.field_key)
        bbox = None
        if rf.source_bbox and len(rf.source_bbox) == 4:
            bbox = BBox(
                x1=rf.source_bbox[0], y1=rf.source_bbox[1],
                x2=rf.source_bbox[2], y2=rf.source_bbox[3],
            )
        fields.append(ExtractedField(
            field_key=rf.field_key,
            field_name=rf.field_name or (defn.field_name if defn else rf.field_key),
            value=str(rf.field_value) if rf.field_value is not None else None,
            value_type=defn.value_type if defn else "string",
            source_text=rf.source_text,
            page_no=rf.source_page,
            bbox=bbox,
            confidence=rf.confidence,
        ))

    return fields
