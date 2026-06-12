"""Tests for service layer."""

import pytest

from app.extraction.base import ExtractedField, RiskItem, ValidationResult, RuleViolation
from app.services.clause_service import _classify_clause_type
from app.services.rule_validation_service import validate_fields
from app.services.risk_service import identify_risks


def test_classify_clause_type():
    assert _classify_clause_type("第二条 付款方式") == "payment"
    assert _classify_clause_type("第三条 违约责任") == "breach"
    assert _classify_clause_type("第四条 保密条款") == "confidentiality"
    assert _classify_clause_type("第五条 争议解决") == "dispute"
    assert _classify_clause_type("第九条 合同期限") == "term"
    assert _classify_clause_type("第一条 项目概述") == "project"


def test_validate_fields_all_required():
    """Should pass when all required fields are present."""
    fields = [
        ExtractedField(field_key="contract-no", field_category="basic", value="HT-001",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="party-a-name", field_category="party", value="A公司",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="party-b-name", field_category="party", value="B公司",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="sign-date", field_category="date", value="2024-01-15",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="contract-amount", field_category="financial", value="100000",
                       source_text="test", page_no=1, confidence=0.9),
    ]
    result = validate_fields(fields)
    # No required_field violations
    assert not any(v.rule_name == "required_fields" for v in result.violations)


def test_validate_fields_missing_required():
    """Should fail when required fields are missing."""
    fields = [
        ExtractedField(field_key="party-a-name", field_category="party", value="A公司",
                       source_text="test", page_no=1, confidence=0.9),
    ]
    result = validate_fields(fields)
    assert not result.passed
    missing = [v for v in result.violations if v.rule_name == "required_fields"]
    assert len(missing) >= 1  # At least party-b-name missing


def test_identify_risks():
    fields = [
        ExtractedField(field_key="contract-no", field_category="basic", value="HT-001",
                       source_text="test", page_no=1, confidence=0.9),
    ]
    validation = ValidationResult(
        passed=True,
        violations=[RuleViolation(
            rule_name="required_fields",
            severity="error",
            description="缺少必要字段: party_a",
            field_name="party-a-name",
        )],
    )
    risks = identify_risks(fields, validation)
    assert len(risks) >= 1
    assert any(r.risk_type == "required_fields" for r in risks)
