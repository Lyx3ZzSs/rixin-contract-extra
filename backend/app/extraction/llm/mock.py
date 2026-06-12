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
    ClauseSegment,
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

    def extract_fields(self, full_text: str, contract_type: str | None = None) -> ExtractionResult:
        """Return the unified field set (kebab-case keys) plus extended fields."""
        fields = self._build_basic_fields(full_text)
        key_clauses = self._build_key_clauses()

        return ExtractionResult(
            contract_type=contract_type or "service",
            contract_type_confidence=0.85,
            fields=fields,
            key_clauses=key_clauses,
        )

    # ------------------------------------------------------------------
    # unified field set
    # ------------------------------------------------------------------

    def _build_basic_fields(self, full_text: str) -> list[ExtractedField]:
        return [
            ExtractedField(
                field_key="party-a-name",
                field_name="甲方名称", field_category="party",
                value="北京日新科技有限公司", value_type="string",
                source_text="甲方：北京日新科技有限公司", page_no=1,
                bbox=BBox(x1=120, y1=140, x2=520, y2=175),
                confidence=0.98,
            ),
            ExtractedField(
                field_key="party-a-legal-rep",
                field_name="甲方法定代表人", field_category="party",
                value="张三", value_type="string",
                source_text="法定代表人：张三", page_no=1,
                confidence=0.90,
            ),
            ExtractedField(
                field_key="party-a-agent",
                field_name="甲方委托代理人", field_category="party",
                value="李四", value_type="string",
                source_text="委托代理人：李四", page_no=1,
                confidence=0.88,
            ),
            ExtractedField(
                field_key="party-a-address",
                field_name="甲方通讯地址", field_category="party",
                value="北京市海淀区中关村大街1号", value_type="string",
                source_text="地址：北京市海淀区中关村大街1号", page_no=1,
                confidence=0.92,
            ),
            ExtractedField(
                field_key="party-a-bank",
                field_name="甲方开户行", field_category="party",
                value="中国工商银行北京分行", value_type="string",
                source_text="开户行：中国工商银行北京分行", page_no=1,
                confidence=0.91,
            ),
            ExtractedField(
                field_key="party-a-account",
                field_name="甲方账号", field_category="party",
                value="0200003609001234567", value_type="string",
                source_text="账号：0200003609001234567", page_no=1,
                confidence=0.95,
            ),
            ExtractedField(
                field_key="party-a-tax",
                field_name="甲方税号", field_category="party",
                value="91110108MA01XXXXXX", value_type="string",
                source_text="税号：91110108MA01XXXXXX", page_no=1,
                confidence=0.93,
            ),
            ExtractedField(
                field_key="party-a-phone",
                field_name="甲方电话", field_category="party",
                value="010-88888888", value_type="string",
                source_text="电话：010-88888888", page_no=1,
                confidence=0.90,
            ),
            ExtractedField(
                field_key="party-b-name",
                field_name="乙方名称", field_category="party",
                value="上海恒信信息技术有限公司", value_type="string",
                source_text="乙方：上海恒信信息技术有限公司", page_no=1,
                bbox=BBox(x1=120, y1=190, x2=520, y2=225),
                confidence=0.98,
            ),
            ExtractedField(
                field_key="party-b-legal-rep",
                field_name="乙方法定代表人", field_category="party",
                value="王五", value_type="string",
                source_text="法定代表人：王五", page_no=1,
                confidence=0.90,
            ),
            ExtractedField(
                field_key="party-b-agent",
                field_name="乙方委托代理人", field_category="party",
                value="赵六", value_type="string",
                source_text="委托代理人：赵六", page_no=1,
                confidence=0.88,
            ),
            ExtractedField(
                field_key="party-b-address",
                field_name="乙方通讯地址", field_category="party",
                value="上海市浦东新区张江高科技园区", value_type="string",
                source_text="地址：上海市浦东新区张江高科技园区", page_no=1,
                confidence=0.92,
            ),
            ExtractedField(
                field_key="party-b-bank",
                field_name="乙方开户行", field_category="party",
                value="中国建设银行上海分行", value_type="string",
                source_text="开户行：中国建设银行上海分行", page_no=1,
                confidence=0.91,
            ),
            ExtractedField(
                field_key="party-b-account",
                field_name="乙方账号", field_category="party",
                value="3100152030005XXXXXXXX", value_type="string",
                source_text="账号：3100152030005XXXXXXXX", page_no=1,
                confidence=0.95,
            ),
            ExtractedField(
                field_key="party-b-tax",
                field_name="乙方税号", field_category="party",
                value="91310115MA1HXXXXXX", value_type="string",
                source_text="税号：91310115MA1HXXXXXX", page_no=1,
                confidence=0.93,
            ),
            ExtractedField(
                field_key="party-b-phone",
                field_name="乙方电话", field_category="party",
                value="021-66666666", value_type="string",
                source_text="电话：021-66666666", page_no=1,
                confidence=0.90,
            ),
        ]

    # ------------------------------------------------------------------
    # Key clauses
    # ------------------------------------------------------------------

    def _build_key_clauses(self) -> list[ClauseSegment]:
        return [
            ClauseSegment(
                clause_type="payment",
                clause_title="第二条 付款方式",
                content=(
                    "本合同总金额为人民币1,200,000.00元，分三期支付：\n"
                    "1. 合同签订后5个工作日内，甲方向乙方支付合同总金额的30%，即360,000.00元；\n"
                    "2. 系统开发完成并通过验收后10个工作日内，甲方向乙方支付合同总金额的40%，即480,000.00元；\n"
                    "3. 系统上线运行满6个月且无重大故障后10个工作日内，甲方向乙方支付合同总金额的30%，即360,000.00元。"
                ),
                page_no=1,
                confidence=0.92,
            ),
            ClauseSegment(
                clause_type="breach",
                clause_title="第四条 违约责任",
                content=(
                    "1. 如乙方未按约定时间完成项目交付，每逾期一天，应向甲方支付合同总金额的0.1%作为违约金，"
                    "违约金总额不超过合同总金额的10%。\n"
                    "2. 如甲方未按约定时间支付款项，每逾期一天，应向乙方支付应付未付金额的0.05%作为滞纳金。"
                ),
                page_no=1,
                confidence=0.90,
            ),
            ClauseSegment(
                clause_type="confidentiality",
                clause_title="第五条 保密条款",
                content=(
                    "双方应对在合作过程中知悉的对方商业秘密、技术秘密及其他保密信息承担保密义务，"
                    "保密期限为合同终止后3年。"
                ),
                page_no=1,
                confidence=0.91,
            ),
            ClauseSegment(
                clause_type="termination",
                clause_title="第九条 合同期限",
                content="本合同自双方签字盖章之日起生效，有效期至双方义务全部履行完毕之日止。",
                page_no=1,
                confidence=0.88,
            ),
            ClauseSegment(
                clause_type="dispute",
                clause_title="第八条 争议解决",
                content=(
                    "因本合同引起的或与本合同有关的任何争议，双方应首先通过友好协商解决；"
                    "协商不成的，任何一方均可向合同签订地有管辖权的人民法院提起诉讼。"
                ),
                page_no=1,
                confidence=0.87,
            ),
        ]
