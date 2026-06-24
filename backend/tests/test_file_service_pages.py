"""Tests for page-image persistence (Tier 2)."""
import uuid

from app.services import file_service


def test_save_and_locate_page_image(tmp_upload_dir):
    contract_id = uuid.uuid4()
    path = file_service.save_page_image(contract_id, 1, b"\x89PNG\r\n\x1a\nfake")

    assert file_service.page_image_path(contract_id, 1).exists()
    assert file_service.page_image_path(contract_id, 1).read_bytes() == b"\x89PNG\r\n\x1a\nfake"
    assert path.endswith("page_0001.png")


def test_save_multiple_pages_zero_padded(tmp_upload_dir):
    contract_id = uuid.uuid4()
    file_service.save_page_image(contract_id, 1, b"a")
    file_service.save_page_image(contract_id, 12, b"b")

    assert file_service.page_image_path(contract_id, 1).exists()
    assert file_service.page_image_path(contract_id, 12).name == "page_0012.png"


def test_delete_contract_pages_removes_dir(tmp_upload_dir):
    contract_id = uuid.uuid4()
    file_service.save_page_image(contract_id, 1, b"a")
    file_service.save_page_image(contract_id, 2, b"b")

    file_service.delete_contract_pages(contract_id)

    assert not file_service.page_image_path(contract_id, 1).exists()
    # deleting a non-existent contract's pages is a no-op (no error)
    file_service.delete_contract_pages(uuid.uuid4())


def test_delete_is_isolated_per_contract(tmp_upload_dir):
    a = uuid.uuid4()
    b = uuid.uuid4()
    file_service.save_page_image(a, 1, b"a")
    file_service.save_page_image(b, 1, b"b")

    file_service.delete_contract_pages(a)

    assert not file_service.page_image_path(a, 1).exists()
    assert file_service.page_image_path(b, 1).exists()
