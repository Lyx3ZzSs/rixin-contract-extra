# Phase 2 / Plan 2: 智能能力接线（classify + rule + clause）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把三个已写好但从未接入的模块（合同类型分类、规则校验、条款拆分）接入抽取流水线，让规则违规落库可复核、分类驱动字段集、条款真正入库可复核。

**Architecture:** 新增 `rule_violations` 表 + `field_definitions.contract_type` 列（1 个 Alembic 迁移）→ 服务层 building blocks（按类型加载字段、违规落库/重算）→ 抽取流水线改为「classify 门控 + Track A(rule) / Track B(clause) 双轨并发」→ API 暴露违规（ContractDetail 嵌入 + 忽略端点 + 复核触发重算）。

**Tech Stack:** Python 3.12 / FastAPI(async) / SQLAlchemy 2.0 async / aiosqlite / Alembic / pytest。

**对应 spec：** `docs/superpowers/specs/2026-06-24-phase2-feature-completion-design.md` §5/§6/§8。

## Scope refinements discovered during planning (both reduce work, spec-consistent)
- `FieldDetail.bbox`（schemas/contract.py:64）与 `ContractDetail.clauses`（:106）**已存在**——本计划不动它们。
- `page_dimensions` **不做**——前端 `<img>` 自带 naturalWidth/Height 供 bbox 缩放，无需后端返回页宽高。

## Global Constraints
- bbox 保持**原始像素**（Plan 1 已确立），本计划不碰 bbox。
- 规则校验**非阻断**：违规作为附加结果落库，绝不中断抽取任务（既有 `validate_*` 契约不变）。
- 零处 schema 迁移以外的 DB 变更：仅新增 `rule_violations` 表 + `field_definitions.contract_type` 列。
- classify 权威：类型来自独立 `LLMService.classify_contract_type`（失败返回 `("unknown", 0.0)`，graceful）；extract 内联类型仅作回退。
- 复核改值不覆盖原始 `value`（写 `reviewed_value`，既有契约）。
- 测试沿用 `backend/tests/`；评测标记 `@pytest.mark.eval`。
- kill-switch：`enable_rule_validation` / `enable_clause_split`（默认 True）。
- 提交规范：conventional commits，每任务一次提交。

## File Structure

| 文件 | 职责 | 任务 |
|---|---|---|
| `backend/alembic/versions/7d8e9f0a1b2c_add_rule_violations_and_field_contract_type.py` | **新建**迁移：rule_violations 表 + field_definitions.contract_type 列 | T1 |
| `backend/app/models/rule_violation.py` | **新建** RuleViolation ORM | T1 |
| `backend/app/models/contract.py` | Contract 加 `violations` relationship | T1 |
| `backend/app/models/field_definition.py` | 加 `contract_type` 列 | T1 |
| `backend/app/models/__init__.py` | 注册 RuleViolation | T1 |
| `backend/app/config.py` | 加 `enable_rule_validation` / `enable_clause_split` | T1 |
| `backend/app/services/extraction_service.py` | `load_field_definitions(db, contract_type=None)` | T2 |
| `backend/app/services/violation_service.py` | **新建** `save_violations` / `recompute_violations` | T2 |
| `backend/app/services/pipeline.py` | `_run_extraction_pipeline_inner` 双轨改造 | T3 |
| `backend/app/schemas/contract.py` | `RuleViolationDetail` + `ContractDetail.violations` | T4 |
| `backend/app/api/contract.py` | `_load_contract_detail` selectinload violations | T4 |
| `backend/app/api/review.py` | `PATCH /violations/{vid}` + field-review 重算 hook | T4 |
| `backend/tests/...` | 各任务测试 | T1–T4 |

---

## Task 1: 迁移 + RuleViolation 模型 + field_definitions.contract_type

**Files:**
- Create: `backend/alembic/versions/7d8e9f0a1b2c_add_rule_violations_and_field_contract_type.py`
- Create: `backend/app/models/rule_violation.py`
- Modify: `backend/app/models/contract.py`（Contract relationship 块，:53-55 之后）
- Modify: `backend/app/models/field_definition.py`（:22 `is_active` 之后）
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/config.py`（:47 `allowed_origins` 之后）
- Test: `backend/tests/test_rule_violation_model.py`

**Interfaces:**
- Produces: `RuleViolation` ORM（表 `rule_violations`）；`Contract.violations` relationship；`FieldDefinition.contract_type: str|None`；config `enable_rule_validation`/`enable_clause_split`；迁移 revision `7d8e9f0a1b2c`（down_revision `0a1b2c3d4e5f`）。
- Consumes: `Base`, `JSONType`（database.py）。

- [ ] **Step 1: 写失败测试** `backend/tests/test_rule_violation_model.py`

```python
"""Tests for RuleViolation model + field_definitions.contract_type (T1)."""
import uuid

