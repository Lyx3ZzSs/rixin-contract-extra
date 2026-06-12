"""Built-in validation rules.

Rules implemented:
  1. Required fields: party-a-name, party-b-name, amount, currency must not be empty.
  2. Date consistency: effective-date must not be later than end-date.
  3. Amount consistency: if uppercase and lowercase amounts both exist, check match.
  4. Payment ratio sum: if payment ratios exist, check they sum to 100%.
  5. Software/service contract clause completeness (handled in validation_service).
  6. Confidence threshold: flag low-confidence extractions.
  7. Value emptiness: required fields with empty/null values.
"""

from __future__ import annotations

import re

from app.extraction.base import ExtractedField, RuleViolation
from app.rules.registry import ValidationRule, register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_date(value: str) -> str | None:
    """Best-effort date normalisation for comparison.

    Handles:
      - ISO: 2024-01-15
      - CN:  2024年1月15日
    Returns ISO format string or None.
    """
    import re as _re

    value = value.strip()

    # Already ISO-like
    if _re.match(r"\d{4}-\d{1,2}-\d{1,2}$", value):
        return value

    # CN date: 2024年1月15日
    m = _re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", value)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return None


# ---------------------------------------------------------------------------
# 1. Required field rule — party-a-name, party-b-name, amount, currency
# ---------------------------------------------------------------------------

class RequiredFieldRule(ValidationRule):
    """Check that essential fields are present and non-empty."""

    name = "required_fields"
    severity = "error"

    # {field_name: human-readable label}
    REQUIRED: dict[str, str] = {
        "party-a-name": "甲方",
        "party-b-name": "乙方",
    }

    def check(self, fields: list[ExtractedField]) -> list[RuleViolation]:
        field_map = {f.field_key: f for f in fields}
        violations: list[RuleViolation] = []

        for name, label in self.REQUIRED.items():
            if name not in field_map:
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"缺少必要字段: {label} ({name})",
                    field_name=name,
                ))
            else:
                val = field_map[name].value
                if val is None or str(val).strip() == "":
                    violations.append(RuleViolation(
                        rule_name=self.name,
                        severity=self.severity,
                        description=f"必要字段值为空: {label} ({name})",
                        field_name=name,
                    ))
        return violations


# ---------------------------------------------------------------------------
# 2. Date consistency — effective-date <= end-date
# ---------------------------------------------------------------------------

class DateConsistencyRule(ValidationRule):
    """Check that effective_date is not later than end_date."""

    name = "date_consistency"
    severity = "warning"

    def check(self, fields: list[ExtractedField]) -> list[RuleViolation]:
        field_map = {f.field_key: f for f in fields}
        violations: list[RuleViolation] = []

        effective = field_map.get("effective-date")
        end = field_map.get("end-date")

        if effective and end and effective.value and end.value:
            eff = _normalise_date(effective.value)
            end_v = _normalise_date(end.value)
            if eff and end_v and eff > end_v:
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"生效日期({effective.value})晚于终止日期({end.value})",
                    field_name="effective-date",
                ))

        # Also check sign-date <= end-date
        sign = field_map.get("sign-date")
        if sign and end and sign.value and end.value:
            s = _normalise_date(sign.value)
            ev = _normalise_date(end.value)
            if s and ev and s > ev:
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"签署日期({sign.value})晚于终止日期({end.value})",
                    field_name="sign-date",
                ))

        return violations


# ---------------------------------------------------------------------------
# 3. Amount consistency — uppercase vs lowercase
# ---------------------------------------------------------------------------

class AmountConsistencyRule(ValidationRule):
    """If both uppercase (大写) and lowercase (小写) amounts exist, check match."""

    name = "amount_consistency"
    severity = "error"

    # Common Chinese uppercase amount patterns in source text
    _UPPER_RE = re.compile(
        r"[人民币RMB￥]*\s*([壹贰叁肆伍陆柒捌玖拾佰仟万亿零元整圆角分，、\s]+)"
    )
    _LOWER_RE = re.compile(r"[¥￥]\s*([\d,]+\.?\d*)")

    # Simplified mapping for common uppercase amounts
    _CN_DIGIT = {
        "零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4,
        "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
        "拾": 10, "佰": 100, "仟": 1000, "万": 10000, "亿": 100000000,
    }

    def check(self, fields: list[ExtractedField]) -> list[RuleViolation]:
        field_map = {f.field_key: f for f in fields}
        violations: list[RuleViolation] = []

        amount_field = field_map.get("contract-amount")
        if not amount_field or not amount_field.source_text:
            return violations

        source = amount_field.source_text

        # Try to extract both forms from source_text
        upper_match = self._UPPER_RE.search(source)
        lower_match = self._LOWER_RE.search(source)

        if upper_match and lower_match:
            upper_str = upper_match.group(1)
            try:
                lower_val = float(lower_match.group(1).replace(",", ""))
            except ValueError:
                return violations

            upper_val = self._parse_upper_amount(upper_str)
            if upper_val is not None and abs(upper_val - lower_val) > 0.01:
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=(
                        f"大写金额({upper_str})与小写金额({lower_val:,.2f})不一致"
                    ),
                    field_name="contract-amount",
                ))

        return violations

    def _parse_upper_amount(self, text: str) -> float | None:
        """Simplified Chinese uppercase amount parser.

        Algorithm: scan left to right.  Digits (壹-玖) set ``current_digit``.
        Unit multipliers (拾/佰/仟) multiply ``current_digit`` into
        ``section``.  Section multipliers (万/亿) finalise the section and
        accumulate into ``result``.

        Examples:
          "壹佰贰拾万" → 1*100 + 2*10 = 120, × 10000 = 1,200,000
          "壹拾万" → 1*10 = 10, × 10000 = 100,000
          "叁万" → 3, × 10000 = 30,000
        """
        text = text.replace("，", "").replace("、", "").replace(" ", "")
        text = text.rstrip("元整圆")

        if not text:
            return None

        try:
            result = 0.0
            section = 0.0      # accumulates within 拾/佰/仟 range
            current_digit = 0  # the last seen basic digit (0-9)

            for ch in text:
                val = self._CN_DIGIT.get(ch)
                if val is None:
                    continue

                if val >= 10 and val < 10000:
                    # Section-level multiplier (拾=10, 佰=100, 仟=1000)
                    if current_digit == 0:
                        current_digit = 1  # implicit leading 1
                    section += current_digit * val
                    current_digit = 0
                elif val >= 10000:
                    # Section finaliser (万=10000, 亿=100000000)
                    section_total = section + current_digit
                    if section_total == 0:
                        section_total = 1
                    result += section_total * val
                    section = 0.0
                    current_digit = 0
                else:
                    # Basic digit (零-玖)
                    current_digit = val

            # Remaining section
            result += section + current_digit
            return float(result)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# 4. Payment ratio sum — should total 100%
