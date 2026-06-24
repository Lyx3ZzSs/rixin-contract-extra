"""File storage service — local filesystem backend."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from app.config import settings
from app.utils.file_type import identify_file_type, is_allowed_file_type


def _upload_dir() -> Path:
    """Return the upload root, creating it if necessary."""
    p = Path(settings.upload_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_file(file_data: bytes, file_name: str) -> tuple[str, str, int, str]:
    """Save uploaded file to local disk.

    Returns (file_path, file_type, file_size, content_hash).
    Raises ValueError if file type is not allowed.
    """
    file_type = identify_file_type(file_data, file_name)
    if not is_allowed_file_type(file_type):
        raise ValueError(f"不支持此文件类型: {file_type}")

    content_hash = hashlib.sha256(file_data).hexdigest()
    file_size = len(file_data)

    # Store under uploads/contracts/<uuid>/<original_name>
    subdir = _upload_dir() / str(uuid.uuid4())
    subdir.mkdir(parents=True, exist_ok=True)
    dest = subdir / file_name
    dest.write_bytes(file_data)

    # Relative path from project root for DB storage
    file_path = str(dest)
    return file_path, file_type, file_size, content_hash


def read_file(file_path: str) -> bytes:
    """Read a file back from disk."""
    return Path(file_path).read_bytes()


import shutil
import uuid as _uuid


def page_images_dir(contract_id: _uuid.UUID) -> Path:
    """Return (creating) the per-contract page-image directory."""
    d = _upload_dir() / "pages" / str(contract_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_page_image(contract_id: _uuid.UUID, page_no: int, png_bytes: bytes) -> str:
    """Persist one rasterized page image. Overwrites if it already exists.

    Returns the absolute path written.
    """
    dest = page_images_dir(contract_id) / f"page_{page_no:04d}.png"
    dest.write_bytes(png_bytes)
    return str(dest)


def page_image_path(contract_id: _uuid.UUID, page_no: int) -> Path:
    """Return the expected path for a page image (may not exist yet)."""
    return _upload_dir() / "pages" / str(contract_id) / f"page_{page_no:04d}.png"


def delete_contract_pages(contract_id: _uuid.UUID) -> None:
    """Remove all persisted page images for a contract (no-op if none)."""
    d = _upload_dir() / "pages" / str(contract_id)
    if d.exists():
        shutil.rmtree(d)
