"""Tests for LLM service abstraction layer."""

import json
import pytest

from app.extraction.base import (
    BBox,
    ClauseSegment,
    FieldSpec,
    ExtractedField,
    ExtractionResult,
)
from app.services.llm_service import (
    LLMService,
    RawExtractionResult,
    RawExtractedField,
    _extract_json_from_text,
)
from app.extraction.llm.qwen import _build_dynamic_prompt


# ---------------------------------------------------------------------------
# Field definition registry tests
# JSON extraction helpers
# ---------------------------------------------------------------------------

class TestExtractJsonFromText:
    def test_plain_json(self):
        text = '{"fields": []}'
        assert _extract_json_from_text(text) == text

    def test_json_in_code_block(self):
        text = '```json\n{"fields": []}\n```'
        result = _extract_json_from_text(text)
        assert result is not None
        assert json.loads(result) == {"fields": []}

    def test_json_in_code_block_no_language(self):
        text = '```\n{"fields": []}\n```'
        result = _extract_json_from_text(text)
        assert result is not None

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"fields": [{"key": "val"}]}\nEnd.'
        result = _extract_json_from_text(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["fields"][0]["key"] == "val"

    def test_invalid_json_returns_none(self):
        assert _extract_json_from_text("not json at all") is None

    def test_empty_string(self):
        assert _extract_json_from_text("") is None


# ---------------------------------------------------------------------------
# RawExtractionResult validation
# ---------------------------------------------------------------------------

class TestRawExtractionResult:
    def test_valid_minimal(self):
        data = {"fields": []}
        result = RawExtractionResult.model_validate(data)
        assert result.fields == []

    def test_valid_with_fields(self):
        data = {
            "fields": [
                {
                    "field_key": "party-a-name",
                    "field_value": "测试公司",
                    "confidence": 0.95,
                },
            ],
        }
        result = RawExtractionResult.model_validate(data)
        assert len(result.fields) == 1
        assert result.fields[0].field_key == "party-a-name"
        assert result.fields[0].field_value == "测试公司"

    def test_valid_full(self):
        data = {
            "fields": [
                {
                    "field_key": "amount",
                    "field_name": "合同金额",
                    "field_value": "100000",
                    "source_text": "合同金额10万元",
                    "source_page": 2,
                    "source_bbox": [100, 200, 500, 240],
                    "confidence": 0.92,
                    "review_status": "extracted",
                },
            ],
            "contract_type": "service",
            "contract_type_confidence": 0.88,
            "key_clauses": [
                {
                    "clause_type": "payment",
                    "clause_title": "付款条款",
                    "content": "分三期支付。",
                    "confidence": 0.85,
                },
            ],
        }
        result = RawExtractionResult.model_validate(data)
        assert result.contract_type == "service"
        assert len(result.key_clauses) == 1
        field = result.fields[0]
        assert field.source_bbox == [100, 200, 500, 240]
        assert field.source_page == 2


# ---------------------------------------------------------------------------
# Dynamic prompt generation
# ---------------------------------------------------------------------------

class TestDynamicPrompt:
    def test_prompt_uses_field_specs_without_category(self):
        prompt = _build_dynamic_prompt(
            [
                FieldSpec(
                    field_key="contract-amount",
                    field_name="合同金额",
                    description="合同总金额，包括币种和数额",
                    value_type="string",
                ),
            ],
            "合同总金额为人民币10万元。",
            "service",
        )

        assert "field_key=contract-amount" in prompt
        assert "field_name=合同金额" in prompt
        assert "value_type=string" in prompt
        assert "description=合同总金额，包括币种和数额" in prompt
        assert "合同类型参考" in prompt
        assert "字段类别" not in prompt
        assert "field_category" not in prompt

    def test_prompt_has_completeness_and_evidence_rules(self):
        prompt = _build_dynamic_prompt(
            [
                FieldSpec(field_key="party-a-name", field_name="甲方名称"),
                FieldSpec(field_key="sign-date", field_name="签署日期"),
            ],
            "甲方：测试公司\n签署日期：2024年1月1日",
        )

        assert "必须覆盖“需要抽取的字段”中的每一个 field_key" in prompt
        assert "不得新增未请求的字段" in prompt
        assert "每个 field_key 只能出现一次" in prompt
        assert "source_text 必须是合同原文片段" in prompt
        assert "甲方/乙方/当事人优先查合同首部和签章区" in prompt


# ---------------------------------------------------------------------------
# LLMService — classify_contract_type
# ---------------------------------------------------------------------------

class TestLLMServiceClassify:
    async def test_classify_service_contract(self):
        text = "甲方委托乙方进行系统开发与技术支持"
        ctype, conf = await LLMService.classify_contract_type(text)
        assert ctype == "service"
        assert conf > 0

    async def test_classify_purchase_contract(self):
        text = "甲方同意向乙方采购以下设备"
        ctype, conf = await LLMService.classify_contract_type(text)
        assert ctype == "purchase"
        assert conf > 0

    async def test_classify_unknown(self):
        # Any text should return something, not crash
        ctype, conf = await LLMService.classify_contract_type("通用合同文本")
        assert isinstance(ctype, str)
        assert isinstance(conf, float)


# ---------------------------------------------------------------------------
# LLMService — extract_fields_from_text
# ---------------------------------------------------------------------------

class TestLLMServiceExtract:
    async def test_mock_extraction_returns_result(self):
        text = (
            "合同编号：HT-2024-001\n"
            "甲方：北京日新科技有限公司\n"
            "乙方：上海恒信信息技术有限公司\n"
            "项目总金额为人民币1,200,000.00元"
        )
        result = await LLMService.extract_fields_from_text(text)
        assert isinstance(result, ExtractionResult)
        assert len(result.fields) > 0

    async def test_all_basic_fields_present(self):
        """The mock provider should return all 16 fields."""
        result = await LLMService.extract_fields_from_text("测试合同文本")
        field_keys = {f.field_key for f in result.fields}
        expected = {
            "party-a-name", "party-a-legal-rep", "party-a-agent",
            "party-a-address", "party-a-bank", "party-a-account",
            "party-a-tax", "party-a-phone",
            "party-b-name", "party-b-legal-rep", "party-b-agent",
            "party-b-address", "party-b-bank", "party-b-account",
            "party-b-tax", "party-b-phone",
        }
        for key in expected:
            assert key in field_keys, f"Missing field: {key}"

    async def test_fields_have_source_text(self):
        result = await LLMService.extract_fields_from_text("测试合同")
        fields_with_source = [f for f in result.fields if f.source_text]
        # Most fields should have source_text
        assert len(fields_with_source) >= 8

    async def test_fields_have_page_no(self):
        result = await LLMService.extract_fields_from_text("测试合同")
        fields_with_page = [f for f in result.fields if f.page_no is not None]
        assert len(fields_with_page) >= 8

    async def test_fields_have_bbox(self):
        result = await LLMService.extract_fields_from_text("测试合同")
        fields_with_bbox = [f for f in result.fields if f.bbox is not None]
        assert len(fields_with_bbox) >= 2

    async def test_fields_have_confidence(self):
        result = await LLMService.extract_fields_from_text("测试合同")
        for f in result.fields:
            assert 0.0 <= f.confidence <= 1.0, f"Invalid confidence for {f.field_key}"

    async def test_key_clauses_returned(self):
        result = await LLMService.extract_fields_from_text("测试合同")
        assert len(result.key_clauses) >= 3
        for clause in result.key_clauses:
            assert clause.clause_title
            assert clause.content

    async def test_empty_text_returns_result(self):
        result = await LLMService.extract_fields_from_text("")
        assert isinstance(result, ExtractionResult)
        # Mock provider always returns fields
        assert len(result.fields) > 0


# ---------------------------------------------------------------------------
# LLMService — _parse_raw_json (simulated real LLM output)
# ---------------------------------------------------------------------------

class TestLLMServiceParseRawJson:
    def test_valid_json_string(self):
        """If a provider returns raw JSON text, it should be parsed correctly."""
        json_text = json.dumps({
            "fields": [
                {
                    "field_key": "party-a-name",
                    "field_value": "测试公司",
                    "source_text": "甲方：测试公司",
                    "source_page": 1,
                    "source_bbox": [100, 200, 400, 230],
                    "confidence": 0.95,
                },
            ],
            "contract_type": "service",
            "contract_type_confidence": 0.9,
            "key_clauses": [],
        })
        result = LLMService._parse_raw_json(json_text, None)
        assert isinstance(result, ExtractionResult)
        assert len(result.fields) == 1
        assert result.fields[0].field_key == "party-a-name"
        assert result.fields[0].value == "测试公司"
        assert result.fields[0].bbox is not None
        assert result.fields[0].bbox.x1 == 100

    def test_invalid_json_returns_empty(self):
        result = LLMService._parse_raw_json("not valid json {{{", None)
        assert isinstance(result, ExtractionResult)
        assert len(result.fields) == 0

    def test_json_with_extra_text(self):
        raw = '以下是抽取结果：\n```json\n{"fields": []}\n```\n以上为结果。'
        result = LLMService._parse_raw_json(raw, "service")
        assert isinstance(result, ExtractionResult)
        assert result.contract_type == "service"

    def test_value_type_from_definition(self):
        """Value type should be resolved from field_map_override."""
        from app.extraction.base import FieldSpec
        fmap = {
            "party-a-name": FieldSpec(
                field_key="party-a-name", field_name="甲方名称", value_type="string",
            ),
            "contract-amount": FieldSpec(
                field_key="contract-amount", field_name="合同金额", value_type="number",
            ),
        }
        json_text = json.dumps({
            "fields": [
                {"field_key": "party-a-name", "field_value": "测试公司"},
                {"field_key": "contract-amount", "field_value": "10000"},
            ],
        })
        result = LLMService._parse_raw_json(json_text, None, field_map_override=fmap)
        name_field = next(f for f in result.fields if f.field_key == "party-a-name")
        assert name_field.value_type == "string"

        amount_field = next(f for f in result.fields if f.field_key == "contract-amount")
        assert amount_field.value_type == "number"

    def test_value_type_defaults_to_string_without_map(self):
        """Without field_map_override, value_type should default to 'string'."""
        json_text = json.dumps({
            "fields": [
                {"field_key": "party-a-name", "field_value": "测试公司"},
            ],
        })
        result = LLMService._parse_raw_json(json_text, None)
        assert result.fields[0].value_type == "string"


# ---------------------------------------------------------------------------
# Raw field conversion tests
# ---------------------------------------------------------------------------

class TestRawFieldConversion:
    def test_convert_raw_fields(self):
        raw = [
            RawExtractedField(
                field_key="party-a-name",
                field_value="北京测试公司",
                source_text="甲方：北京测试公司",
                source_page=1,
                source_bbox=[100, 200, 400, 240],
                confidence=0.97,
            ),
        ]
        fmap = {
            "party-a-name": FieldSpec(
                field_key="party-a-name", field_name="甲方名称",
            ),
        }
        fields = LLMService._convert_raw_fields(raw, field_map_override=fmap)
        assert len(fields) == 1
        f = fields[0]
        assert f.field_key == "party-a-name"
        assert f.value == "北京测试公司"
        assert f.bbox is not None
        assert f.bbox.x1 == 100
        assert f.confidence == 0.97

    def test_convert_raw_fields_unknown_key(self):
        """Unknown field_key should default to value_type='string'."""
        raw = [
            RawExtractedField(field_key="custom_field", field_value="hello"),
        ]
        fields = LLMService._convert_raw_fields(raw)
        assert fields[0].value_type == "string"

    def test_convert_raw_fields_none_value(self):
        raw = [
            RawExtractedField(field_key="tax-included", field_value=None),
        ]
        fields = LLMService._convert_raw_fields(raw)
        assert fields[0].value is None

    def test_convert_raw_clauses(self):
        raw_clauses = [
            {
                "clause_type": "payment",
                "clause_title": "付款条款",
                "content": "分三期支付。",
                "confidence": 0.9,
            },
        ]
        clauses = LLMService._convert_raw_clauses(raw_clauses)
        assert len(clauses) == 1
        assert clauses[0].clause_type == "payment"
        assert clauses[0].clause_title == "付款条款"
        assert clauses[0].content == "分三期支付。"

    def test_convert_raw_clauses_minimal(self):
        raw_clauses = [{"clause_title": "条款", "content": "内容"}]
        clauses = LLMService._convert_raw_clauses(raw_clauses)
        assert clauses[0].clause_type is None
        assert clauses[0].confidence == 0.8  # default