import pytest
from sqlalchemy import select

from app.models.rule_violation import RuleViolation
from app.models.field_definition import FieldDefinition


@pytest.mark.asyncio
async def test_rule_violation_roundtrip():
    """A RuleViolation row can be created and queried with all fields."""
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    async with test_session_factory() as db:
        v = RuleViolation(
            contract_id=cid, field_key="party-a-name", rule_key="required_fields",
            severity="error", message="必要字段值为空: 甲方", status="active",
        )
        db.add(v)
        await db.flush()
        vid = v.id
        await db.commit()

    async with test_session_factory() as db:
        row = (await db.execute(select(RuleViolation).where(RuleViolation.id == vid))).scalar_one()
        assert row.rule_key == "required_fields"
        assert row.severity == "error"
        assert row.status == "active"
        assert row.field_key == "party-a-name"
        assert row.contract_id == cid


@pytest.mark.asyncio
async def test_rule_violation_contract_level_null_field_key():
    """Contract-level violations use field_key=NULL (e.g. payment ratio sum)."""
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    async with test_session_factory() as db:
        db.add(RuleViolation(
            contract_id=cid, field_key=None, rule_key="payment_ratio",
            severity="warning", message="付款比例合计 90%", status="active",
        ))
        await db.commit()
    async with test_session_factory() as db:
        row = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == cid))).scalar_one()
        assert row.field_key is None


@pytest.mark.asyncio
async def test_field_definition_has_contract_type_column():
    """FieldDefinition.contract_type exists and defaults to None (通用)."""
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        fd = FieldDefinition(
            field_key="test-key-t1", field_name="测试", description="",
            value_type="string", required=False, sort_order=0, is_active=True,
        )
        db.add(fd)
        await db.commit()
    async with test_session_factory() as db:
        row = (await db.execute(select(FieldDefinition).where(FieldDefinition.field_key == "test-key-t1"))).scalar_one()
        assert row.contract_type is None
```

> **Pattern (applies to all tests in this plan):** `test_session_factory` is a module object in `tests/conftest.py`, NOT a pytest fixture. Import it INSIDE each test (`from tests.conftest import test_session_factory`) and use `async with test_session_factory() as db:`. The autouse `setup_db` fixture creates all tables (via `Base.metadata.create_all`) per test.

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_rule_violation_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.rule_violation'` (table also absent).

- [ ] **Step 3: 写最小实现**

`backend/app/models/rule_violation.py`（新建）：

```python
"""RuleViolation — persisted rule-validation findings per contract.

Populated by the extraction pipeline (validate_contract) and refreshed on
field review. status='active' by default; reviewers can set 'ignored'.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, JSONType


class RuleViolation(Base):
    __tablename__ = "rule_violations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True,
    )
    field_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rule_key: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    detail: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime)
    ignored_by: Mapped[str | None] = mapped_column(String(100))

    contract: Mapped["Contract"] = relationship(back_populates="violations")

    __table_args__ = (
        Index("ix_rv_contract_rule_field", "contract_id", "rule_key", "field_key"),
    )
```

`backend/app/models/contract.py` — 在 `review_records` relationship（:53-55）之后追加：

```python
    violations: Mapped[list["RuleViolation"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
```

`backend/app/models/field_definition.py` — 在 `is_active`（:22）之后追加：

```python
    # contract_type: NULL = 通用（所有类型适用）; 否则仅该类型合同加载此字段。
    contract_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
```

`backend/app/models/__init__.py` — 加 import + `__all__` 条目：

```python
from app.models.rule_violation import RuleViolation
```
（并在 `__all__` 列表加 `"RuleViolation"`。）

`backend/app/config.py` — 在 `allowed_origins`（:47）之后追加：

```python
    # Phase 2 kill-switches: gate rule validation and clause splitting in the
    # extraction pipeline (default on; set False to disable for rollout/rollback).
    enable_rule_validation: bool = True
    enable_clause_split: bool = True
```

