"""Local PDF rasterization — produces page images in a known pixel space.

Tier 2 zero-error traceability: we rasterize locally so the bbox returned by
OCR lives in the SAME pixel space as the page image we persist and the frontend
displays. The overlay maps exactly via ``displayWidth / page_width``.
"""
from __future__ import annotations

import fitz  # PyMuPDF

from app.extraction.base import PageImage


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
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(PageImage(
                page_no=idx,
                png_bytes=pix.tobytes("png"),
                width=pix.width,
                height=pix.height,
            ))
    finally:
        doc.close()
    return pages
