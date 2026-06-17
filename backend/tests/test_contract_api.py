"""Tests for contract API endpoints."""

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select


def _prepare(client, filename, content):
    """Helper: POST prepare and return raw response."""
    return client.post(
        "/api/v1/contracts/prepare",
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
    )


# ---- prepare ----

@pytest.mark.asyncio
async def test_prepare_contract(client, sample_pdf_content, tmp_upload_dir):
    resp = await _prepare(client, "test_contract.pdf", sample_pdf_content)
    assert resp.status_code == 201

    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "预处理已开始"
    data = body["data"]
    assert "contract_id" in data
    assert "file_id" in data
    assert "task_id" in data
    assert data["status"] == "uploaded"


@pytest.mark.asyncio
async def test_prepare_empty_file(client):
    resp = await _prepare(client, "empty.pdf", b"")
    assert resp.status_code == 400
    assert "为空" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_prepare_docx_rejected(client):
    resp = await client.post(
        "/api/v1/contracts/prepare",
        files={
            "file": (
                "contract.docx",
                io.BytesIO(b"PK\x03\x04fake-docx-content"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        },
    )
    assert resp.status_code == 400
    assert "不支持" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_prepare_no_filename(client, sample_pdf_content):
    resp = await client.post(
        "/api/v1/contracts/prepare",
        files={"file": (None, io.BytesIO(sample_pdf_content), "application/pdf")},
    )
    # FastAPI returns 422 for missing filename
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_prepare_duplicate_allowed(client, sample_pdf_content, tmp_upload_dir):
    """Duplicate prepares are permitted (no dedup); each gets a new contract."""
    resp1 = await _prepare(client, "dup.pdf", sample_pdf_content)
    assert resp1.status_code == 201

    resp2 = await _prepare(client, "dup.pdf", sample_pdf_content)
    assert resp2.status_code == 201
    assert resp1.json()["data"]["contract_id"] != resp2.json()["data"]["contract_id"]


@pytest.mark.asyncio
async def test_prepare_contract_starts_ocr_task(client, sample_pdf_content, tmp_upload_dir):
    resp = await client.post(
        "/api/v1/contracts/prepare",
        files={"file": ("prepare_test.pdf", io.BytesIO(sample_pdf_content), "application/pdf")},
    )
    assert resp.status_code == 201

    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "预处理已开始"
    data = body["data"]
    assert "contract_id" in data
    assert "file_id" in data
    assert "task_id" in data

    task_resp = await client.get(f"/api/v1/tasks/{data['task_id']}")
    assert task_resp.status_code == 200
    assert task_resp.json()["task_type"] == "ocr"


@pytest.mark.asyncio
async def test_extract_prepared_contract_requires_ready_ocr(client, sample_pdf_content, tmp_upload_dir):
    prepare_resp = await client.post(
        "/api/v1/contracts/prepare",
        files={"file": ("not_ready.pdf", io.BytesIO(sample_pdf_content), "application/pdf")},
    )
    assert prepare_resp.status_code == 201
    contract_id = uuid.UUID(prepare_resp.json()["data"]["contract_id"])

    extract_resp = await client.post(f"/api/v1/contracts/{contract_id}/extract")
    assert extract_resp.status_code == 409
    assert "OCR" in extract_resp.json()["detail"]


@pytest.mark.asyncio
async def test_extract_prepared_contract_passes_selected_fields(
    client,
    sample_pdf_content,
    tmp_upload_dir,
):
    from tests.conftest import test_session_factory
    from app.models.contract import Contract
    from app.models.ocr import OCRBlock
    from app.models.task import ContractTask

    prepare_resp = await client.post(
        "/api/v1/contracts/prepare",
        files={"file": ("ready.pdf", io.BytesIO(sample_pdf_content), "application/pdf")},
    )
    assert prepare_resp.status_code == 201
    contract_id = uuid.UUID(prepare_resp.json()["data"]["contract_id"])

    async with test_session_factory() as db:
        result = await db.execute(select(Contract).where(Contract.id == contract_id))
        contract = result.scalar_one()
        contract.ocr_completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(OCRBlock(
            contract_id=contract.id,
            page_no=1,
            block_type="text",
            text="甲方名称：北京日新科技有限公司",
            confidence=1.0,
            bbox=None,
            sort_order=1,
            page_width=100,
            page_height=100,
        ))
        await db.commit()

    extract_resp = await client.post(
        f"/api/v1/contracts/{contract_id}/extract",
        json={
            "fields": [
                {
                    "field_key": "party-a-name",
                    "field_name": "甲方名称",
                    "description": "合同甲方",
                    "value_type": "string",
                },
            ],
        },
    )

    assert extract_resp.status_code == 202
    task_id = uuid.UUID(extract_resp.json()["data"]["task_id"])
    async with test_session_factory() as db:
        result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
        task = result.scalar_one()
        assert task.status == "pending"
        assert task.stage == "queued"
        assert task.task_payload is not None
        assert len(task.task_payload["fields"]) == 1
        assert task.task_payload["fields"][0]["field_key"] == "party-a-name"
        assert task.task_payload["fields"][0]["field_name"] == "甲方名称"


# ---- list ----

@pytest.mark.asyncio
async def test_list_contracts(client, sample_pdf_content, tmp_upload_dir):
    await _prepare(client, "list_test.pdf", sample_pdf_content)

    response = await client.get("/api/v1/contracts")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


# ---- detail ----

@pytest.mark.asyncio
async def test_get_contract_detail(client, sample_pdf_content, tmp_upload_dir):
    upload_resp = await _prepare(client, "detail_test.pdf", sample_pdf_content)
    contract_id = upload_resp.json()["data"]["contract_id"]

    response = await client.get(f"/api/v1/contracts/{contract_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == contract_id
    assert len(data["files"]) == 1
    assert data["files"][0]["file_name"] == "detail_test.pdf"


@pytest.mark.asyncio
async def test_get_nonexistent_contract(client):
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/contracts/{fake_id}")
    assert response.status_code == 404
