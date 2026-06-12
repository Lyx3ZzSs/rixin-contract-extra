"""Qwen LLM provider -- calls Qwen3-30B-A3B via OpenAI-compatible API.

Returns raw JSON text for LLMService._parse_raw_json() to handle.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.extraction.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "prompts"

_CLASSIFY_SYSTEM_PROMPT = (
    "你是一个合同类型分类助手。请根据合同文本判断合同类型。"
    '只返回JSON，格式为：{"contract_type": "...", "confidence": 0.85}\n'
    "可选类型：service（技术服务）、purchase（采购）、lease（租赁）、"
    "sale（买卖）、construction（工程）、other（其他）。\n"
    "不要输出任何其他内容。"
)

# ---------------------------------------------------------------------------
# Category-specific extraction hints — dynamically injected into prompt
# ---------------------------------------------------------------------------

CATEGORY_LABELS: dict[str, str] = {
    "party": "当事人信息",
    "financial": "金融信息",
    "date": "日期信息",
    "basic": "基本信息",
    "clause": "条款信息",
}

CATEGORY_HINTS: dict[str, str] = {
    "party": "从合同开头的当事人信息部分提取，通常在“甲方/乙方”后的签章区域。",
    "financial": "从付款条款、费用条款中提取。注意区分总金额、分项金额和比例。保留原文的数值表述（如“360,000.00元”或“30%”）。注意同义词：预付款/首期款/前期款/定金均可能指代预付款。",
    "date": "从合同期限、生效条件等条款中提取，优先提取精确日期。",
    "basic": "从合同基本信息部分提取。",
    "clause": "从合同条款中提取关键内容。",
}

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
    - Fields grouped by category with section headers
    - Category-specific extraction hints auto-generated
    - Contract type context injected when available
    - Diverse examples covering multiple field categories
    """
    # --- Group fields by category ---
    from collections import OrderedDict
    groups: dict[str, list] = OrderedDict()
    for f in field_definitions:
        cat = f.field_category
        groups.setdefault(cat, []).append(f)

    # --- Build grouped field list with category hints ---
    field_sections: list[str] = []
    for cat, fields in groups.items():
        label = CATEGORY_LABELS.get(cat, cat)
        hint = CATEGORY_HINTS.get(cat, "")
        header = f"【{label}】"
        if hint:
            header += f"  {hint}"
        field_sections.append(header)
        for f in fields:
            line = f"  - {f.field_key}：{f.field_name}"
            desc = f.description or None
            if desc:
                line += f"（{desc}）"
            field_sections.append(line)

    grouped_fields = "\n".join(field_sections)

    # --- Contract type context ---
    type_intro = ""
    if contract_type and contract_type in TYPE_CONTEXT:
        type_intro = f"\n合同类型参考：{TYPE_CONTEXT[contract_type]}\n"

    return f"""你是一位资深合同审核专家，擅长从合同文本中精准提取结构化信息。

工作方法：
1. 先通读合同全文，识别合同结构和关键条款位置
2. 按字段类别依次定位：当事人信息通常在合同开头，金额和付款条款在中间，日期和签章在末尾
3. 对每个字段，找到原文中最直接的表述，提取原始值{type_intro}
重要规则：

1. 只能基于输入原文抽取，不得猜测或编造。
2. 如果字段在原文中没有明确出现，field_value 返回 null。
3. 每个字段必须返回 source_text（摘录原文中对应片段）。
4. 每个字段必须返回 source_page。
5. 每个字段必须返回 confidence（0到1之间的浮点数）。
6. 不允许输出 Markdown，只输出一个合法的 JSON 对象。

需要抽取的字段：

{grouped_fields}

输出格式示例：

{{
  "fields": [
    {{
      "field_key": "party-a-name",
      "field_name": "甲方名称",
      "field_value": "上海某某科技有限公司",
      "source_text": "甲方：上海某某科技有限公司",
      "source_page": 1,
      "confidence": 0.97
    }},
    {{
      "field_key": "contract-amount",
      "field_name": "合同金额",
      "field_value": "人民币1,200,000.00元",
      "source_text": "合同总金额为人民币壹佰贰拾万元整（¥1,200,000.00）",
      "source_page": 2,
      "confidence": 0.95
    }},
    {{
      "field_key": "prepayment-amount",
      "field_name": "预付款金额",
      "field_value": "360,000.00元",
      "source_text": "合同签订后5个工作日内支付合同总金额的30%，即360,000.00元",
      "source_page": 2,
      "confidence": 0.92
    }}
  ]
}}

字段不存在时：
{{"field_key": "xxx", "field_name": "xxx", "field_value": null, "source_text": null, "source_page": null, "confidence": 0}}

下面是合同全文：

{full_text}"""


class QwenLLMProvider(LLMProvider):
    """LLM provider that calls Qwen via OpenAI-compatible chat completions API."""

    def classify_contract_type(self, full_text: str) -> tuple[str, float]:
        sample = full_text[:2000]
        user_msg = f"请判断以下合同的类型：\n\n{sample}"

        resp_text = self._chat(
            system=_CLASSIFY_SYSTEM_PROMPT,
            user=user_msg,
            max_tokens=256,
            temperature=0.1,
        )
        if resp_text is None:
            return ("unknown", 0.0)

        try:
            data = json.loads(resp_text)
            return (
                data.get("contract_type", "unknown"),
                float(data.get("confidence", 0.0)),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Failed to parse classify response: %s", exc)
            return ("unknown", 0.0)

    def extract_fields(self, full_text: str, contract_type: str | None = None,
                       field_definitions: list | None = None):
        """Extract fields. Returns raw JSON string.

        If field_definitions is provided (from DB), generates a dynamic prompt.
        Otherwise falls back to the static prompt template file.
        """
        if field_definitions:
            user_msg = _build_dynamic_prompt(field_definitions, full_text, contract_type)
        else:
            # Fallback: load static prompt template
            prompt_path = _PROMPTS_DIR / "extract_basic_fields.md"
            try:
                template = prompt_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                logger.error("Prompt file not found: %s", prompt_path)
                return '{"fields": []}'
            user_msg = template.replace("{{contract_chunks}}", full_text)

        system_msg = (
            "你是一位资深合同审核专家，擅长从合同文本中精准提取结构化信息。\n"
            "严格要求：只输出一个合法的 JSON 对象，不要输出任何其他内容，不要用 Markdown 包裹。"
        )

        logger.debug("Extraction prompt (first 500 chars): %s", user_msg[:500])

        resp_text = self._chat(
            system=system_msg,
            user=user_msg,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
        if resp_text is None:
            return '{"fields": []}'

        logger.debug("Extraction response (first 500 chars): %s", resp_text[:500])
        return resp_text

    # ------------------------------------------------------------------
    # HTTP chat completions call
    # ------------------------------------------------------------------

    @staticmethod
    def _chat(
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
        retries: int = 2,
    ) -> str | None:
        url = settings.llm_api_url
        payload = {
            "model": settings.llm_model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        timeout = settings.llm_timeout
        headers = {}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"

        for attempt in range(1 + retries):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
            except httpx.HTTPError as exc:
                logger.warning(
                    "LLM request failed (attempt %d/%d): %s",
                    attempt + 1, 1 + retries, exc,
                )
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                logger.error("Failed to parse LLM response: %s", exc)

        return None
