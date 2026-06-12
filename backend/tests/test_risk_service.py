"""Tests for the risk identification service (Phase 8)."""

import pytest

from app.extraction.base import (
    ClauseSegment,
    ExtractedField,
    RiskItem,
    RuleViolation,
    ValidationResult,
)
from app.services.risk_service import identify_risks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _field(name: str, value: str | None = None, category: str = "basic",
           source_text: str | None = None, confidence: float = 0.9) -> ExtractedField:
    return ExtractedField(
        field_key=name,
        field_category=category,
        value=value,
        source_text=source_text,
        page_no=1,
        confidence=confidence,
    )


def _clause(title: str, content: str, clause_type: str | None = None) -> ClauseSegment:
    return ClauseSegment(
        clause_type=clause_type,
        clause_title=title,
        content=content,
        page_no=1,
        confidence=0.9,
    )


def _good_fields() -> list[ExtractedField]:
    """Fields that should not trigger field-based risks."""
    return [
        _field("party-a-name", "A公司", "party", confidence=0.95),
        _field("party-b-name", "B公司", "party", confidence=0.95),
        _field("contract-amount", "100000", "financial", source_text="人民币拾万元整（¥100,000.00）"),
        _field("sign-date", "2024-01-15", "date"),
        _field("end-date", "2024-06-30", "date"),
    ]


def _good_clauses() -> list[ClauseSegment]:
    """Clauses that should not trigger clause-based risks."""
    return [
        _clause("第二条 付款方式", "合同签订后5个工作日内支付30%，验收后支付70%。", "payment"),
        _clause("第四条 违约责任", "如乙方未按约定交付，应支付合同总金额的0.1%作为违约金。", "breach"),
        _clause("第八条 争议解决", "因本合同引起的争议，双方协商解决；协商不成，向合同签订地人民法院起诉。", "dispute"),
        _clause("第三条 验收标准", "乙方应于交付后10个工作日内完成验收，验收标准详见附件。", "acceptance"),
        _clause("第六条 知识产权", "项目成果的知识产权归甲方所有。乙方不得将项目成果用于其他用途。", "intellectual_property"),
    ]


def _clean_validation() -> ValidationResult:
    return ValidationResult(passed=True, violations=[])


# ---------------------------------------------------------------------------
# amount_inconsistency
# ---------------------------------------------------------------------------

class TestAmountInconsistency:
    def test_missing_amount(self):
        fields = []
        risks = identify_risks(fields, _clean_validation())
        assert any(r.risk_type == "amount_inconsistency" and "缺失" in r.description for r in risks)

    def test_zero_amount(self):
        fields = [_field("contract-amount", "0", "financial")]
        risks = identify_risks(fields, _clean_validation())
        assert any(r.risk_type == "amount_inconsistency" and "异常" in r.description for r in risks)

    def test_negative_amount(self):
        fields = [_field("contract-amount", "-5000", "financial")]
        risks = identify_risks(fields, _clean_validation())
        assert any(r.risk_type == "amount_inconsistency" for r in risks)

    def test_no_currency_field_no_risk(self):
        """Currency is embedded in contract-amount; no separate check needed."""
        fields = [_field("contract-amount", "100000", "financial")]
        risks = identify_risks(fields, _clean_validation())
        assert not any("币种" in r.description for r in risks)

    def test_good_amount_no_risk(self):
        risks = identify_risks(_good_fields(), _clean_validation(), _good_clauses())
        assert not any(r.risk_type == "amount_inconsistency" for r in risks)


# ---------------------------------------------------------------------------
# party_inconsistency
# ---------------------------------------------------------------------------

class TestPartyInconsistency:
    def test_missing_party_a(self):
        fields = [_field("party-b-name", "B公司")]
        risks = identify_risks(fields, _clean_validation())
        assert any(r.risk_type == "party_inconsistency" and "甲方" in r.description for r in risks)

    def test_missing_party_b(self):
        fields = [_field("party-a-name", "A公司")]
        risks = identify_risks(fields, _clean_validation())
        assert any(r.risk_type == "party_inconsistency" and "乙方" in r.description for r in risks)

    def test_low_confidence_party(self):
        fields = [_field("party-a-name", "某公司", confidence=0.5), _field("party-b-name", "B公司", confidence=0.95)]
        risks = identify_risks(fields, _clean_validation())
        assert any(r.risk_type == "party_inconsistency" and "置信度" in r.description for r in risks)

    def test_good_parties_no_risk(self):
        risks = identify_risks(_good_fields(), _clean_validation(), _good_clauses())
        assert not any(r.risk_type == "party_inconsistency" for r in risks)


