"""Tests for review API endpoints."""

import io
import uuid

import pytest


async def _upload(client, filename, content):
    """Helper: POST upload and return response."""
    return await client.post(
        "/api/v1/contracts/upload",
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
    )


@pytest.mark.asyncio
async def test_approve_contract(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "approve_test.pdf", sample_pdf_content)
    contract_id = resp.json()["data"]["contract_id"]

    response = await client.post(
        f"/api/v1/contracts/{contract_id}/approve",
        params={"reviewer_id": "test_reviewer"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reject_contract(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "reject_test.pdf", sample_pdf_content)
    contract_id = resp.json()["data"]["contract_id"]

    response = await client.post(
        f"/api/v1/contracts/{contract_id}/reject",
        params={"reviewer_id": "test_reviewer", "comment": "invalid"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_list_fields_empty(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "fields_test.pdf", sample_pdf_content)
    contract_id = resp.json()["data"]["contract_id"]

    response = await client.get(f"/api/v1/contracts/{contract_id}/fields")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_clauses_empty(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "clauses_test.pdf", sample_pdf_content)
    contract_id = resp.json()["data"]["contract_id"]

    response = await client.get(f"/api/v1/contracts/{contract_id}/clauses")
    assert response.status_code == 200
    assert response.json() == []