`backend/alembic/versions/7d8e9f0a1b2c_add_rule_violations_and_field_contract_type.py`（新建，down_revision 接当前 head `0a1b2c3d4e5f`）：

```python
"""add_rule_violations_and_field_contract_type

Revision ID: 7d8e9f0a1b2c
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-25 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7d8e9f0a1b2c'
down_revision: Union[str, None] = '0a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rule_violations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('contract_id', sa.Uuid(), nullable=False),
        sa.Column('field_key', sa.String(length=100), nullable=True),
        sa.Column('rule_key', sa.String(length=100), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False, server_default='warning'),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('ignored_at', sa.DateTime(), nullable=True),
        sa.Column('ignored_by', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['contract_id'], ['contracts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rule_violations_contract_id', 'rule_violations', ['contract_id'])
    op.create_index('ix_rv_contract_rule_field', 'rule_violations', ['contract_id', 'rule_key', 'field_key'])

    op.add_column(
        'field_definitions',
        sa.Column('contract_type', sa.String(length=50), nullable=True),
    )
    op.create_index('ix_field_definitions_contract_type', 'field_definitions', ['contract_type'])


def downgrade() -> None:
    op.drop_index('ix_field_definitions_contract_type', table_name='field_definitions')
    op.drop_column('field_definitions', 'contract_type')

    op.drop_index('ix_rv_contract_rule_field', table_name='rule_violations')
    op.drop_index('ix_rule_violations_contract_id', table_name='rule_violations')
    op.drop_table('rule_violations')
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_rule_violation_model.py -v`
Expected: PASS (3 passed). （测试 DB 用 `Base.metadata.create_all` 建表，新模型自动建表。）

- [ ] **Step 5: Alembic 迁移冒烟（手动验证 upgrade/downgrade）**

Run:
```bash
cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend
env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m alembic upgrade head
env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m alembic downgrade -1
env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m alembic upgrade head
```
Expected: 三个命令均成功（upgrade 到 7d8e9f0a1b2c、downgrade 回 0a1b2c3d4e5f、再 upgrade）。注意：这会操作真实 `data/contract_extract.db`——若库已有数据可接受（只加表/列，不破坏现有）。

- [ ] **Step 6: 提交**

```bash
git add backend/alembic/versions/7d8e9f0a1b2c_add_rule_violations_and_field_contract_type.py backend/app/models/rule_violation.py backend/app/models/contract.py backend/app/models/field_definition.py backend/app/models/__init__.py backend/app/config.py backend/tests/test_rule_violation_model.py
git commit -m "feat(models): add RuleViolation + field_definitions.contract_type migration"
```

---

## Task 2: 服务层 building blocks（按类型加载字段 + 违规落库/重算）

**Files:**
- Modify: `backend/app/services/extraction_service.py`（`load_field_definitions`，:57-64）
- Create: `backend/app/services/violation_service.py`
- Test: `backend/tests/test_violation_service.py`

**Interfaces:**
- Produces: `load_field_definitions(db, contract_type=None) -> list[FieldDefinition]`；`violation_service.save_violations(db, contract_id, result: ValidationResult) -> int`；`violation_service.recompute_violations(db, contract_id) -> int`。
- Consumes: T1 `RuleViolation` ORM；`rule_validation_service.validate_contract`；`ExtractedField` ORM；`app.extraction.base.ExtractedField`、`ValidationResult`。

- [ ] **Step 1: 写失败测试** `backend/tests/test_violation_service.py`

