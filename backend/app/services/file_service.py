"""File storage service — local filesystem backend."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from app.config import settings
from app.utils.file_type import identify_file_type, is_allowed_file_type


# Extension used for the sanitized on-disk filename, keyed by detected type.
_EXTENSION_BY_TYPE: dict[str, str] = {
    "pdf": ".pdf",
    "png": ".png",
    "jpg": ".jpg",
    "jpeg": ".jpg",
    "gif": ".gif",
    "bmp": ".bmp",
    "tiff": ".tiff",
    "tif": ".tiff",
}


def _upload_dir() -> Path:
    """Return the upload root, creating it if necessary."""
    p = Path(settings.upload_dir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_file(file_data: bytes, file_name: str) -> tuple[str, str, int, str]:
    """Save uploaded file to local disk.

    Returns (file_path, file_type, file_size, content_hash).
    Raises ValueError if file type is not allowed.

    The on-disk filename is sanitized to a UUID-stemmed name to prevent path
    traversal (a malicious ``file_name`` like ``../../etc/x`` could otherwise
    escape the upload subdir). The caller keeps the original user-facing name
    separately (ContractFile.file_name) for display.
    """
    file_type = identify_file_type(file_data, file_name)
    if not is_allowed_file_type(file_type):
        raise ValueError(f"不支持此文件类型: {file_type}")

    content_hash = hashlib.sha256(file_data).hexdigest()
    file_size = len(file_data)

    # Store under uploads/contracts/<uuid>/<safe-name>
    subdir = _upload_dir() / str(uuid.uuid4())
    subdir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_disk_filename(file_name, file_type)
    dest = subdir / safe_name
    dest.write_bytes(file_data)

    # Relative path from project root for DB storage
    file_path = str(dest)
    return file_path, file_type, file_size, content_hash


def _safe_disk_filename(original: str, file_type: str) -> str:
    """Build a traversal-safe on-disk filename.

    Strips any directory components from the user-supplied name and falls back
    to a UUID stem when nothing usable remains, keeping a sane extension for
    the detected file type so downstream tools can still infer it.
    """
    ext = _EXTENSION_BY_TYPE.get(file_type, ".bin")
    # Take only the basename (drops any path separators / `..` segments) and
    # restrict to a conservative character set.
    base = Path(original).name
    base = "".join(c for c in base if c.isalnum() or c in "-_.") or "upload"
    # Trim any leading dots so the file is never hidden / never escapes via `..`.
    base = base.lstrip(".")
    if not base:
        base = "upload"
    return f"{base}{ext}" if not base.lower().endswith(ext) else base


def read_file(file_path: str) -> bytes:
    """Read a file back from disk."""
    return Path(file_path).read_bytes()


def page_images_dir(contract_id: uuid.UUID) -> Path:
    """Return (creating) the per-contract page-image directory."""
    d = _upload_dir() / "pages" / str(contract_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_page_image(contract_id: uuid.UUID, page_no: int, png_bytes: bytes) -> str:
    """Persist one rasterized page image. Overwrites if it already exists.

    Returns the absolute path written.
    """
    dest = page_images_dir(contract_id) / f"page_{page_no:04d}.png"
    dest.write_bytes(png_bytes)
    return str(dest)


def page_image_path(contract_id: uuid.UUID, page_no: int) -> Path:
    """Return the expected path for a page image (may not exist yet)."""
    return _upload_dir() / "pages" / str(contract_id) / f"page_{page_no:04d}.png"


def delete_contract_pages(contract_id: uuid.UUID) -> None:
    """Remove all persisted page images for a contract (no-op if none)."""
    d = _upload_dir() / "pages" / str(contract_id)
    if d.exists():
        shutil.rmtree(d)
