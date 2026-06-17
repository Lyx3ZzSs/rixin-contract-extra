"""Mock LLM provider for development and testing.

Returns structured extraction results with the unified field set (kebab-case keys)
matching the format specified in ``FIELD_DEFINITIONS`` from llm_service.

This provider returns structured ``ExtractionResult`` objects directly,
bypassing JSON serialisation — which is the fast-path for mock mode.
Real providers (OpenAI / DeepSeek / Qwen) will return raw JSON text
that gets parsed and validated in ``LLMService``.
"""

from __future__ import annotations

from app.extraction.base import (
    BBox,
    ExtractedField,
    ExtractionResult,
)
from app.extraction.llm.base import LLMProvider


class MockLLMProvider(LLMProvider):
    """Mock provider that returns pre-built extraction results."""

    def classify_contract_type(self, full_text: str) -> tuple[str, float]:
        if "开发" in full_text or "技术" in full_text:
            return ("service", 0.85)
        if "采购" in full_text or "购买" in full_text:
            return ("purchase", 0.80)
        if "租赁" in full_text:
            return ("lease", 0.80)
        return ("sale", 0.7)

    def extract_fields(
        self,
        full_text: str,
        contract_type: str | None = None,
        field_definitions: list | None = None,
    ) -> ExtractionResult:
        """Return the unified field set (kebab-case keys) plus extended fields."""
        fields = self._build_basic_fields(full_text)

        return ExtractionResult(
            contract_type=contract_type or "service",
            contract_type_confidence=0.85,
            fields=fields,
        )

    # ------------------------------------------------------------------
    # unified field set
    # ------------------------------------------------------------------

    def _build_basic_fields(self, full_text: str) -> list[ExtractedField]:
        return [
            ExtractedField(
                field_key="party-a-name",
                field_name="甲方名称",
                value="北京日新科技有限公司", value_type="string",
                source_text="甲方：北京日新科技有限公司", page_no=1,
                bbox=BBox(x1=120, y1=140, x2=520, y2=175),
                confidence=0.98,
            ),
            ExtractedField(
                field_key="party-a-legal-rep",
                field_name="甲方法定代表人",
                value="张三", value_type="string",
                source_text="法定代表人：张三", page_no=1,
                confidence=0.90,
            ),
            ExtractedField(
                field_key="party-a-agent",
                field_name="甲方委托代理人",
                value="李四", value_type="string",
                source_text="委托代理人：李四", page_no=1,
                confidence=0.88,
            ),
            ExtractedField(
                field_key="party-a-address",
                field_name="甲方通讯地址",
                value="北京市海淀区中关村大街1号", value_type="string",
                source_text="地址：北京市海淀区中关村大街1号", page_no=1,
                confidence=0.92,
            ),
            ExtractedField(
                field_key="party-a-bank",
                field_name="甲方开户行",
                value="中国工商银行北京分行", value_type="string",
                source_text="开户行：中国工商银行北京分行", page_no=1,
                confidence=0.91,
            ),
            ExtractedField(
                field_key="party-a-account",
                field_name="甲方账号",
                value="0200003609001234567", value_type="string",
                source_text="账号：0200003609001234567", page_no=1,
                confidence=0.95,
            ),
            ExtractedField(
                field_key="party-a-tax",
                field_name="甲方税号",
                value="91110108MA01XXXXXX", value_type="string",
                source_text="税号：91110108MA01XXXXXX", page_no=1,
                confidence=0.93,
            ),
            ExtractedField(
                field_key="party-a-phone",
                field_name="甲方电话",
                value="010-88888888", value_type="string",
                source_text="电话：010-88888888", page_no=1,
                confidence=0.90,
            ),
            ExtractedField(
                field_key="party-b-name",
                field_name="乙方名称",
                value="上海恒信信息技术有限公司", value_type="string",
                source_text="乙方：上海恒信信息技术有限公司", page_no=1,
                bbox=BBox(x1=120, y1=190, x2=520, y2=225),
                confidence=0.98,
            ),
            ExtractedField(
                field_key="party-b-legal-rep",
                field_name="乙方法定代表人",
                value="王五", value_type="string",
                source_text="法定代表人：王五", page_no=1,
                confidence=0.90,
            ),
            ExtractedField(
                field_key="party-b-agent",
                field_name="乙方委托代理人",
                value="赵六", value_type="string",
                source_text="委托代理人：赵六", page_no=1,
                confidence=0.88,
            ),
            ExtractedField(
                field_key="party-b-address",
                field_name="乙方通讯地址",
                value="上海市浦东新区张江高科技园区", value_type="string",
                source_text="地址：上海市浦东新区张江高科技园区", page_no=1,
                confidence=0.92,
            ),
            ExtractedField(
                field_key="party-b-bank",
                field_name="乙方开户行",
                value="中国建设银行上海分行", value_type="string",
                source_text="开户行：中国建设银行上海分行", page_no=1,
                confidence=0.91,
            ),
            ExtractedField(
                field_key="party-b-account",
                field_name="乙方账号",
                value="3100152030005XXXXXXXX", value_type="string",
                source_text="账号：3100152030005XXXXXXXX", page_no=1,
                confidence=0.95,
            ),
            ExtractedField(
                field_key="party-b-tax",
                field_name="乙方税号",
                value="91310115MA1HXXXXXX", value_type="string",
                source_text="税号：91310115MA1HXXXXXX", page_no=1,
                confidence=0.93,
            ),
            ExtractedField(
                field_key="party-b-phone",
                field_name="乙方电话",
                value="021-66666666", value_type="string",
                source_text="电话：021-66666666", page_no=1,
                confidence=0.90,
            ),
        ]