```python
"""Tests for violation_service (save/recompute) + type-driven field loading (T2)."""
import uuid

import pytest
from sqlalchemy import select

from app.extraction.base import ExtractedField, RuleViolation as RVData, ValidationResult
from app.models.rule_violation import RuleViolation
from app.models.field_definition import FieldDefinition
from app.services import violation_service


def _fields_with_empty_party_a() -> list[ExtractedField]:
    return [
        ExtractedField(field_key="party-a-name", field_name="甲方", value=None, confidence=0.9),
        ExtractedField(field_key="party-b-name", field_name="乙方", value="某公司", confidence=0.9),
    ]


@pytest.mark.asyncio
async def test_save_violations_persists_active_rows():
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    vr = ValidationResult(passed=False, violations=[
        RVData(rule_name="required_fields", severity="error", description="必要字段值为空: 甲方", field_name="party-a-name"),
    ])
    async with test_session_factory() as db:
        n = await violation_service.save_violations(db, cid, vr)
        await db.commit()
    assert n == 1
    async with test_session_factory() as db:
        rows = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == cid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].rule_key == "required_fields"
    assert rows[0].severity == "error"
    assert rows[0].field_key == "party-a-name"
    assert rows[0].status == "active"


@pytest.mark.asyncio
async def test_save_violations_clears_old_active_but_keeps_ignored():
    """Re-save deletes prior 'active' rows but preserves 'ignored' (recompute semantics)."""
    from tests.conftest import test_session_factory
    cid = uuid.uuid4()
    async with test_session_factory() as db:
        db.add(RuleViolation(contract_id=cid, field_key="x", rule_key="r1", severity="warning", message="old active", status="active"))
        db.add(RuleViolation(contract_id=cid, field_key="y", rule_key="r2", severity="warning", message="ignored stays", status="ignored"))
        await db.commit()

    vr = ValidationResult(passed=True, violations=[])  # recompute finds nothing new
    async with test_session_factory() as db:
        await violation_service.save_violations(db, cid, vr)
        await db.commit()

    async with test_session_factory() as db:
        rows = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == cid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ignored"  # active one cleared, ignored preserved


@pytest.mark.asyncio
async def test_load_field_definitions_filters_by_contract_type():
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        db.add(FieldDefinition(field_key="common-1", field_name="通用1", description="", is_active=True, sort_order=1, contract_type=None))
        db.add(FieldDefinition(field_key="lease-1", field_name="租赁专属", description="", is_active=True, sort_order=2, contract_type="lease"))
        db.add(FieldDefinition(field_key="service-1", field_name="服务专属", description="", is_active=True, sort_order=3, contract_type="service"))
        await db.commit()

    from app.services.extraction_service import load_field_definitions
    async with test_session_factory() as db:
        lease_fields = await load_field_definitions(db, contract_type="lease")
        keys = {f.field_key for f in lease_fields}
    assert keys == {"common-1", "lease-1"}  # 通用 + 该类型专属，不含 service
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_violation_service.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.violation_service` / `load_field_definitions` signature mismatch.

- [ ] **Step 3: 写最小实现**

`backend/app/services/extraction_service.py` — 替换 `load_field_definitions`（:57-64）为：

```python
from sqlalchemy import or_

async def load_field_definitions(
    db: AsyncSession, contract_type: str | None = None,
) -> list[FieldDefinition]:
    """Load active field definitions, optionally filtered by contract type.

    With contract_type: returns 通用 (NULL) + that type's专属 fields.
    Without: returns all active fields (legacy behaviour).
    """
    stmt = select(FieldDefinition).where(FieldDefinition.is_active == True)
    if contract_type:
        stmt = stmt.where(
            or_(
                FieldDefinition.contract_type.is_(None),
                FieldDefinition.contract_type == contract_type,
            )
        )
    stmt = stmt.order_by(FieldDefinition.sort_order, FieldDefinition.field_key)
    result = await db.execute(stmt)
    return list(result.scalars().all())
```
（`select` 已在 :14 导入；补 `from sqlalchemy import delete, or_, select`。）

`backend/app/services/violation_service.py`（新建）：

```python
"""Persist rule-validation findings and refresh them on field review.

save_violations: write a fresh ValidationResult, clearing prior 'active'
rows for the contract while preserving 'ignored' ones (reviewer dismissals).
recompute_violations: rebuild from the contract's current (possibly reviewed)
field values — called after a field correction.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.base import ExtractedField as EFData, ValidationResult
from app.models.contract import Contract, ExtractedField
from app.models.rule_violation import RuleViolation
from app.services import rule_validation_service

logger = logging.getLogger(__name__)


async def save_violations(
    db: AsyncSession, contract_id: uuid.UUID, result: ValidationResult,
) -> int:
    """Persist validation results: clear prior 'active' rows, insert fresh.

    Preserves 'ignored' rows so reviewer dismissals survive recompute.
    Returns the number of rows inserted.
    """
    await db.execute(
        delete(RuleViolation).where(
            RuleViolation.contract_id == contract_id,
            RuleViolation.status == "active",
        )
    )
    count = 0
    for v in result.violations:
        db.add(RuleViolation(
            contract_id=contract_id,
            rule_key=v.rule_name,
            severity=v.severity,
            message=v.description,
            field_key=v.field_name,
            status="active",
        ))
        count += 1
    await db.flush()
    return count


async def recompute_violations(db: AsyncSession, contract_id: uuid.UUID) -> int:
    """Re-run validation against current field values (reviewed_value wins)
    and persist. Returns the number of active violations written.
    """
    contract_row = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = contract_row.scalar_one_or_none()
    contract_type = contract.contract_type if contract else None

    rows = await db.execute(
        select(ExtractedField).where(ExtractedField.contract_id == contract_id)
    )
    fields = [
        EFData(
            field_key=r.field_key,
            field_name=r.field_name,
            value=(r.reviewed_value or r.value),
            value_type=r.value_type,
            confidence=r.confidence or 0.0,
            source_text=r.source_text,
            page_no=r.page_no,
        )
        for r in rows.scalars()
    ]
    result = rule_validation_service.validate_contract(fields, contract_type=contract_type)
    return await save_violations(db, contract_id, result)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_violation_service.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/extraction_service.py backend/app/services/violation_service.py backend/tests/test_violation_service.py
git commit -m "feat(rules): add violation_service + type-driven field loading"
```

