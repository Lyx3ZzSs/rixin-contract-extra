"""Rule registry for contract validation."""

from __future__ import annotations

from app.extraction.base import ExtractedField, RuleViolation


class ValidationRule:
    name: str = ""
    severity: str = "warning"  # error / warning

    def check(self, fields: list[ExtractedField]) -> list[RuleViolation]:
        raise NotImplementedError


_registry: list[ValidationRule] = []


def register(rule: ValidationRule) -> None:
    _registry.append(rule)


def get_all_rules() -> list[ValidationRule]:
    return list(_registry)


def clear_rules() -> None:
    _registry.clear()
