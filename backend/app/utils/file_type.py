"""File type identification based on magic bytes and extensions."""

# Magic byte signatures
SIGNATURES: dict[bytes, str] = {
    b"%PDF": "pdf",
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF8": "gif",
    b"BM": "bmp",
    b"II*\x00": "tiff",
    b"MM\x00*": "tiff",
}

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif"}
ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/bmp",
    "image/tiff",
}


def identify_file_type(content: bytes, filename: str) -> str | None:
    """Identify file type from magic bytes, falling back to extension."""
    for sig, ftype in SIGNATURES.items():
        if content[: len(sig)] == sig:
            return ftype

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ALLOWED_EXTENSIONS:
        return ext

    return None


def is_allowed_file_type(file_type: str | None) -> bool:
    return file_type is not None and file_type in ALLOWED_EXTENSIONS