---

## Task 3: 抽取流水线双轨接线（classify 门控 + rule/clause 并发）

**Files:**
- Modify: `backend/app/services/pipeline.py`（`_run_extraction_pipeline_inner` 核心，:221-248）
- Test: `backend/tests/test_pipeline_wiring.py`

**Interfaces:**
- Produces: `_run_extraction_pipeline_inner` 现在跑 classify→type-fields→extract→rule-validate（Track A）并发 clause-split（Track B）；设置权威 contract_type；落库 violations；kill-switch + 优雅降级。
- Consumes: T1 config `enable_rule_validation`/`enable_clause_split`；T2 `load_field_definitions(contract_type)`、`save_violations`；`LLMService.classify_contract_type`；`rule_validation_service.validate_contract`；`clause_service.split_and_save_clauses`。

- [ ] **Step 1: 写失败测试** `backend/tests/test_pipeline_wiring.py`

```python
"""Tests for Phase 2 extraction-pipeline wiring (classify/rule/clause)."""
import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_extraction_pipeline_writes_clauses_and_violations(monkeypatch):
    """End-to-end: extraction pipeline runs classify (authoritative type) +
    Track A (extract → rule-validate → violations) + Track B (clause-split)."""
    from tests.conftest import test_session_factory
    from app.services import pipeline, task_service
    from app.services.contract_service import create_contract
    from app.services.llm_service import LLMService
    from app.services.ocr_service import OCRService
    from app.models.contract import Contract, ContractFile, ContractClause
    from app.models.rule_violation import RuleViolation
    from app.extraction.base import (
        OCRDetailedResult, OCRPageResult, OCRTextBlock,
        ExtractedField, ExtractionResult,
    )

    # Canned OCR result — monkeypatch load_result so no OCR blocks need seeding.
    fake_ocr = OCRDetailedResult(pages=[OCRPageResult(page_no=1, blocks=[
        OCRTextBlock(block_type="title", text="合同", sort_order=1),
        OCRTextBlock(block_type="text", text="第一条 付款方式 分期付款。", sort_order=2),
    ])])
    monkeypatch.setattr(
        OCRService, "load_result",
        classmethod(lambda cls, db, contract_id, provider="stored": _async_return(fake_ocr)),
    )

    async def fake_classify(_full_text):
        return ("service", 0.9)
    monkeypatch.setattr(LLMService, "classify_contract_type", fake_classify)

    async def fake_extract(_full_text, field_definitions=None):
        # party-a-name empty → triggers required_fields violation (Track A)
        return ExtractionResult(contract_type="service", fields=[
            ExtractedField(field_key="party-a-name", field_name="甲方", value=None, confidence=0.9),
            ExtractedField(field_key="party-b-name", field_name="乙方", value="某公司", confidence=0.9),
        ])
    monkeypatch.setattr(LLMService, "extract_fields_from_text", fake_extract)

    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash="pipe-wiring-t3")
        db.add(ContractFile(
            contract_id=contract.id, file_name="c.pdf", file_path="/tmp/c.pdf",
            file_type="pdf", file_size=10, content_type="application/pdf", version=1,
        ))
        task = await task_service.create_task(db, contract.id, task_type="extraction")
        await db.commit()
        contract_id = contract.id
        task_id = task.id

    await pipeline.run_extraction_pipeline(task_id, session_factory=test_session_factory)

    async with test_session_factory() as db:
        clauses = (await db.execute(select(ContractClause).where(ContractClause.contract_id == contract_id))).scalars().all()
        violations = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == contract_id))).scalars().all()
        c = (await db.execute(select(Contract).where(Contract.id == contract_id))).scalar_one()

    assert len(clauses) > 0                                   # Track B ran
    assert any(v.rule_key == "required_fields" for v in violations)  # Track A rule ran
    assert c.contract_type == "service"                       # classify authoritative


async def _async_return(value):
    return value
```

