"""Tests for Phase 9: POST /contracts/{id}/review + GET review/records."""

import io
import uuid

import pytest
from sqlalchemy import select

from app.models.contract import Contract, ExtractedField
from app.models.review import ReviewRecord


async def _upload(client, filename, content):
    return await client.post(
        "/api/v1/contracts/upload",
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
    )


async def _create_contract_with_fields(client_or_content, content_override=None):
    """Create a contract with sample fields. Accepts client or raw content.

    When called as _create_contract_with_fields(client, sample_pdf_content):
      client_or_content=client, content_override=sample_pdf_content
    """
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.models.contract import ContractFile

    # Determine what was passed
    if content_override is not None:
        content = content_override
    else:
        # Called with just content bytes
        content = client_or_content

    file_path, file_type, file_size, content_hash = save_file(
        content, "review_test.pdf",
    )
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        cf = ContractFile(
            contract_id=contract.id,
            file_name="review_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        )
        db.add(cf)

        field_a = ExtractedField(
            contract_id=contract.id,
            field_key="party-a-name",
            field_name="甲方名称",
            value="北京日新科技有限公司",
            value_type="string",
            source_text="甲方：北京日新科技有限公司",
            page_no=1,
            confidence=0.98,
            review_status="extracted",
        )
        field_b = ExtractedField(
            contract_id=contract.id,
            field_key="amount",
            field_name="合同金额",
            value="1200000.00",
            value_type="number",
            source_text="项目总金额为人民币壹佰贰拾万元整",
            page_no=1,
            confidence=0.96,
            review_status="extracted",
        )
        db.add(field_a)
        db.add(field_b)
        await db.flush()
        await db.commit()
        return contract.id, field_a.id, field_b.id


# ---------------------------------------------------------------------------
# POST /review
# ---------------------------------------------------------------------------

class TestPostReview:
    @pytest.mark.asyncio
    async def test_correct_single_field(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, field_a_id, field_b_id = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {
                        "field_id": str(field_a_id),
                        "corrected_value": "北京日新科技股份有限公司",
                        "comment": "公司名称变更",
                    },
                ],
                "action": "corrected",
                "reviewer_id": "reviewer_001",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["review_action"] == "corrected"
        assert len(data["corrected_fields"]) == 1
        assert len(data["review_records"]) == 1

        cf = data["corrected_fields"][0]
        assert cf["field_id"] == str(field_a_id)
        assert cf["old_value"] == "北京日新科技有限公司"
        assert cf["corrected_value"] == "北京日新科技股份有限公司"
        assert cf["review_status"] == "corrected"

    @pytest.mark.asyncio
    async def test_correct_multiple_fields(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, field_a_id, field_b_id = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {
                        "field_id": str(field_a_id),
                        "corrected_value": "新公司A",
                    },
                    {
                        "field_id": str(field_b_id),
                        "corrected_value": "1500000.00",
                        "comment": "金额修正",
                    },
                ],
                "action": "corrected",
                "reviewer_id": "reviewer_001",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["corrected_fields"]) == 2
        assert len(data["review_records"]) == 2

    @pytest.mark.asyncio
    async def test_original_value_preserved(self, client, sample_pdf_content, tmp_upload_dir):
        """The original field.value must NOT be overwritten."""
        contract_id, field_a_id, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {
                        "field_id": str(field_a_id),
                        "corrected_value": "修正后的公司名称",
                    },
                ],
                "action": "corrected",
                "reviewer_id": "reviewer_001",
            },
        )

        from tests.conftest import test_session_factory
        async with test_session_factory() as db:
            result = await db.execute(
                select(ExtractedField).where(ExtractedField.id == field_a_id)
            )
            field = result.scalar_one()
            assert field.value == "北京日新科技有限公司"
            assert field.reviewed_value == "修正后的公司名称"
            assert field.review_status == "corrected"

    @pytest.mark.asyncio
    async def test_audit_record_written(self, client, sample_pdf_content, tmp_upload_dir):
        """Each correction must produce a ReviewRecord with old_value and new_value."""
        contract_id, field_a_id, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {
                        "field_id": str(field_a_id),
                        "corrected_value": "新值",
                        "comment": "测试审计",
                    },
                ],
                "action": "corrected",
                "reviewer_id": "reviewer_002",
            },
        )

        from tests.conftest import test_session_factory
        async with test_session_factory() as db:
            result = await db.execute(
                select(ReviewRecord).where(
                    ReviewRecord.contract_id == contract_id,
                    ReviewRecord.target_type == "field",
                )
            )
            records = result.scalars().all()
            assert len(records) >= 1
            r = records[0]
            assert r.action == "modify"
            assert r.old_value == "北京日新科技有限公司"
            assert r.new_value == "新值"
            assert r.comment == "测试审计"
            assert r.reviewer_id == "reviewer_002"

    @pytest.mark.asyncio
    async def test_review_status_reviewed(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, field_a_id, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {"field_id": str(field_a_id), "corrected_value": "X"},
                ],
                "action": "reviewed",
                "reviewer_id": "r1",
            },
        )
        assert response.status_code == 200
        assert response.json()["corrected_fields"][0]["review_status"] == "reviewed"

    @pytest.mark.asyncio
    async def test_nonexistent_field_returns_404(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, _, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {"field_id": str(uuid.uuid4()), "corrected_value": "X"},
                ],
                "action": "corrected",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_nonexistent_contract_returns_404(self, client):
        response = await client.post(
            f"/api/v1/contracts/{uuid.uuid4()}/review",
            json={
                "corrections": [],
                "action": "corrected",
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_corrections(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, _, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [],
                "action": "approved",
                "reviewer_id": "r1",
                "comment": "全部确认无误",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["corrected_fields"]) == 0
        assert len(data["review_records"]) >= 1

    @pytest.mark.asyncio
    async def test_field_wrong_contract_returns_404(self, client, sample_pdf_content, tmp_upload_dir):
        """A field belonging to a different contract should be rejected."""
        contract_id, _, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)
        # Second contract with different content
        different_content = sample_pdf_content.replace(b"190", b"191")
        contract_id_2, field_a2_id, _ = \
            await _create_contract_with_fields(client, different_content)

        response = await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {"field_id": str(field_a2_id), "corrected_value": "X"},
                ],
                "action": "corrected",
            },
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /review/records
# ---------------------------------------------------------------------------

