"""Tests for OCR result → structured markdown rendering."""
from app.extraction.base import BBox, OCRDetailedResult, OCRPageResult, OCRTextBlock


def _block(btype: str, text: str, sort_order: int) -> OCRTextBlock:
    return OCRTextBlock(
        block_type=btype, text=text, bbox=BBox.from_list([0, 0, 10, 10]),
        confidence=0.9, sort_order=sort_order,
    )


def test_page_markdown_emits_plain_text():
    page = OCRPageResult(page_no=1, blocks=[
        _block("title", "第一条 总则", 1),
        _block("text", "本合同如下。", 2),
    ])
    md = page.to_markdown()
    assert "第一条 总则" in md
    assert "**" not in md
    assert "本合同如下。" in md


def test_page_markdown_emits_table_text_verbatim():
    table_md = "| 期次 | 金额 |\n|---|---|\n| 1 | 30万 |"
    page = OCRPageResult(page_no=1, blocks=[_block("table", table_md, 1)])
    assert page.to_markdown() == table_md


def test_page_markdown_empty_blocks():
    assert OCRPageResult(page_no=1, blocks=[]).to_markdown() == ""


def test_document_markdown_has_one_marker_per_page():
    doc = OCRDetailedResult(pages=[
        OCRPageResult(page_no=1, blocks=[_block("text", "p1", 1)]),
        OCRPageResult(page_no=2, blocks=[_block("text", "p2", 1)]),
    ])
    md = doc.to_markdown()
    assert md.count("<!-- page:") == 2
    assert "<!-- page: 1 -->" in md
    assert "<!-- page: 2 -->" in md
    assert "p1" in md and "p2" in md


def test_document_markdown_single_page():
    doc = OCRDetailedResult(pages=[
        OCRPageResult(page_no=1, blocks=[_block("title", "标题", 1)]),
    ])
    md = doc.to_markdown()
    assert md.startswith("<!-- page: 1 -->")
    assert "标题" in md
    assert "**" not in md


def test_page_marker_template_constant():
    assert OCRDetailedResult.PAGE_MARKER_TEMPLATE == "<!-- page: {page_no} -->"
