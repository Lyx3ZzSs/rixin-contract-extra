"""Tests for task API endpoints and pipeline."""

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.contract import Contract, ExtractedField
from app.models.ocr import OCRBlock
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
async def test_pipeline_sets_per_step_status(sample_pdf_content, tmp_upload_dir, monkeypatch):
    """Verify that intermediate status values are set during pipeline execution."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.services.task_service import create_task
    from app.models.contract import ContractFile
    from app.services import pipeline

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

    seen_statuses: list[str] = []
    original_update_task = pipeline._update_task

    async def capture_update_task(*args, **kwargs):
        seen_statuses.append(kwargs["status"])
        await original_update_task(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_update_task", capture_update_task)

    await pipeline.run_pipeline(task_id, session_factory=test_session_factory)

    assert "clause_splitting" not in seen_statuses
    assert "field_extracting" in seen_statuses
    assert seen_statuses[-1] == "completed"

    async with test_session_factory() as db:
        result = await db.execute(
            select(ContractTask).where(ContractTask.id == task_id)
        )
        task = result.scalar_one()
        # After completion the status should be one of the valid final states
        assert task.status == "completed"
        assert task.progress == 100
        assert task.updated_at is not None


@pytest.mark.asyncio
async def test_pipeline_marks_unexpected_exception_failed(sample_pdf_content, tmp_upload_dir, monkeypatch):
    """Unexpected exceptions must not leave a task stuck in an in-progress status."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.services.task_service import create_task
    from app.models.contract import Contract, ContractFile
    from app.services.pipeline import run_pipeline
    from app.services.ocr_service import OCRService

    file_path, file_type, file_size, content_hash = save_file(
        sample_pdf_content, "unexpected_failure_test.pdf",
    )
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        db.add(ContractFile(
            contract_id=contract.id,
            file_name="unexpected_failure_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        ))
        await db.flush()
        task = await create_task(db, contract.id, task_type="full_pipeline")
        await db.commit()
        task_id = task.id
        contract_id = contract.id

    def raise_unexpected(*_args, **_kwargs):
        raise AttributeError("simulated unexpected OCR failure")

    monkeypatch.setattr(OCRService, "process", raise_unexpected)

    with pytest.raises(AttributeError):
        await run_pipeline(task_id, session_factory=test_session_factory)

    async with test_session_factory() as db:
        task_result = await db.execute(
            select(ContractTask).where(ContractTask.id == task_id)
        )
        task = task_result.scalar_one()
        assert task.status == "text_extracting_failed"
        assert task.progress == 15
        assert task.error_message == "simulated unexpected OCR failure"
        assert task.completed_at is not None

        contract_result = await db.execute(
            select(Contract).where(Contract.id == contract_id)
        )
        assert contract_result.scalar_one().status == "failed"


@pytest.mark.asyncio
async def test_ocr_pipeline_runs_to_completion(sample_pdf_content, tmp_upload_dir):
    """OCR-only pipeline should persist OCR blocks and stop before field extraction."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.services.task_service import create_task
    from app.models.contract import ContractFile
    from app.services.pipeline import run_ocr_pipeline

    file_path, file_type, file_size, content_hash = save_file(
        sample_pdf_content, "ocr_pipeline_test.pdf",
    )
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        db.add(ContractFile(
            contract_id=contract.id,
            file_name="ocr_pipeline_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        ))
        await db.flush()
        task = await create_task(db, contract.id, task_type="ocr")
        await db.commit()
        task_id = task.id
        contract_id = contract.id

    result = await run_ocr_pipeline(task_id, session_factory=test_session_factory)
    assert result["status"] == "completed"

    async with test_session_factory() as db:
        task_result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
        task = task_result.scalar_one()
        assert task.task_type == "ocr"
        assert task.status == "completed"
        assert task.progress == 100

        contract_result = await db.execute(select(Contract).where(Contract.id == contract_id))
        contract = contract_result.scalar_one()
        assert contract.status == "ocr_completed"
        assert contract.ocr_completed_at is not None
        assert contract.extraction_completed_at is None

        block_count = await db.execute(select(OCRBlock).where(OCRBlock.contract_id == contract_id))
        assert len(block_count.scalars().all()) > 0

        field_count = await db.execute(select(ExtractedField).where(ExtractedField.contract_id == contract_id))
        assert len(field_count.scalars().all()) == 0


@pytest.mark.asyncio
async def test_extraction_pipeline_reuses_stored_ocr(sample_pdf_content, tmp_upload_dir, monkeypatch):
    """Extraction-only pipeline must use persisted OCR blocks instead of calling OCR again."""
    from tests.conftest import test_session_factory
    from app.services.file_service import save_file
    from app.services.contract_service import create_contract
    from app.services.task_service import create_task
    from app.models.contract import ContractFile
    from app.services.ocr_service import OCRService
    from app.services.pipeline import run_extraction_pipeline

    file_path, file_type, file_size, content_hash = save_file(
        sample_pdf_content, "extraction_pipeline_test.pdf",
    )
    async with test_session_factory() as db:
        contract = await create_contract(db, content_hash=content_hash)
        db.add(ContractFile(
            contract_id=contract.id,
            file_name="extraction_pipeline_test.pdf",
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            content_type="application/pdf",
        ))
        await db.flush()
        await OCRService.process(db, contract.id, file_path, file_type)
        contract.ocr_completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        task = await create_task(db, contract.id, task_type="extraction")
        await db.commit()
        task_id = task.id
        contract_id = contract.id

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("OCR should not run during extraction-only pipeline")

    monkeypatch.setattr(OCRService, "process", fail_if_called)

    result = await run_extraction_pipeline(task_id, session_factory=test_session_factory)
    assert result["status"] == "completed"

    async with test_session_factory() as db:
        task_result = await db.execute(select(ContractTask).where(ContractTask.id == task_id))
        task = task_result.scalar_one()
        assert task.task_type == "extraction"
        assert task.status == "completed"

        contract_result = await db.execute(select(Contract).where(Contract.id == contract_id))
        contract = contract_result.scalar_one()
        assert contract.status == "reviewing"
        assert contract.extraction_completed_at is not None

        fields = await db.execute(select(ExtractedField).where(ExtractedField.contract_id == contract_id))
        assert len(fields.scalars().all()) > 0
