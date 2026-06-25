"""Local PDF rasterization — produces page images in a known pixel space.

Tier 2 zero-error traceability: we rasterize locally so the bbox returned by
OCR lives in the SAME pixel space as the page image we persist and the frontend
displays. The overlay maps exactly via ``displayWidth / page_width``.
"""
from __future__ import annotations

import fitz  # PyMuPDF

from app.extraction.base import PageImage


def _page_to_image(page, page_no: int, matrix) -> PageImage:
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return PageImage(
        page_no=page_no,
        png_bytes=pix.tobytes("png"),
        width=pix.width,
        height=pix.height,
    )


def rasterize_pdf_page(file_path: str, page_no: int, dpi: int = 200) -> PageImage:
    """Rasterize a single 1-indexed PDF page to PNG."""
    if page_no < 1:
        raise ValueError("page_no must be >= 1")
    doc = fitz.open(file_path)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    try:
        if page_no > len(doc):
            raise ValueError(f"page_no out of range: {page_no}")
        return _page_to_image(doc[page_no - 1], page_no, matrix)
    finally:
        doc.close()


def rasterize_pdf_to_pages(file_path: str, dpi: int = 200) -> list[PageImage]:
    """Rasterize every page of *file_path* (a PDF) to a PNG at *dpi*.

    Returns one ``PageImage`` per page, 1-indexed, with pixel width/height.
    """
    doc = fitz.open(file_path)
    zoom = dpi / 72.0  # PDF points (72/inch) -> device pixels at the requested DPI
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[PageImage] = []
    try:
        for idx, page in enumerate(doc, start=1):
            pages.append(_page_to_image(page, idx, matrix))
    finally:
        doc.close()
    return pages
