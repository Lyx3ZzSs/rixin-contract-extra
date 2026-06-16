"""Tests for contract API endpoints."""

import io
from unittest.mock import patch

import pytest


def _upload(client, filename, content):
    """Helper: POST upload and return raw response."""
    return client.post(
        "/api/v1/contracts/upload",
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
    )


# ---- upload ----

@pytest.mark.asyncio
async def test_upload_contract(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "test_contract.pdf", sample_pdf_content)
    assert resp.status_code == 201

    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "上传成功"
    data = body["data"]
    assert "contract_id" in data
    assert "file_id" in data
    assert "task_id" in data
    assert data["status"] == "uploaded"


@pytest.mark.asyncio
async def test_upload_empty_file(client):
    resp = await _upload(client, "empty.pdf", b"")
    assert resp.status_code == 400
    assert "为空" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_no_filename(client, sample_pdf_content):
    resp = await client.post(
        "/api/v1/contracts/upload",
        files={"file": (None, io.BytesIO(sample_pdf_content), "application/pdf")},
    )
    # FastAPI returns 422 for missing filename
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_upload_duplicate_allowed(client, sample_pdf_content, tmp_upload_dir):
    """Duplicate uploads are permitted (no dedup); each gets a new contract."""
    resp1 = await _upload(client, "dup.pdf", sample_pdf_content)
    assert resp1.status_code == 201

    resp2 = await _upload(client, "dup.pdf", sample_pdf_content)
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
    contract_id = prepare_resp.json()["data"]["contract_id"]

    extract_resp = await client.post(f"/api/v1/contracts/{contract_id}/extract")
    assert extract_resp.status_code == 409
    assert "OCR" in extract_resp.json()["detail"]


# ---- list ----

@pytest.mark.asyncio
async def test_list_contracts(client, sample_pdf_content, tmp_upload_dir):
    await _upload(client, "list_test.pdf", sample_pdf_content)

    response = await client.get("/api/v1/contracts")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


# ---- detail ----

@pytest.mark.asyncio
async def test_get_contract_detail(client, sample_pdf_content, tmp_upload_dir):
    upload_resp = await _upload(client, "detail_test.pdf", sample_pdf_content)
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
