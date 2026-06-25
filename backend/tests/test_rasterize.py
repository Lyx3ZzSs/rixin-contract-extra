"""Tests for local PDF rasterization (Tier 2 zero-error traceability)."""
import fitz

from app.extraction.base import PageImage
from app.extraction.ocr.rasterize import rasterize_pdf_page, rasterize_pdf_to_pages


def _make_pdf(tmp_path, pages: int = 2) -> str:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    p = tmp_path / "test.pdf"
    doc.save(str(p))
    doc.close()
    return str(p)


def test_rasterize_produces_one_image_per_page(tmp_path):
    pdf = _make_pdf(tmp_path, pages=3)
    images = rasterize_pdf_to_pages(pdf, dpi=144)

    assert [img.page_no for img in images] == [1, 2, 3]
    assert all(img.width and img.height for img in images)
    # PNG magic header
    assert all(img.png_bytes[:8] == b"\x89PNG\r\n\x1a\n" for img in images)


def test_rasterize_dpi_scales_dimensions(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    low = rasterize_pdf_to_pages(pdf, dpi=72)[0]
    high = rasterize_pdf_to_pages(pdf, dpi=144)[0]

    # 144 dpi == 2x the pixels of 72 dpi
    assert high.width == low.width * 2
    assert high.height == low.height * 2


def test_rasterize_returns_page_image_type(tmp_path):
    pdf = _make_pdf(tmp_path, pages=1)
    img = rasterize_pdf_to_pages(pdf)[0]

    assert isinstance(img, PageImage)
    assert img.page_no == 1


def test_rasterize_single_pdf_page(tmp_path):
    pdf = _make_pdf(tmp_path, pages=3)
    img = rasterize_pdf_page(pdf, page_no=2, dpi=144)

    assert isinstance(img, PageImage)
    assert img.page_no == 2
    assert img.width and img.height
    assert img.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
