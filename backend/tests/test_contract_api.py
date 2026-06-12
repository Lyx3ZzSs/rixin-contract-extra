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