> **Implementer note:** `_async_return` is a tiny helper so the monkeypatched `load_result` (a classmethod) returns the canned OCR result as an awaitable. If the project already has a cleaner pattern for monkeypatching async classmethods, use it. `task_service.create_task(db, contract_id, task_type=...)` is the existing helper used by `api/contract.py`. The pipeline is invoked with `session_factory=test_session_factory` so it uses the test DB (it defaults to the real `async_session_factory`).

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_pipeline_wiring.py -v`
Expected: FAIL / skip (test not yet real; pipeline not yet wired).

- [ ] **Step 3: 写最小实现**

`backend/app/services/pipeline.py` — 在 `_run_extraction_pipeline_inner` 中，替换从 `ocr_result = await OCRService.load_result(...)`（:226）到 `await db.commit()`（:248）之间的核心为下面双轨结构（保留外层 try/except 与 task/contract 状态更新不变）：

```python
            ocr_result = await OCRService.load_result(db, contract_id)
            if ocr_result is None or not ocr_result.full_text.strip():
                raise ValueError("OCR result is not ready")

            from app.config import settings
            from app.extraction.base import FieldSpec
            from app.services.extraction_service import extract_and_save, load_field_definitions
            from app.services.llm_service import LLMService
            from app.services.violation_service import save_violations
            from app.services import rule_validation_service
            import asyncio

            # --- Field set: user override (task payload) OR classify-driven ---
            field_specs = field_definitions
            classified_type: str | None = None
            classified_conf: float | None = None
            if field_specs is None:
                classified_type, classified_conf = await LLMService.classify_contract_type(
                    ocr_result.full_text,
                )
                ctype_for_fields = classified_type if classified_type and classified_type != "unknown" else None
                db_fields = await load_field_definitions(db, contract_type=ctype_for_fields)
                field_specs = [
                    FieldSpec(field_key=f.field_key, field_name=f.field_name,
                              description=f.description, value_type=f.value_type)
                    for f in db_fields
                ]

            ocr_result_ref = ocr_result  # captured for Track B

            async def _track_a() -> None:
                extraction = await extract_and_save(
                    db, contract_id, ocr_result_ref.to_markdown(),
                    field_definitions=field_specs,
                )
                await _raise_if_cancelled(db, task_id)
                # Authoritative contract type: classify result if we ran it, else inline.
                if classified_type and classified_type != "unknown":
                    contract.contract_type = classified_type
                    contract.contract_type_confidence = classified_conf
                elif extraction.contract_type:
                    contract.contract_type = extraction.contract_type
                    contract.contract_type_confidence = extraction.contract_type_confidence

                # Rule validation (non-fatal): persist violations for review.
                if settings.enable_rule_validation:
                    try:
                        vr = rule_validation_service.validate_contract(
                            extraction.fields,
                            contract_type=contract.contract_type,
                        )
                        await save_violations(db, contract_id, vr)
                    except Exception:
                        logger.warning("Rule validation failed for contract %s", contract_id, exc_info=True)

            async def _track_b() -> None:
                if not settings.enable_clause_split:
                    return
                from app.services.clause_service import split_and_save_clauses
                try:
                    # Separate session: concurrent writer to a different table (WAL-safe).
                    async with session_factory() as db2:
                        await split_and_save_clauses(db2, contract_id, ocr_result_ref)
                        await db2.commit()
                except Exception:
                    logger.warning("Clause split failed for contract %s", contract_id, exc_info=True)

            await asyncio.gather(_track_a(), _track_b())

            now = utc_now()
            contract.extraction_completed_at = now
            contract.status = "reviewing"
            contract.updated_at = now
            await db.commit()
