"""Tests for service layer."""

import pytest

from app.extraction.base import ExtractedField
from app.services.clause_service import _classify_clause_type
from app.services.rule_validation_service import validate_fields


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
        ExtractedField(field_key="contract-no", value="HT-001",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="party-a-name", value="A公司",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="party-b-name", value="B公司",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="sign-date", value="2024-01-15",
                       source_text="test", page_no=1, confidence=0.9),
        ExtractedField(field_key="contract-amount", value="100000",
                       source_text="test", page_no=1, confidence=0.9),
    ]
    result = validate_fields(fields)
    # No required_field violations
    assert not any(v.rule_name == "required_fields" for v in result.violations)


def test_validate_fields_missing_required():
    """Should fail when required fields are missing."""
    fields = [
        ExtractedField(field_key="party-a-name", value="A公司",
                       source_text="test", page_no=1, confidence=0.9),
    ]
    result = validate_fields(fields)
    assert not result.passed
    missing = [v for v in result.violations if v.rule_name == "required_fields"]
    assert len(missing) >= 1  # At least party-b-name missing