class TestReviewRecords:
    @pytest.mark.asyncio
    async def test_list_records_empty(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, _, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.get(
            f"/api/v1/contracts/{contract_id}/review/records",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_records_after_correction(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, field_a_id, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {"field_id": str(field_a_id), "corrected_value": "X"},
                ],
                "action": "corrected",
                "reviewer_id": "r1",
            },
        )

        response = await client.get(
            f"/api/v1/contracts/{contract_id}/review/records",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1
        assert data["items"][0]["action"] == "modify"
        assert data["items"][0]["old_value"] == "北京日新科技有限公司"
        assert data["items"][0]["new_value"] == "X"

    @pytest.mark.asyncio
    async def test_filter_by_target_type(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, field_a_id, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        await client.post(
            f"/api/v1/contracts/{contract_id}/review",
            json={
                "corrections": [
                    {"field_id": str(field_a_id), "corrected_value": "X"},
                ],
                "action": "corrected",
            },
        )

        response = await client.get(
            f"/api/v1/contracts/{contract_id}/review/records",
            params={"target_type": "field"},
        )
        assert response.status_code == 200
        assert response.json()["total"] >= 1

# ---------------------------------------------------------------------------
# PATCH /fields/{field_id}/review — existing endpoint still works
# ---------------------------------------------------------------------------

class TestFieldReviewEndpoint:
    @pytest.mark.asyncio
    async def test_modify_via_patch(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, field_a_id, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.patch(
            f"/api/v1/contracts/{contract_id}/fields/{field_a_id}/review",
            json={"action": "modify", "new_value": "修正公司名"},
            params={"reviewer_id": "r1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reviewed_value"] == "修正公司名"
        assert data["review_status"] == "corrected"
        assert data["value"] == "北京日新科技有限公司"

    @pytest.mark.asyncio
    async def test_approve_via_patch(self, client, sample_pdf_content, tmp_upload_dir):
        contract_id, field_a_id, _ = \
            await _create_contract_with_fields(client, sample_pdf_content)

        response = await client.patch(
            f"/api/v1/contracts/{contract_id}/fields/{field_a_id}/review",
            json={"action": "approve"},
            params={"reviewer_id": "r1"},
        )
        assert response.status_code == 200
        assert response.json()["review_status"] == "approved"