```

> Note: `session_factory` is the parameter of `_run_extraction_pipeline_inner`（:208）— use it for Track B's independent session. `logger` is module-level（:29）. Keep the surrounding try/except, `_update_task(... completed ...)`, and `return`（:250-251）unchanged.

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_pipeline_wiring.py tests/test_services.py tests/test_clause_service.py tests/test_validation_service.py -v`
Expected: PASS (new wiring test + existing services/clause/validation tests green — no regression).

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/pipeline.py backend/tests/test_pipeline_wiring.py
git commit -m "feat(pipeline): wire classify gate + rule/clause dual-track extraction"
```

---

## Task 4: API — ContractDetail.violations + 忽略端点 + 复核重算 hook

**Files:**
- Modify: `backend/app/schemas/contract.py`（加 `RuleViolationDetail` + `ContractDetail.violations`）
- Modify: `backend/app/api/contract.py`（`_load_contract_detail` selectinload）
- Modify: `backend/app/api/review.py`（`PATCH /violations/{vid}` + `review_field` 重算 hook）
- Test: `backend/tests/test_review_api.py`（扩展）或新建 `test_violation_api.py`

**Interfaces:**
- Produces: `RuleViolationDetail` schema；`ContractDetail.violations`；`PATCH /contracts/{id}/violations/{vid}`（ignore/unignore）；`review_field` modify → `recompute_violations`。
- Consumes: T1 `RuleViolation`；T2 `recompute_violations`；`ReviewAction` schema（review.py）。

- [ ] **Step 1: 写失败测试** — 在 `backend/tests/test_violation_api.py`（新建，复用 `client` fixture + `_prepare` 风格；若该项目的复核测试用别的 client，按既有写法）：

```python
"""Tests for violation API: ContractDetail embed + ignore endpoint + recompute hook (T4)."""
import uuid

import pytest


@pytest.mark.asyncio
async def test_contract_detail_includes_violations(client, sample_pdf_content, tmp_upload_dir):
    """ContractDetail returns a violations list (possibly empty)."""
    from tests.test_contract_api import _prepare
    resp = await _prepare(client, "v.pdf", sample_pdf_content)
    cid = resp.json()["data"]["contract_id"]

    detail = await client.get(f"/api/v1/contracts/{cid}")
    assert detail.status_code == 200
    assert "violations" in detail.json()


