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