# ---------------------------------------------------------------------------
# missing_payment_clause
# ---------------------------------------------------------------------------

class TestMissingPaymentClause:
    def test_no_payment_clause(self):
        clauses = [_clause("第三条 工期", "工期6个月。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "missing_payment_clause" for r in risks)

    def test_has_payment_clause(self):
        clauses = [
            _clause("第二条 付款方式", "分三期支付。"),
            *_good_clauses(),
        ]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        # Could still exist from other reasons but not because payment is missing
        payment_missing = [r for r in risks if r.risk_type == "missing_payment_clause"]
        assert payment_missing == []

    def test_payment_in_content_but_not_title(self):
        clauses = [_clause("第三条 其他", "付款方式为银行转账。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert not any(r.risk_type == "missing_payment_clause" for r in risks)


# ---------------------------------------------------------------------------
# missing_breach_clause
# ---------------------------------------------------------------------------

class TestMissingBreachClause:
    def test_no_breach_clause(self):
        clauses = [_clause("第三条 工期", "工期6个月。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "missing_breach_clause" for r in risks)

    def test_has_breach_clause(self):
        risks = identify_risks(_good_fields(), _clean_validation(), _good_clauses())
        assert not any(r.risk_type == "missing_breach_clause" for r in risks)


# ---------------------------------------------------------------------------
# missing_dispute_clause
# ---------------------------------------------------------------------------

class TestMissingDisputeClause:
    def test_no_dispute_clause(self):
        clauses = [_clause("第三条 工期", "工期6个月。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "missing_dispute_clause" for r in risks)

    def test_has_dispute_clause(self):
        risks = identify_risks(_good_fields(), _clean_validation(), _good_clauses())
        assert not any(r.risk_type == "missing_dispute_clause" for r in risks)


# ---------------------------------------------------------------------------
# long_payment_period
# ---------------------------------------------------------------------------

class TestLongPaymentPeriod:
    def test_short_period_no_risk(self):
        risks = identify_risks(_good_fields(), _clean_validation(), _good_clauses())
        assert not any(r.risk_type == "long_payment_period" for r in risks)

    def test_long_period_triggers_risk(self):
        fields = [
            _field("sign-date", "2024-01-01", "date"),
            _field("end-date", "2026-06-30", "date"),
            _field("contract-amount", "100000", "financial"),
            _field("party-a-name", "A公司", "party"),
            _field("party-b-name", "B公司", "party"),
        ]
        risks = identify_risks(fields, _clean_validation(), _good_clauses())
        long_risks = [r for r in risks if r.risk_type == "long_payment_period"]
        assert len(long_risks) >= 1
        assert "911" in long_risks[0].evidence

    def test_no_dates_no_risk(self):
        fields = [_field("contract-amount", "100000")]
        risks = identify_risks(fields, _clean_validation())
        assert not any(r.risk_type == "long_payment_period" for r in risks)


# ---------------------------------------------------------------------------
# unfavorable_jurisdiction
# ---------------------------------------------------------------------------

class TestUnfavorableJurisdiction:
    def test_employer_court(self):
        clauses = [
            _clause("争议解决", "协商不成的，向甲方所在地有管辖权的人民法院提起诉讼。"),
        ]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "unfavorable_jurisdiction" for r in risks)

    def test_neutral_court_no_risk(self):
        clauses = [
            _clause("争议解决", "协商不成的，向合同签订地有管辖权的人民法院提起诉讼。"),
        ]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert not any(r.risk_type == "unfavorable_jurisdiction" for r in risks)

    def test_no_jurisdiction_clause_no_risk(self):
        clauses = [_clause("其他", "本合同一式两份。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert not any(r.risk_type == "unfavorable_jurisdiction" for r in risks)


# ---------------------------------------------------------------------------
# unclear_acceptance
# ---------------------------------------------------------------------------

class TestUnclearAcceptance:
    def test_no_acceptance_clause(self):
        clauses = [_clause("付款", "分三期支付。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "unclear_acceptance" and "缺少" in r.description for r in risks)

    def test_vague_acceptance(self):
        clauses = [_clause("验收", "乙方完成工作后交付甲方。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "unclear_acceptance" for r in risks)

    def test_clear_acceptance(self):
        risks = identify_risks(_good_fields(), _clean_validation(), _good_clauses())
        assert not any(r.risk_type == "unclear_acceptance" for r in risks)


# ---------------------------------------------------------------------------
# unclear_ip_ownership
# ---------------------------------------------------------------------------

class TestUnclearIpOwnership:
    def test_no_ip_clause(self):
        clauses = [_clause("付款", "分三期支付。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "unclear_ip_ownership" and "缺少" in r.description for r in risks)

    def test_vague_ip(self):
        clauses = [_clause("知识产权", "双方应尊重知识产权。")]
        risks = identify_risks(_good_fields(), _clean_validation(), clauses)
        assert any(r.risk_type == "unclear_ip_ownership" and "不明确" in r.description for r in risks)

    def test_clear_ip(self):
        risks = identify_risks(_good_fields(), _clean_validation(), _good_clauses())
        assert not any(r.risk_type == "unclear_ip_ownership" for r in risks)


# ---------------------------------------------------------------------------
# Validation violation passthrough
# ---------------------------------------------------------------------------

class TestValidationPassthrough:
    def test_validation_violations_become_risks(self):
        validation = ValidationResult(passed=False, violations=[
            RuleViolation(rule_name="required_fields", severity="error",
                          description="缺少必要字段: party_a", field_name="party-a-name"),
        ])
        risks = identify_risks([], validation)
        assert any(r.risk_type == "required_fields" and r.risk_level == "high" for r in risks)

    def test_validation_warnings_are_medium(self):
        validation = ValidationResult(passed=True, violations=[
            RuleViolation(rule_name="date_consistency", severity="warning",
                          description="生效日期晚于终止日期"),
        ])
        risks = identify_risks([], validation)
        assert any(r.risk_type == "date_consistency" and r.risk_level == "medium" for r in risks)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_no_duplicate_risks(self):
        """Same risk type + description should only appear once."""
        # Both validation and field check might flag missing party_a
        fields = [_field("party-a-name", "", confidence=0.3)]
        validation = ValidationResult(passed=False, violations=[
            RuleViolation(rule_name="required_fields", severity="error",
                          description="必要字段值为空: 甲方 (party_a)", field_name="party-a-name"),
        ])
        risks = identify_risks(fields, validation)
        # Should not have duplicates
        type_desc = [(r.risk_type, r.description) for r in risks]
        assert len(type_desc) == len(set(type_desc))


# ---------------------------------------------------------------------------
# Evidence field
# ---------------------------------------------------------------------------

class TestEvidenceField:
    def test_risks_have_evidence(self):
        risks = identify_risks(_good_fields(), _clean_validation(), [])
        for r in risks:
            if r.risk_type in (
                "missing_payment_clause", "missing_breach_clause",
                "missing_dispute_clause", "unclear_acceptance",
                "unclear_ip_ownership",
            ):
                assert r.evidence is not None, f"Missing evidence for {r.risk_type}"

    def test_field_risks_have_evidence(self):
        fields = [_field("contract-amount", "0", "financial")]
        risks = identify_risks(fields, _clean_validation())
        amount_risks = [r for r in risks if r.risk_type == "amount_inconsistency"]
        for r in amount_risks:
            assert r.evidence is not None


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_identify_risks_without_clauses(self):
        """Old signature with just fields + validation should still work."""
        fields = [
            _field("contract-no", "HT-001"),
            _field("party-a-name", "A公司", "party"),
        ]
        validation = ValidationResult(passed=False, violations=[
            RuleViolation(rule_name="required_fields", severity="error",
                          description="缺少必要字段: party_b", field_name="party-b-name"),
        ])
        risks = identify_risks(fields, validation)
        assert len(risks) >= 1
        assert any(r.risk_type == "required_fields" for r in risks)

    def test_empty_input(self):
        risks = identify_risks([], _clean_validation(), [])
        assert isinstance(risks, list)