@pytest.mark.asyncio
async def test_ignore_violation_toggles_status(client, sample_pdf_content, tmp_upload_dir):
    """PATCH /violations/{vid} action=approve sets status=ignored (ReviewAction in body)."""
    from tests.test_contract_api import _prepare
    from app.services import violation_service
    from app.extraction.base import RuleViolation as RVData, ValidationResult
    from app.models.rule_violation import RuleViolation
    from tests.conftest import test_session_factory
    from sqlalchemy import select

    resp = await _prepare(client, "v.pdf", sample_pdf_content)
    cid = uuid.UUID(resp.json()["data"]["contract_id"])

    # seed a violation directly
    async with test_session_factory() as db:
        await violation_service.save_violations(db, cid, ValidationResult(passed=False, violations=[
            RVData(rule_name="required_fields", severity="error", description="x", field_name="party-a-name"),
        ]))
        await db.commit()
        vid = (await db.execute(select(RuleViolation).where(RuleViolation.contract_id == cid))).scalar_one().id

    # ReviewAction is a JSON body (action/new_value/comment); reviewer_id is a query param,
    # mirroring the existing review_field endpoint.
    r = await client.patch(
        f"/api/v1/contracts/{cid}/violations/{vid}",
        json={"action": "approve"}, params={"reviewer_id": "u1"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
```

> **Note:** `ReviewAction` (schemas/review.py) is the JSON body shape used by the existing `review_field` endpoint — `action` (approve/reject/modify), optional `new_value`/`comment`. `reviewer_id` is a query param. The violation endpoint mirrors `review_field`'s signature. Confirm against `schemas/review.py` if the field names differ.

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_violation_api.py -v`
Expected: FAIL — `"violations" not in detail` / no PATCH route.

- [ ] **Step 3: 写最小实现**

`backend/app/schemas/contract.py` — 在 `ClauseDetail` 之后、`ContractDetail` 之前加：

```python
class RuleViolationDetail(BaseModel):
    id: uuid.UUID
    field_key: str | None
    rule_key: str
    severity: str
    message: str
    status: str
    detail: dict | None
    created_at: datetime
    ignored_at: datetime | None
    ignored_by: str | None

    model_config = {"from_attributes": True}
```
并在 `ContractDetail`（:104-106 附近）加字段：
```python
    violations: list[RuleViolationDetail] = []
```

`backend/app/api/contract.py` — 在 `_load_contract_detail` 的 `selectinload` 块（:44-48）加 violations：
```python
            selectinload(Contract.violations),
```

`backend/app/api/review.py` — 在 `review_field`（:209-256）的 `await db.flush()`（:254）之前，action=modify 后加重算 hook：
```python
    # Phase 2: a value correction can resolve/introduce rule violations — recompute.
    if body.action == "modify":
        from app.services.violation_service import recompute_violations
        try:
            await recompute_violations(db, contract_id)
        except Exception:
            logger.warning("violation recompute failed for %s", contract_id, exc_info=True)
```
（在 review.py 顶部加 `import logging` + `logger = logging.getLogger(__name__)` 若尚无。）

并在 review.py 末尾（batch_review 之后）加忽略端点：
```python
@router.patch("/violations/{violation_id}")
async def review_violation(
    contract_id: uuid.UUID,
    violation_id: uuid.UUID,
    body: ReviewAction,
    reviewer_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Ignore / un-ignore a rule violation. action=approve → ignored; reject → active."""
    from app.models.rule_violation import RuleViolation
    from app.schemas.contract import RuleViolationDetail

    result = await db.execute(
        select(RuleViolation).where(
            RuleViolation.id == violation_id,
            RuleViolation.contract_id == contract_id,
        )
    )
    violation = result.scalar_one_or_none()
    if not violation:
        raise HTTPException(404, "Violation not found")

    if body.action == "approve":
        violation.status = "ignored"
        violation.ignored_at = datetime.now(timezone.utc)
        violation.ignored_by = reviewer_id
    elif body.action == "reject":
        violation.status = "active"
        violation.ignored_at = None
        violation.ignored_by = None
    else:
        raise HTTPException(400, "Use action=approve (ignore) or reject (restore)")

    db.add(ReviewRecord(
        contract_id=contract_id,
        target_type="violation",
        target_id=violation_id,
        action=body.action,
        comment=body.comment,
        reviewer_id=reviewer_id,
    ))
    await db.flush()
    return RuleViolationDetail.model_validate(violation)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest tests/test_violation_api.py tests/test_review_api.py tests/test_contract_api.py -v`
Expected: PASS (new + existing review/contract tests green).

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/contract.py backend/app/api/contract.py backend/app/api/review.py backend/tests/test_violation_api.py
git commit -m "feat(api): expose violations in ContractDetail + ignore endpoint + recompute hook"
```

---

## Task 5: 全量回归 + 评测冒烟

**Files:** 无（验证任务）

- [ ] **Step 1: 跑全量后端测试**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest -q`
Expected: all green. 重点：新 `test_rule_violation_model` / `test_violation_service` / `test_pipeline_wiring` / `test_violation_api`；既有 `test_clause_service` / `test_validation_service` / `test_review_api` / `test_contract_api` / `test_services` 不退化。（已知：3 个 qwen 测试在 SOCKS 代理环境下失败——去代理变量后应全绿，与 Plan 1 同。）

- [ ] **Step 2: 评测冒烟**

Run: `cd /Users/liyuanxin/PycharmProjects/rixin-contract-extract/backend && env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy ../.venv/bin/python -m pytest -m eval -q`
Expected: 通过（harness 未被破坏）。

- [ ] **Step 3: 提交（若有 smoke 笔记，否则跳过）**

---

## Spec coverage（Plan 2 对照 spec §5/§6/§8）

| Spec 条目 | 任务 |
|---|---|
| §5.1 `rule_violations` 表 | T1 |
| §5.2 `field_definitions.contract_type` 列 | T1 |
| §6 ContractDetail 嵌入 violations（+ RuleViolationDetail） | T4 |
| §6 PATCH /violations/{vid}（忽略/留痕） | T4 |
| §6 复核改值触发规则重算 | T4（recompute hook） |
| §6 kill-switch enable_rule_validation/enable_clause_split | T1（config）+ T3（消费） |
| §8 classify 门控 + 按类型加载字段集 | T2（load_field_definitions）+ T3（pipeline） |
| §8 Track A：extract → rule-validate → 落库 | T2（save_violations）+ T3 |
| §8 Track B：clause-split 并发 + 独立 session + 优雅降级 | T3 |
| §9 评测回归 | T5 |

**不做**（spec 已简化或属 Plan 3）：FieldDetail bbox（已存在）、page_dimensions（前端用图像 natural 尺寸）、前端展示——Plan 3。
