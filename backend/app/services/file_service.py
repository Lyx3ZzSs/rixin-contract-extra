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
