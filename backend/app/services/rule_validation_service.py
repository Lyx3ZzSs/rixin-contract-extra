"""Rule-based validation service.

Orchestrates all registered validation rules and returns a
``ValidationResult`` that the risk service can consume.  Validation
failures never interrupt the pipeline — they become violations that
feed into risk generation.

Public API:
  - ``validate_fields(fields)``              — run field-only rules
  - ``validate_contract(fields, contract_type, clause_titles)`` — full check
"""

from __future__ import annotations

import re
from typing import Sequence

from app.extraction.base import ExtractedField, RuleViolation, ValidationResult
from app.rules.builtin import load_builtin_rules
from app.rules.registry import get_all_rules


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_fields(fields: list[ExtractedField]) -> ValidationResult:
    """Run all registered validation rules against extracted fields."""
    load_builtin_rules()
    rules = get_all_rules()

    all_violations: list[RuleViolation] = []
    for rule in rules:
        violations = rule.check(fields)
        all_violations.extend(violations)

    has_errors = any(v.severity == "error" for v in all_violations)
    return ValidationResult(passed=not has_errors, violations=all_violations)


def validate_contract(
    fields: list[ExtractedField],
    contract_type: str | None = None,
    clause_titles: list[str] | None = None,
) -> ValidationResult:
    """Run all rules including contract-type-specific clause checks.

    This is the preferred entry point from the pipeline, which has access
    to the contract type and clause titles.
    """
    load_builtin_rules()
    rules = get_all_rules()

    all_violations: list[RuleViolation] = []
    for rule in rules:
        violations = rule.check(fields)
        all_violations.extend(violations)

    # Run clause-completeness check if contract type is known
    if contract_type and clause_titles is not None:
        clause_rule = ClauseCompletenessRule(contract_type)
        all_violations.extend(clause_rule.check_clauses(clause_titles))

    has_errors = any(v.severity == "error" for v in all_violations)
    return ValidationResult(passed=not has_errors, violations=all_violations)


# ---------------------------------------------------------------------------
# Clause completeness — instantiated per contract type, not in registry
# ---------------------------------------------------------------------------

# Mapping: contract_type -> { clause_kind: [keyword_patterns] }
_CLAUSE_REQUIREMENTS: dict[str, dict[str, list[str]]] = {
    "service": {
        "payment": ["付款", "支付"],
        "delivery": ["交付"],
        "acceptance": ["验收"],
        "intellectual_property": ["知识产权", "产权"],
        "breach": ["违约"],
        "dispute": ["争议"],
    },
}

# "development" shares the same clause requirements as "service"
_CLAUSE_REQUIREMENTS["development"] = _CLAUSE_REQUIREMENTS["service"]


class ClauseCompletenessRule:
    """Check that a contract of a given type includes all required clauses."""

    name = "clause_completeness"
    severity = "warning"

    def __init__(self, contract_type: str) -> None:
        self.contract_type = contract_type

    def check_clauses(self, clause_titles: list[str]) -> list[RuleViolation]:
        requirements = _CLAUSE_REQUIREMENTS.get(self.contract_type)
        if not requirements:
            return []

        violations: list[RuleViolation] = []
        for clause_kind, keywords in requirements.items():
            found = any(
                any(kw in title for kw in keywords)
                for title in clause_titles
            )
            if not found:
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=(
                        f"软件开发类合同缺少必要条款: {clause_kind}"
                        if self.contract_type in ("service", "development")
                        else f"{self.contract_type}类合同缺少必要条款: {clause_kind}"
                    ),
                    field_name=None,
                ))
        return violations