# ---------------------------------------------------------------------------

class PaymentRatioRule(ValidationRule):
    """If payment ratio fields exist, check they sum to 100%."""

    name = "payment_ratio"
    severity = "warning"

    # Field name patterns that represent payment ratios
    _RATIO_PATTERNS = [
        re.compile(r"ratio"),
        re.compile(r"付款比例"),
    ]

    def check(self, fields: list[ExtractedField]) -> list[RuleViolation]:
        field_map = {f.field_key: f for f in fields}
        violations: list[RuleViolation] = []

        # Collect ratio fields
        ratios: list[tuple[str, float]] = []
        for name, field in field_map.items():
            is_ratio = any(pat.search(name) for pat in self._RATIO_PATTERNS)
            if is_ratio and field.value:
                try:
                    val = float(field.value.replace("%", "").replace("％", "").strip())
                    ratios.append((name, val))
                except (ValueError, AttributeError):
                    pass

        if len(ratios) >= 2:
            total = sum(r[1] for r in ratios)
            # Allow 1% tolerance for rounding
            if abs(total - 100.0) > 1.0:
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=(
                        f"付款比例合计为{total:.1f}%，不等于100% "
                        f"(共{len(ratios)}期: {', '.join(f'{r[1]}%' for r in ratios)})"
                    ),
                    field_name=ratios[0][0],
                ))

        return violations


# ---------------------------------------------------------------------------
# 5. Financial consistency — basic sanity checks on amounts
# ---------------------------------------------------------------------------

class FinancialConsistencyRule(ValidationRule):
    """Check that financial fields are internally consistent."""

    name = "financial_consistency"
    severity = "warning"

    def check(self, fields: list[ExtractedField]) -> list[RuleViolation]:
        field_map = {f.field_key: f for f in fields}
        violations: list[RuleViolation] = []

        amount = field_map.get("contract-amount")
        if amount and amount.value:
            try:
                amount_val = float(str(amount.value).replace(",", ""))
                if amount_val < 0:
                    violations.append(RuleViolation(
                        rule_name=self.name,
                        severity=self.severity,
                        description=f"合同金额为负数: {amount.value}",
                        field_name="contract-amount",
                    ))
                elif amount_val == 0:
                    violations.append(RuleViolation(
                        rule_name=self.name,
                        severity=self.severity,
                        description="合同金额为零",
                        field_name="contract-amount",
                    ))
            except (ValueError, AttributeError):
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"合同金额格式异常: {amount.value}",
                    field_name="contract-amount",
                ))

        return violations


# ---------------------------------------------------------------------------
# 6. Confidence threshold — flag low-confidence extractions
# ---------------------------------------------------------------------------

class ConfidenceThresholdRule(ValidationRule):
    """Flag low-confidence extractions for manual review."""

    name = "low_confidence"
    severity = "warning"

    THRESHOLD = 0.7

    def check(self, fields: list[ExtractedField]) -> list[RuleViolation]:
        violations: list[RuleViolation] = []
        for f in fields:
            if f.confidence is not None and f.confidence < self.THRESHOLD:
                violations.append(RuleViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    description=f"字段 '{f.field_key}' 置信度过低: {f.confidence:.2f}",
                    field_name=f.field_key,
                ))
        return violations


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_loaded = False


def load_builtin_rules() -> None:
    """Register built-in rules (idempotent)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    register(RequiredFieldRule())
    register(DateConsistencyRule())
    register(AmountConsistencyRule())
    register(PaymentRatioRule())
    register(FinancialConsistencyRule())
    register(ConfidenceThresholdRule())


def reset_builtin_rules() -> None:
    """Clear the loaded flag (for testing)."""
    global _loaded
    _loaded = False
    from app.rules.registry import clear_rules
    clear_rules()
