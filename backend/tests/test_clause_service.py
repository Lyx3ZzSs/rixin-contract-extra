"""Tests for the enhanced clause splitting service."""

import pytest

from app.extraction.base import (
    BBox,
    ClauseSegment,
    OCRDetailedResult,
    OCRPageResult,
    OCRTextBlock,
)
from app.services.clause_service import (
    _classify_clause_type,
    _is_clause_marker,
    split_clauses_from_blocks,
)


# ---------------------------------------------------------------------------
# _is_clause_marker tests
# ---------------------------------------------------------------------------

class TestIsClauseMarker:
    def test_article_cn_1(self):
        name, marker, title, _level = _is_clause_marker("第一条 项目概述")
        assert name == "article_cn"
        assert "第一条" in marker
        assert title == "项目概述"

    def test_article_cn_number(self):
        name, marker, title, _level = _is_clause_marker("第3条 付款方式")
        assert name == "article_cn"
        assert title == "付款方式"

    def test_article_cn_no_title(self):
        name, marker, title, _level = _is_clause_marker("第十条")
        assert name == "article_cn"
        assert title is None

    def test_section_cn(self):
        name, marker, title, _level = _is_clause_marker("一、项目背景")
        assert name == "section_cn"
        assert title == "项目背景"

    def test_paren_cn(self):
        name, marker, title, _level = _is_clause_marker("（一）甲方权利")
        assert name == "paren_cn"
        assert title == "甲方权利"

    def test_paren_num(self):
        name, marker, title, _level = _is_clause_marker("（1）首期款")
        assert name == "paren_num"
        assert title == "首期款"

    def test_dotted_multi(self):
        name, marker, title, _level = _is_clause_marker("1.1.1 技术标准")
        assert name == "dotted_multi"
        assert title == "技术标准"

    def test_num_dot(self):
        name, marker, title, _level = _is_clause_marker("1. 项目目标")
        assert name == "num_dot"
        assert title == "项目目标"

    def test_article_en(self):
        name, marker, title, _level = _is_clause_marker("Article 1 Scope")
        assert name == "article_en"
        assert title == "Scope"

    def test_article_en_upper(self):
        name, marker, title, _level = _is_clause_marker("ARTICLE 3 TERMINATION")
        assert name == "article_en"
        assert title == "TERMINATION"

    def test_non_marker(self):
        name, marker, title, _level = _is_clause_marker("甲方：北京科技有限公司")
        assert name is None

    def test_empty(self):
        name, marker, title, _level = _is_clause_marker("")
        assert name is None

    def test_short(self):
        name, marker, title, _level = _is_clause_marker("第")
        assert name is None


# ---------------------------------------------------------------------------
# _classify_clause_type tests
# ---------------------------------------------------------------------------

class TestClassifyClauseType:
    def test_payment(self):
        assert _classify_clause_type("第二条 付款方式") == "payment"
        assert _classify_clause_type("第三条 支付条款") == "payment"

    def test_breach(self):
        assert _classify_clause_type("第四条 违约责任") == "breach"

    def test_confidentiality(self):
        assert _classify_clause_type("第五条 保密条款") == "confidentiality"

    def test_dispute(self):
        assert _classify_clause_type("第八条 争议解决") == "dispute"

    def test_term(self):
        assert _classify_clause_type("第九条 合同期限") == "term"

    def test_project(self):
        assert _classify_clause_type("第一条 项目概述") == "project"

    def test_schedule(self):
        assert _classify_clause_type("第三条 工期") == "schedule"

    def test_unknown_returns_none(self):
        assert _classify_clause_type("第十二条 苹果") is None

    def test_termination(self):
        assert _classify_clause_type("第十条 终止") == "termination"


# ---------------------------------------------------------------------------
# Block-based split_clauses_from_blocks tests
# ---------------------------------------------------------------------------

def _make_block(block_type: str, text: str, sort_order: int,
                bbox: list[float] | None = None, confidence: float = 0.95) -> OCRTextBlock:
    return OCRTextBlock(
        block_type=block_type,
        text=text,
        bbox=BBox.from_list(bbox) if bbox else None,
        confidence=confidence,
        sort_order=sort_order,
    )


def _make_ocr_result(pages: list[tuple[int, list[tuple]]]) -> OCRDetailedResult:
    """Build OCRDetailedResult from a simpler structure.

    pages: [(page_no, [(block_type, text, sort_order, bbox_list_or_None, confidence), ...])]
    """
    result_pages = []
    for page_no, block_specs in pages:
        blocks = []
        for spec in block_specs:
            btype, text, sort = spec[0], spec[1], spec[2]
            bbox = spec[3] if len(spec) > 3 else None
            conf = spec[4] if len(spec) > 4 else 0.95
            blocks.append(_make_block(btype, text, sort, bbox, conf))
        result_pages.append(OCRPageResult(page_no=page_no, blocks=blocks))
    return OCRDetailedResult(pages=result_pages, provider="mock")


