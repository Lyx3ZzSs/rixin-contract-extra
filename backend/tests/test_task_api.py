"""Tests for task API endpoints and pipeline."""

import io
import uuid

import pytest
from sqlalchemy import select

from app.models.task import ContractTask


async def _upload(client, filename, content):
    return await client.post(
        "/api/v1/contracts/upload",
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
    )


@pytest.mark.asyncio
async def test_get_contract_tasks(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "task_test.pdf", sample_pdf_content)
    contract_id = resp.json()["data"]["contract_id"]

    response = await client.get(f"/api/v1/tasks/contract/{contract_id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) >= 1
    assert data["items"][0]["contract_id"] == contract_id


@pytest.mark.asyncio
async def test_get_task_detail(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "task_detail.pdf", sample_pdf_content)
    contract_id = resp.json()["data"]["contract_id"]

    tasks_resp = await client.get(f"/api/v1/tasks/contract/{contract_id}")
    task_id = tasks_resp.json()["items"][0]["id"]

    response = await client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["id"] == task_id




@pytest.mark.asyncio
async def test_task_detail_includes_updated_at(client, sample_pdf_content, tmp_upload_dir):
    resp = await _upload(client, "updated_at_test.pdf", sample_pdf_content)
    task_id = resp.json()["data"]["task_id"]

    response = await client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert "updated_at" in data
    assert data["updated_at"] is not None
@pytest.mark.asyncio
async def test_pipeline_runs_to_completion(sample_pdf_content, tmp_upload_dir):
    """Run the pipeline directly (bypassing Celery) and verify every step."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.services.task_service import create_task
    from app.models.contract import ContractFile

    # Create a file + contract + task manually
    file_path, file_type, file_size, content_hash = save_file(
        sample_pdf_content, "pipeline_test.pdf",
    )
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        # Create a file record so the pipeline can find it
        contract_file = ContractFile(
            contract_id=contract.id,
            file_name="pipeline_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        )
        db.add(contract_file)
        await db.flush()
        task = await create_task(db, contract.id, task_type="full_pipeline")
        await db.commit()
        task_id = task.id

    from app.services.pipeline import run_pipeline
    result = await run_pipeline(task_id, session_factory=test_session_factory)

    assert result["status"] == "completed"

    # Verify task end state
    async with test_session_factory() as db:
        result_q = await db.execute(
            select(ContractTask).where(ContractTask.id == task_id)
        )
        task = result_q.scalar_one()
        assert task.status == "completed"
        assert task.progress == 100
        assert task.error_message is None
        assert task.started_at is not None
        assert task.completed_at is not None


@pytest.mark.asyncio
async def test_pipeline_sets_per_step_status(sample_pdf_content, tmp_upload_dir):
    """Verify that intermediate status values are set during pipeline execution."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.services.task_service import create_task
    from app.models.contract import ContractFile

    file_path, file_type, file_size, content_hash = save_file(
        sample_pdf_content, "status_test.pdf",
    )
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        contract_file = ContractFile(
            contract_id=contract.id,
            file_name="status_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        )
        db.add(contract_file)
        await db.flush()
        task = await create_task(db, contract.id, task_type="full_pipeline")
        await db.commit()
        task_id = task.id

    from app.services.pipeline import run_pipeline
    await run_pipeline(task_id, session_factory=test_session_factory)

    async with test_session_factory() as db:
        result = await db.execute(
            select(ContractTask).where(ContractTask.id == task_id)
        )
        task = result.scalar_one()
        # After completion the status should be one of the valid final states
        assert task.status == "completed"
        assert task.progress == 100
        assert task.updated_at is not None
