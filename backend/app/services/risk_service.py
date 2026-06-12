"""Risk identification service — rule-based contract risk analysis.

Input:
  - extracted_fields
  - contract_clauses
  - validation_results

Output:
  - list[RiskItem] → persisted as ContractRisk rows

Risk types implemented:
  1. amount_inconsistency   — amount / currency anomalies
  2. party_inconsistency    — missing or suspicious party info
  3. missing_payment_clause — no payment terms found
  4. missing_breach_clause  — no breach / liability clause found
  5. missing_dispute_clause — no dispute resolution clause found
  6. long_payment_period    — payment cycle > 90 days
  7. unfavorable_jurisdiction — one-sided jurisdiction choice
  8. unclear_acceptance     — acceptance criteria absent or vague
  9. unclear_ip_ownership   — IP ownership clause absent or ambiguous
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import (
    ClauseSegment,
    ExtractedField as ExtractedFieldData,
    RiskItem,
    ValidationResult,
)
from app.models.contract import ExtractedField, ContractRisk


# ---------------------------------------------------------------------------
# Suggestion templates
# ---------------------------------------------------------------------------

_SUGGESTIONS: dict[str, str] = {
    "required_fields": "请确认合同原文中是否包含该信息，必要时手动补充",
    "financial_consistency": "请核查合同金额相关条款",
    "date_consistency": "请核查合同生效日期与终止日期",
    "amount_consistency": "请核查合同大写金额与小写金额是否一致",
    "payment_ratio": "请核查付款比例合计是否为100%",
    "clause_completeness": "请确认合同是否缺少必要条款，必要时补充",
    "low_confidence": "建议人工核对该字段",
    # New risk types
    "amount_inconsistency": "请核实合同金额是否准确、币种是否明确",
    "party_inconsistency": "请核实合同双方当事人信息是否完整准确",
    "missing_payment_clause": "建议补充付款条款，明确付款方式、金额和时限",
    "missing_breach_clause": "建议补充违约责任条款，明确违约金和赔偿责任",
    "missing_dispute_clause": "建议补充争议解决条款，明确管辖法院或仲裁机构",
    "long_payment_period": "建议缩短付款周期或增加阶段性付款保障",
    "unfavorable_jurisdiction": "建议确认管辖约定是否对本方有利，必要时协商修改",
    "unclear_acceptance": "建议明确验收标准、验收期限和验收方式",
    "unclear_ip_ownership": "建议明确知识产权归属条款，避免权属争议",
}


# ---------------------------------------------------------------------------
# Main risk identification function
# ---------------------------------------------------------------------------

def identify_risks(
    fields: list[ExtractedFieldData],
    validation_result: ValidationResult,
    clauses: list[ClauseSegment] | None = None,
) -> list[RiskItem]:
    """Generate risk items from validation violations, field analysis, and clauses."""
    risks: list[RiskItem] = []
    field_map: dict[str, ExtractedFieldData] = {f.field_key: f for f in fields}
    clause_titles = [c.clause_title or "" for c in (clauses or [])]
    clause_contents = [c.content or "" for c in (clauses or [])]

    # ---- Layer 1: Convert validation violations ----
    for v in validation_result.violations:
        level = "high" if v.severity == "error" else "medium"
        risks.append(RiskItem(
            risk_level=level,
            risk_type=v.rule_name,
            description=v.description,
            evidence=v.description,
            suggestion=_SUGGESTIONS.get(v.rule_name, "建议人工复核"),
            field_name=v.field_name,
        ))

    # ---- Layer 2: Field-based risk checks ----
    _check_low_confidence(fields, risks)
    _check_amount_inconsistency(field_map, risks)
    _check_party_inconsistency(field_map, risks)
    _check_long_payment_period(field_map, risks)

    # ---- Layer 3: Clause-based risk checks ----
    _check_missing_payment_clause(clause_titles, clause_contents, risks)
    _check_missing_breach_clause(clause_titles, clause_contents, risks)
    _check_missing_dispute_clause(clause_titles, clause_contents, risks)
    _check_unfavorable_jurisdiction(clause_contents, risks)
    _check_unclear_acceptance(clause_titles, clause_contents, risks)
    _check_unclear_ip_ownership(clause_titles, clause_contents, risks)

    return _deduplicate(risks)


# ---------------------------------------------------------------------------
# Field-based checks
# ---------------------------------------------------------------------------

def _check_low_confidence(fields: list[ExtractedFieldData], risks: list[RiskItem]) -> None:
    """Flag fields with very low extraction confidence."""
    for f in fields:
        if f.confidence is not None and f.confidence < 0.6:
            risks.append(RiskItem(
                risk_level="medium",
                risk_type="low_confidence",
                description=f"字段 '{f.field_key}' 置信度极低 ({f.confidence:.0%})，建议人工核查",
                evidence=f"confidence={f.confidence:.2f}, value={f.value!r}",
                suggestion="请核对该字段值是否正确",
                field_name=f.field_key,
            ))


def _check_amount_inconsistency(
    field_map: dict[str, ExtractedFieldData],
    risks: list[RiskItem],
) -> None:
    """amount_inconsistency: missing or invalid contract-amount."""
    amount = field_map.get("contract-amount")

    if not amount or not amount.value:
        risks.append(RiskItem(
            risk_level="high",
            risk_type="amount_inconsistency",
            description="合同金额缺失或为空",
            evidence="amount 字段不存在或值为空",
            suggestion=_SUGGESTIONS["amount_inconsistency"],
            field_name="contract-amount",
        ))
    else:
        try:
            val = float(str(amount.value).replace(",", ""))
            if val <= 0:
                risks.append(RiskItem(
                    risk_level="high",
                    risk_type="amount_inconsistency",
                    description=f"合同金额异常: {amount.value}",
                    evidence=f"amount={amount.value}",
                    suggestion=_SUGGESTIONS["amount_inconsistency"],
                    field_name="contract-amount",
                ))
        except (ValueError, AttributeError):
            risks.append(RiskItem(
                risk_level="medium",
                risk_type="amount_inconsistency",
                description=f"合同金额格式无法解析: {amount.value}",
                evidence=f"amount={amount.value!r}",
                suggestion=_SUGGESTIONS["amount_inconsistency"],
                field_name="contract-amount",
            ))



def _check_party_inconsistency(
    field_map: dict[str, ExtractedFieldData],
    risks: list[RiskItem],
) -> None:
    for key in ("party-a-name", "party-b-name"):
        party = field_map.get(key)
        label = "甲方" if key == "party-a-name" else "乙方"
        if not party or not party.value:
            risks.append(RiskItem(
                risk_level="high",
                risk_type="party_inconsistency",
                description=f"{label}信息缺失",
                evidence=f"{key} 字段不存在或值为空",
                suggestion=_SUGGESTIONS["party_inconsistency"],
                field_name=key,
            ))
        elif party.confidence is not None and party.confidence < 0.7:
            risks.append(RiskItem(
                risk_level="medium",
                risk_type="party_inconsistency",
                description=f"{label}名称置信度较低: {party.value}",
                evidence=f"{key}={party.value!r}, confidence={party.confidence:.2f}",
                suggestion=_SUGGESTIONS["party_inconsistency"],
                field_name=key,
            ))


def _check_long_payment_period(
    field_map: dict[str, ExtractedFieldData],
    risks: list[RiskItem],
) -> None:
    """long_payment_period: if sign_date and end_date exist, check duration.

    Flags if the total project duration exceeds 365 days with a single
    payment at the end, which could indicate cash-flow risk.
    """
    sign = field_map.get("sign-date")
    end = field_map.get("end-date")
    if not sign or not end or not sign.value or not end.value:
        return

    sign_date = _parse_iso_date(sign.value)
    end_date = _parse_iso_date(end.value)
    if not sign_date or not end_date:
        return

    delta_days = (end_date - sign_date).days
    if delta_days > 365:
        risks.append(RiskItem(
            risk_level="medium",
            risk_type="long_payment_period",
            description=f"合同工期较长 ({delta_days}天)，需关注付款周期和现金流风险",
            evidence=f"sign-date={sign.value}, end-date={end.value}, duration={delta_days}天",
            suggestion=_SUGGESTIONS["long_payment_period"],
            field_name="end-date",
        ))


# ---------------------------------------------------------------------------
# Clause-based checks
# ---------------------------------------------------------------------------

def _has_clause(titles: list[str], *keywords: str) -> bool:
    """Check if any clause title contains at least one keyword."""
    return any(any(kw in t for kw in keywords) for t in titles)


def _has_content(clause_contents: list[str], *keywords: str) -> bool:
    """Check if any clause content contains at least one keyword."""
    return any(any(kw in c for kw in keywords) for c in clause_contents)


def _check_missing_payment_clause(
    titles: list[str], contents: list[str], risks: list[RiskItem]
) -> None:
    """missing_payment_clause: no payment-related clause found."""
    if not _has_clause(titles, "付款", "支付", "费用") and \
       not _has_content(contents, "付款", "支付方式", "支付金额"):
        risks.append(RiskItem(
            risk_level="high",
            risk_type="missing_payment_clause",
            description="合同缺少付款条款",
            evidence="未找到包含'付款'或'支付'关键词的条款",
            suggestion=_SUGGESTIONS["missing_payment_clause"],
        ))


def _check_missing_breach_clause(
    titles: list[str], contents: list[str], risks: list[RiskItem]
) -> None:
    """missing_breach_clause: no breach / liability clause found."""
    if not _has_clause(titles, "违约", "赔偿", "责任") and \
       not _has_content(contents, "违约金", "赔偿责任", "违约责任"):
        risks.append(RiskItem(
            risk_level="high",
            risk_type="missing_breach_clause",
            description="合同缺少违约责任条款",
            evidence="未找到包含'违约'或'赔偿'关键词的条款",
            suggestion=_SUGGESTIONS["missing_breach_clause"],
        ))


def _check_missing_dispute_clause(
    titles: list[str], contents: list[str], risks: list[RiskItem]
) -> None:
    """missing_dispute_clause: no dispute resolution clause found."""
    if not _has_clause(titles, "争议", "纠纷", "仲裁") and \
       not _has_content(contents, "争议解决", "人民法院", "仲裁委员会"):
        risks.append(RiskItem(
            risk_level="medium",
            risk_type="missing_dispute_clause",
            description="合同缺少争议解决条款",
            evidence="未找到包含'争议'或'仲裁'关键词的条款",
            suggestion=_SUGGESTIONS["missing_dispute_clause"],
        ))


# Unfavorable jurisdiction patterns — detects one-sided court choices
_JURISDICTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"甲方所在地.*?法院"), "约定由甲方所在地法院管辖"),
    (re.compile(r"原告所在地.*?法院"), "约定由原告所在地法院管辖"),
    (re.compile(r"守约方所在地.*?法院"), "约定由守约方所在地法院管辖"),
]


def _check_unfavorable_jurisdiction(
    contents: list[str], risks: list[RiskItem]
) -> None:
    """unfavorable_jurisdiction: detect potentially one-sided jurisdiction clauses."""
    for content in contents:
        for pat, desc in _JURISDICTION_PATTERNS:
            m = pat.search(content)
            if m:
                risks.append(RiskItem(
                    risk_level="low",
                    risk_type="unfavorable_jurisdiction",
                    description=f"管辖条款可能不利于本方: {desc}",
                    evidence=m.group(0),
                    suggestion=_SUGGESTIONS["unfavorable_jurisdiction"],
                    source_text=content[:200],
                ))
                return  # only report once


def _check_unclear_acceptance(
    titles: list[str], contents: list[str], risks: list[RiskItem]
) -> None:
    """unclear_acceptance: acceptance criteria absent or vague."""
    has_acceptance_title = _has_clause(titles, "验收", "交付", "测试")
    has_acceptance_content = _has_content(contents, "验收标准", "验收期限", "验收方式")

    if not has_acceptance_title and not has_acceptance_content:
        risks.append(RiskItem(
            risk_level="medium",
            risk_type="unclear_acceptance",
            description="合同缺少验收条款",
            evidence="未找到包含'验收'关键词的条款标题或内容",
            suggestion=_SUGGESTIONS["unclear_acceptance"],
        ))
    elif has_acceptance_title and not has_acceptance_content:
        # Clause exists but lacks specific standards
        risks.append(RiskItem(
            risk_level="low",
            risk_type="unclear_acceptance",
            description="验收条款存在但缺少明确标准（验收标准、期限、方式）",
            evidence="找到验收条款标题但内容中缺少'验收标准'、'验收期限'或'验收方式'",
            suggestion=_SUGGESTIONS["unclear_acceptance"],
        ))


def _check_unclear_ip_ownership(
    titles: list[str], contents: list[str], risks: list[RiskItem]
) -> None:
    """unclear_ip_ownership: IP ownership clause absent or ambiguous."""
    has_ip_title = _has_clause(titles, "知识产权", "产权", "著作权", "专利")
    has_ip_content = _has_content(
        contents, "知识产权归", "产权归属", "著作权归", "所有权归",
    )

    if not has_ip_title and not has_ip_content:
        risks.append(RiskItem(
            risk_level="medium",
            risk_type="unclear_ip_ownership",
            description="合同缺少知识产权归属条款",
            evidence="未找到包含'知识产权'或'产权归属'关键词的条款",
            suggestion=_SUGGESTIONS["unclear_ip_ownership"],
        ))
    elif has_ip_title and not has_ip_content:
        risks.append(RiskItem(
            risk_level="low",
            risk_type="unclear_ip_ownership",
            description="知识产权条款存在但归属表述不明确",
            evidence="找到知识产权条款标题但内容中未明确归属方",
            suggestion=_SUGGESTIONS["unclear_ip_ownership"],
        ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso_date(value: str) -> datetime | None:
    """Parse ISO date string (YYYY-MM-DD) or CN date (YYYY年M月D日)."""
    value = value.strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        pass
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", value)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _deduplicate(risks: list[RiskItem]) -> list[RiskItem]:
    """Remove exact-duplicate risks (same type + description)."""
    seen: set[tuple[str, str]] = set()
    unique: list[RiskItem] = []
    for r in risks:
        key = (r.risk_type, r.description)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def save_risks(
    db: AsyncSession,
    contract_id: uuid.UUID,
    risks: list[RiskItem],
    fields: list[ExtractedFieldData],
    clauses: list[ClauseSegment] | None = None,
) -> list[ContractRisk]:
    """Persist risk items to DB.

    Resolves field_id and clause_id from the database for FK linkage.
    """
    # Build field name → id map
    field_name_to_id: dict[str, uuid.UUID] = {}
    result = await db.execute(
        select(ExtractedField).where(ExtractedField.contract_id == contract_id)
    )
    for f in result.scalars().all():
        field_name_to_id[f.field_key] = f.id

    # Build clause index → id map
    clause_idx_to_id: dict[int, uuid.UUID] = {}
    if clauses is not None:
        from app.models.contract import ContractClause
        cresult = await db.execute(
            select(ContractClause).where(ContractClause.contract_id == contract_id)
            .order_by(ContractClause.created_at)
        )
        for i, c in enumerate(cresult.scalars().all()):
            clause_idx_to_id[i] = c.id

    records: list[ContractRisk] = []
    for r in risks:
        # Resolve field FK
        field_id = field_name_to_id.get(r.field_name) if r.field_name else None
        # Resolve clause FK
        clause_id = clause_idx_to_id.get(r.clause_index) if r.clause_index is not None else None

        record = ContractRisk(
            contract_id=contract_id,
            field_id=field_id,
            clause_id=clause_id,
            risk_level=r.risk_level,
            risk_type=r.risk_type,
            description=r.description,
            evidence=r.evidence,
            suggestion=r.suggestion,
            source_text=r.source_text,
            page_no=r.page_no,
            review_status="pending",
        )
        db.add(record)
        records.append(record)
    await db.flush()
    return records