class TestSplitClausesFromBlocks:
    def test_single_page_basic(self):
        ocr = _make_ocr_result([
            (1, [
                ("title", "合同编号：HT-2024-001", 1, [120, 80, 540, 115]),
                ("title", "甲方：北京日新科技有限公司", 2, [120, 140, 520, 175]),
                ("title", "第一条 项目概述", 3, [120, 200, 380, 235]),
                ("text", "甲方委托乙方进行企业管理系统的设计、开发、测试及部署工作。", 4, [120, 250, 900, 290]),
                ("title", "第二条 付款方式", 5, [120, 330, 380, 365]),
                ("text", "本合同总金额为人民币1,200,000.00元。", 6, [120, 400, 900, 440]),
            ]),
        ])

        clauses = split_clauses_from_blocks(ocr)
        assert len(clauses) >= 2

        # Find article clauses
        article_clauses = [c for c in clauses if c.clause_title and "第" in c.clause_title]
        assert len(article_clauses) >= 2

        # Check titles
        titles = [c.clause_title for c in article_clauses]
        assert any("项目概述" in t for t in titles if t)
        assert any("付款方式" in t for t in titles if t)

    def test_preserves_page_no(self):
        ocr = _make_ocr_result([
            (1, [
                ("title", "第一条 项目概述", 1),
                ("text", "项目描述内容。", 2),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        article = [c for c in clauses if c.clause_title and "第一条" in c.clause_title]
        assert len(article) == 1
        assert article[0].page_no == 1

    def test_preserves_bbox(self):
        ocr = _make_ocr_result([
            (1, [
                ("title", "第一条 项目概述", 1, [120, 200, 380, 235]),
                ("text", "内容。", 2, [120, 250, 900, 290]),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        article = [c for c in clauses if c.clause_title and "第一条" in c.clause_title]
        assert len(article) == 1
        assert article[0].bbox is not None
        assert article[0].bbox.x1 == 120

    def test_multi_page(self):
        ocr = _make_ocr_result([
            (1, [
                ("title", "第一条 项目概述", 1, [120, 200, 380, 235]),
                ("text", "项目描述内容。", 2),
            ]),
            (2, [
                ("title", "第二条 付款方式", 1, [120, 200, 380, 235]),
                ("text", "分三期支付。", 2),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        article_clauses = [c for c in clauses if c.clause_title and "第" in c.clause_title]
        assert len(article_clauses) >= 2

        first = [c for c in article_clauses if "第一条" in (c.clause_title or "")][0]
        assert first.page_no == 1

        second = [c for c in article_clauses if "第二条" in (c.clause_title or "")][0]
        assert second.page_no == 2

    def test_clause_crossing_pages(self):
        """A clause starts on page 1 and continues on page 2."""
        ocr = _make_ocr_result([
            (1, [
                ("title", "第一条 付款方式", 1),
                ("text", "本合同总金额为人民币1,200,000.00元，", 2),
            ]),
            (2, [
                ("text", "分三期支付：首期30%，二期40%，三期30%。", 1),
                ("title", "第二条 违约责任", 2),
                ("text", "违约金不超过合同总金额的10%。", 3),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        first = [c for c in clauses if c.clause_title and "第一条" in c.clause_title][0]
        assert first.page_no == 1
        assert first.page_end == 2

    def test_content_includes_all_blocks(self):
        ocr = _make_ocr_result([
            (1, [
                ("title", "第一条 项目概述", 1),
                ("text", "第一段描述。", 2),
                ("text", "第二段描述。", 3),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        article = [c for c in clauses if c.clause_title and "第一条" in c.clause_title][0]
        assert "第一段描述" in article.content
        assert "第二段描述" in article.content

    def test_preamble_without_marker(self):
        """Text before the first article marker should be preserved as preamble.

        With layout-aware splitting, title-type blocks before the first article
        marker get their own clauses.  The preamble is still preserved, just
        with inferred titles from the block text.
        """
        ocr = _make_ocr_result([
            (1, [
                ("text", "合同编号：HT-2024-001", 1),
                ("text", "甲方：北京日新科技有限公司", 2),
                ("title", "第一条 项目概述", 3),
                ("text", "项目描述内容。", 4),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        # Preamble blocks should appear before the first article
        first_article_idx = next(
            (i for i, c in enumerate(clauses)
             if c.clause_title and "第一条" in c.clause_title),
            None,
        )
        assert first_article_idx is not None
        assert first_article_idx >= 1  # at least the preamble before the first article

    def test_confidence_averaged(self):
        ocr = _make_ocr_result([
            (1, [
                ("title", "第一条 项目概述", 1, None, 0.9),
                ("text", "内容。", 2, None, 0.8),
                ("text", "更多内容。", 3, None, 0.7),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        article = [c for c in clauses if c.clause_title and "第一条" in c.clause_title][0]
        # Average of 0.9, 0.8, 0.7
        assert 0.79 < article.confidence < 0.81

    def test_mixed_marker_types(self):
        """Contract using both 第X条 and 一、 style markers."""
        ocr = _make_ocr_result([
            (1, [
                ("title", "第一条 项目概述", 1),
                ("text", "项目描述。", 2),
                ("title", "一、甲方权利", 3),
                ("text", "甲方有权监督。", 4),
                ("title", "二、乙方义务", 5),
                ("text", "乙方应按期交付。", 6),
            ]),
        ])
        clauses = split_clauses_from_blocks(ocr)
        assert len(clauses) >= 3
        titles = [c.clause_title for c in clauses if c.clause_title]
        assert any("第一条" in t for t in titles if t)
        assert any("甲方权利" in t for t in titles if t)
        assert any("乙方义务" in t for t in titles if t)

    def test_uses_real_mock_ocr_data(self):
        """Test with the actual mock OCR provider data."""
        from app.extraction.ocr.mock import MOCK_DETAILED_RESULT
        clauses = split_clauses_from_blocks(MOCK_DETAILED_RESULT)
        assert len(clauses) >= 8

        titles = [c.clause_title for c in clauses if c.clause_title]
        assert any("项目概述" in t for t in titles if t)
        assert any("付款方式" in t for t in titles if t)
        assert any("违约责任" in t for t in titles if t)
        assert any("保密条款" in t for t in titles if t)

        # All clauses should have valid content
        for c in clauses:
            assert c.content.strip()
            assert c.confidence > 0
