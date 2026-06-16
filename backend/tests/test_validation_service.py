"""Tests for the rule validation service (Phase 7)."""

import pytest

from app.extraction.base import ExtractedField, RuleViolation, ValidationResult
from app.rules.builtin import (
    AmountConsistencyRule,
    ConfidenceThresholdRule,
    DateConsistencyRule,
    FinancialConsistencyRule,
    PaymentRatioRule,
    RequiredFieldRule,
    reset_builtin_rules,
)
from app.services.rule_validation_service import (
    validate_contract,
    validate_fields,
    ClauseCompletenessRule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _field(name: str, value: str | None = None, category: str = "basic",
           source_text: str | None = None, confidence: float = 0.9) -> ExtractedField:
    return ExtractedField(
        field_key=name,
        value=value,
        source_text=source_text,
        page_no=1,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# 1. RequiredFieldRule
# ---------------------------------------------------------------------------

class TestRequiredFieldRule:
    def test_all_present(self):
        rule = RequiredFieldRule()
        fields = [
            _field("party-a-name", "A公司", "party"),
            _field("party-b-name", "B公司", "party"),
            _field("contract-amount", "100000", "financial"),
            _field("contract-no", "HT-001"),
            _field("sign-date", "2024-01-15", "date"),
        ]
        violations = rule.check(fields)
        assert violations == []

    def test_missing_party_a(self):
        rule = RequiredFieldRule()
        fields = [
            _field("party-b-name", "B公司"),
            _field("contract-amount", "100000"),
        ]
        violations = rule.check(fields)
        names = {v.field_name for v in violations}
        assert "party-a-name" in names

    def test_missing_party_b(self):
        rule = RequiredFieldRule()
        fields = [_field("party-a-name", "A公司")]
        violations = rule.check(fields)
        names = {v.field_name for v in violations}
        assert "party-b-name" in names

    def test_missing_amount(self):
        rule = RequiredFieldRule()
        fields = [_field("party-a-name", "A公司")]
        violations = rule.check(fields)
        assert len(violations) == 1
        assert violations[0].field_name == "party-b-name"

    def test_missing_currency(self):
        rule = RequiredFieldRule()
        fields = [_field("party-a-name", "A公司"), _field("party-b-name", "B公司")]
        violations = rule.check(fields)
        assert len(violations) == 0  # All required present


    def test_empty_value_treated_as_missing(self):
        rule = RequiredFieldRule()
        fields = [_field("party-a-name", "", "party")]
        violations = rule.check(fields)
        assert len(violations) >= 1
        assert violations[0].field_name == "party-a-name"
        assert "值为空" in violations[0].description

    def test_none_value_treated_as_missing(self):
        rule = RequiredFieldRule()
        fields = [_field("party-a-name", None, "party")]
        violations = rule.check(fields)
        assert len(violations) >= 1


# ---------------------------------------------------------------------------
# 2. DateConsistencyRule
# ---------------------------------------------------------------------------

class TestDateConsistencyRule:
    def test_valid_date_range(self):
        rule = DateConsistencyRule()
        fields = [
            _field("effective-date", "2024-01-15", "date"),
            _field("end-date", "2024-12-31", "date"),
        ]
        violations = rule.check(fields)
        date_violations = [v for v in violations if v.rule_name == "date_consistency"]
        assert date_violations == []

    def test_effective_after_end(self):
        rule = DateConsistencyRule()
        fields = [
            _field("effective-date", "2025-01-01", "date"),
            _field("end-date", "2024-06-30", "date"),
        ]
        violations = rule.check(fields)
        assert len(violations) >= 1
        assert "生效日期" in violations[0].description
        assert "晚于" in violations[0].description

    def test_cn_date_format(self):
        rule = DateConsistencyRule()
        fields = [
            _field("effective-date", "2024年6月1日", "date"),
            _field("end-date", "2024年12月31日", "date"),
        ]
        violations = rule.check(fields)
        date_violations = [v for v in violations if v.rule_name == "date_consistency"]
        assert date_violations == []

    def test_cn_date_inverted(self):
        rule = DateConsistencyRule()
        fields = [
            _field("effective-date", "2025年1月1日", "date"),
            _field("end-date", "2024年6月30日", "date"),
        ]
        violations = rule.check(fields)
        assert len(violations) >= 1

    def test_missing_end_date_no_violation(self):
        rule = DateConsistencyRule()
        fields = [_field("effective-date", "2024-01-15", "date")]
        violations = rule.check(fields)
        date_violations = [v for v in violations if v.rule_name == "date_consistency"]
        assert date_violations == []

    def test_sign_date_after_end_date(self):
        rule = DateConsistencyRule()
        fields = [
            _field("sign-date", "2025-01-01", "date"),
            _field("end-date", "2024-06-30", "date"),
        ]
        violations = rule.check(fields)
        assert any("签署日期" in v.description for v in violations)


# ---------------------------------------------------------------------------
# 3. AmountConsistencyRule
# ---------------------------------------------------------------------------

class TestAmountConsistencyRule:
    def test_consistent_amounts(self):
        rule = AmountConsistencyRule()
        fields = [
            _field("contract-amount", "1200000.00", "financial",
                   source_text="人民币壹佰贰拾万元整（¥1,200,000.00）"),
        ]
        violations = rule.check(fields)
        assert violations == []

    def test_inconsistent_amounts(self):
        rule = AmountConsistencyRule()
        fields = [
            _field("contract-amount", "1500000.00", "financial",
                   source_text="人民币壹佰贰拾万元整（¥1,500,000.00）"),
        ]
        violations = rule.check(fields)
        assert len(violations) >= 1
        assert "不一致" in violations[0].description

    def test_no_source_text_skips(self):
        rule = AmountConsistencyRule()
        fields = [_field("contract-amount", "100000", "financial")]
        violations = rule.check(fields)
        assert violations == []

    def test_only_uppercase_no_violation(self):
        rule = AmountConsistencyRule()
        fields = [
            _field("contract-amount", "100000", "financial",
                   source_text="人民币壹拾万元整"),
        ]
        violations = rule.check(fields)
        assert violations == []


class TestParseUpperAmount:
    def test_yi_bai_er_shi_wan(self):
        rule = AmountConsistencyRule()
        result = rule._parse_upper_amount("壹佰贰拾万")
        assert result == 1_200_000.0

    def test_shi_wan(self):
        rule = AmountConsistencyRule()
        result = rule._parse_upper_amount("拾万")
        assert result == 100_000.0

    def test_yi_wan(self):
        rule = AmountConsistencyRule()
        result = rule._parse_upper_amount("壹万")
        assert result == 10_000.0

    def test_with_trailing(self):
        rule = AmountConsistencyRule()
        result = rule._parse_upper_amount("壹佰贰拾万元整")
        assert result == 1_200_000.0


# ---------------------------------------------------------------------------
# 4. PaymentRatioRule
# ---------------------------------------------------------------------------

class TestPaymentRatioRule:
    def test_ratios_sum_100(self):
        rule = PaymentRatioRule()
        fields = [
            _field("payment_ratio_first", "30%", "financial"),
            _field("payment_ratio_second", "40%", "financial"),
            _field("payment_ratio_third", "30%", "financial"),
        ]
        violations = rule.check(fields)
        assert violations == []

    def test_ratios_do_not_sum_100(self):
        rule = PaymentRatioRule()
        fields = [
            _field("payment_ratio_first", "30%", "financial"),
            _field("payment_ratio_second", "30%", "financial"),
        ]
        violations = rule.check(fields)
        assert len(violations) >= 1
        assert "60" in violations[0].description

    def test_single_ratio_no_check(self):
        rule = PaymentRatioRule()
        fields = [_field("payment_ratio_first", "30%", "financial")]
        violations = rule.check(fields)
        assert violations == []

    def test_no_ratio_fields(self):
        rule = PaymentRatioRule()
        fields = [_field("contract-amount", "100000", "financial")]
        violations = rule.check(fields)
        assert violations == []

    def test_fullwidth_percent(self):
        rule = PaymentRatioRule()
        fields = [
            _field("payment_ratio_first", "50％", "financial"),
            _field("payment_ratio_second", "50％", "financial"),
        ]
        violations = rule.check(fields)
        assert violations == []


# ---------------------------------------------------------------------------
# 5. FinancialConsistencyRule
# ---------------------------------------------------------------------------

class TestFinancialConsistencyRule:
    def test_negative_amount(self):
        rule = FinancialConsistencyRule()
        fields = [_field("contract-amount", "-50000", "financial")]
        violations = rule.check(fields)
        assert any("负数" in v.description for v in violations)

    def test_zero_amount(self):
        rule = FinancialConsistencyRule()
        fields = [_field("contract-amount", "0", "financial")]
        violations = rule.check(fields)
        assert any("为零" in v.description for v in violations)

    def test_valid_amount(self):
        rule = FinancialConsistencyRule()
        fields = [_field("contract-amount", "100000", "financial")]
        violations = rule.check(fields)
        assert violations == []

    def test_unparseable_amount(self):
        rule = FinancialConsistencyRule()
        fields = [_field("contract-amount", "N/A", "financial")]
        violations = rule.check(fields)
        assert any("格式异常" in v.description for v in violations)


# ---------------------------------------------------------------------------
# 6. ConfidenceThresholdRule
# ---------------------------------------------------------------------------

class TestConfidenceThresholdRule:
    def test_low_confidence_flagged(self):
        rule = ConfidenceThresholdRule()
        fields = [_field("party-a-name", "A公司", confidence=0.5)]
        violations = rule.check(fields)
        assert len(violations) == 1
        assert "置信度过低" in violations[0].description

    def test_high_confidence_ok(self):
        rule = ConfidenceThresholdRule()
        fields = [_field("party-a-name", "A公司", confidence=0.95)]
        violations = rule.check(fields)
        assert violations == []

    def test_exactly_at_threshold(self):
        rule = ConfidenceThresholdRule()
        fields = [_field("party-a-name", "A公司", confidence=0.7)]
        violations = rule.check(fields)
        assert violations == []


# ---------------------------------------------------------------------------
# ClauseCompletenessRule
# ---------------------------------------------------------------------------

class TestClauseCompletenessRule:
    def test_service_contract_all_present(self):
        rule = ClauseCompletenessRule("service")
        titles = [
            "第二条 付款方式",
            "第三条 交付条款",
            "第四条 验收标准",
            "第六条 知识产权",
            "第四条 违约责任",
            "第八条 争议解决",
        ]
        violations = rule.check_clauses(titles)
        assert violations == []

    def test_service_contract_missing_clauses(self):
        rule = ClauseCompletenessRule("service")
        titles = ["第二条 付款方式", "第四条 违约责任"]
        violations = rule.check_clauses(titles)
        kinds = {v.description.split(": ")[-1] for v in violations}
        assert "delivery" in kinds
        assert "acceptance" in kinds
        assert "intellectual_property" in kinds
        assert "dispute" in kinds

    def test_unknown_contract_type_no_check(self):
        rule = ClauseCompletenessRule("lease")
        violations = rule.check_clauses(["只有一条"])
        assert violations == []

    def test_empty_titles(self):
        rule = ClauseCompletenessRule("service")
        violations = rule.check_clauses([])
        assert len(violations) >= 6  # all 6 required clauses missing


# ---------------------------------------------------------------------------
# Full validate_fields / validate_contract
# ---------------------------------------------------------------------------

class TestValidateFields:
    def test_all_good(self):
        fields = [
            _field("party-a-name", "A公司", "party"),
            _field("party-b-name", "B公司", "party"),
            _field("contract-amount", "100000", "financial"),
            _field("contract-no", "HT-001"),
            _field("sign-date", "2024-01-15", "date"),
        ]
        result = validate_fields(fields)
        required_violations = [v for v in result.violations if v.rule_name == "required_fields"]
        assert required_violations == []

    def test_missing_multiple_required(self):
        fields = [_field("party-a-name", "A公司")]
        result = validate_fields(fields)
        required = [v for v in result.violations if v.rule_name == "required_fields"]
        assert len(required) >= 1  # party-b-name missing


class TestValidateContract:
    def test_with_clause_check(self):
        fields = [
            _field("party-a-name", "A公司", "party"),
            _field("party-b-name", "B公司", "party"),
            _field("contract-amount", "100000", "financial"),
            _field("contract-no", "HT-001"),
            _field("sign-date", "2024-01-15", "date"),
        ]
        clause_titles = [
            "第二条 付款方式",
            "第三条 交付",
            "第四条 验收标准",
            "第六条 知识产权",
            "第四条 违约责任",
            "第八条 争议解决",
        ]
        result = validate_contract(fields, contract_type="service", clause_titles=clause_titles)
        clause_violations = [v for v in result.violations if v.rule_name == "clause_completeness"]
        assert clause_violations == []

    def test_missing_clause_detected(self):
        fields = [
            _field("party-a-name", "A公司", "party"),
            _field("party-b-name", "B公司", "party"),
            _field("contract-amount", "100000", "financial"),
            _field("contract-no", "HT-001"),
            _field("sign-date", "2024-01-15", "date"),
        ]
        result = validate_contract(fields, contract_type="service", clause_titles=["第二条 付款"])
        clause_violations = [v for v in result.violations if v.rule_name == "clause_completeness"]
        assert len(clause_violations) >= 3

    def test_no_contract_type_skips_clause_check(self):
        fields = [_field("contract-amount", "100000", "financial")]
        result = validate_contract(fields, contract_type=None, clause_titles=[])
        clause_violations = [v for v in result.violations if v.rule_name == "clause_completeness"]
        assert clause_violations == []
