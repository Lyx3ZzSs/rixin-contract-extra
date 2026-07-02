"""Tests for OCR-block bbox backfill matching.

Covers the text-based-PDF fix: when PyMuPDF splits a clause across several
blocks, the backfill must (a) score candidates by character-bigram overlap
instead of pure substring containment, and (b) merge adjacent same-page blocks
into a bbox union when no single block clears the threshold — without
degrading the scanned-PDF exact-hit path.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.extraction.base import BBox, ExtractedField, ExtractionResult, FieldSpec
from app.models.contract import ExtractedField as ExtractedFieldRecord
from app.models.ocr import OCRBlock
from app.services.extraction_service import (
    _attach_source_bboxes,
    _char_bigrams,
    _containment,
    _score_block,
    _sort_order_contiguous,
    _union_bbox,
    _window_coverage,
)
from tests.conftest import test_session_factory


# ---------------------------------------------------------------------------
# Pure-function unit tests
# ---------------------------------------------------------------------------

def test_char_bigrams_mixed_text():
    grams = _char_bigrams("甲方Ab1")
    assert "甲方" in grams and "方A" in grams and "Ab" in grams and "b1" in grams


def test_char_bigrams_short_inputs():
    assert _char_bigrams("") == frozenset()
    assert _char_bigrams("甲") == frozenset({"甲"})


def test_containment_extremes():
    full = _char_bigrams("甲方名称是北京日新科技")
    part = _char_bigrams("甲方名称")
    # _containment(haystack, needle) = fraction of needle's bigrams in haystack
    assert _containment(full, part) == 1.0            # part fully inside full
    assert _containment(part, full) == len(part & full) / len(full)  # full only partly in part
    disjoint = _char_bigrams("zzzzz")
    assert _containment(full, disjoint) == 0.0
    assert _containment(full, frozenset()) == 0.0


def test_score_block_exact_containment_clears_threshold():
    # Whole source sits inside one block -> scanned-PDF fast path.
    source = _char_bigrams("甲方：北京日新科技有限公司")
    block = _char_bigrams("甲方：北京日新科技有限公司成立于2010年")
    assert _score_block(source, block) >= 0.85  # _TOKEN_THRESHOLD


def test_score_block_fragment_scores_low():
    # Block is only a fragment of a long source -> must NOT clear threshold,
    # so it routes into the merge path.
    source = _char_bigrams("乙方应在合同签订后三日内向甲方支付首期款项人民币拾万元整。")
    fragment = _char_bigrams("向甲方支付首期款项人民币拾万元整。")
    assert _score_block(source, fragment) < 0.85


def test_score_block_disjoint_is_low():
    source = _char_bigrams("甲方：北京日新科技有限公司")
    other = _char_bigrams("乙方：上海某某贸易公司")
    assert _score_block(source, other) < 0.2


def test_score_block_empty_inputs():
    empty = frozenset()
    grams = _char_bigrams("甲方")
    assert _score_block(empty, grams) == 0.0
    assert _score_block(grams, empty) == 0.0


def test_window_coverage_full_span():
    source = _char_bigrams("乙方应在合同签订后三日内向甲方支付首期款项人民币拾万元整。")
    window = _char_bigrams("乙方应在合同签订后三日内" + "向甲方支付首期款项人民币拾万元整。")
    assert _window_coverage(source, window) == 1.0
    partial = _char_bigrams("乙方应在合同签订后三日内")
    assert _window_coverage(source, partial) < 0.7  # _MERGE_THRESHOLD


def test_union_bbox_merges_and_ignores_none():
    a = BBox(x1=10, y1=20, x2=100, y2=80)
    b = BBox(x1=50, y1=60, x2=200, y2=150)
    union = _union_bbox([a, None, b])
    assert union is not None
    assert (union.x1, union.y1) == (10, 20)
    assert (union.x2, union.y2) == (200, 150)
    assert _union_bbox([None, None]) is None


def _rows(sort_orders):
    return [SimpleNamespace(sort_order=s) for s in sort_orders]


def test_sort_order_contiguous():
    rows = _rows([1, 2, 3])
    assert _sort_order_contiguous(rows, [0, 1, 2]) is True
    # gap of 2 still considered adjacent
    rows = _rows([1, 3, 5])
    assert _sort_order_contiguous(rows, [0, 1, 2]) is True
    # gap > 2 breaks contiguity
    rows = _rows([1, 2, 10])
    assert _sort_order_contiguous(rows, [0, 1, 2]) is False


# ---------------------------------------------------------------------------
# Backfill behaviour tests (DB-backed, mirroring test_services.py style)
# ---------------------------------------------------------------------------

def _bbox_dict(x1, y1, x2, y2):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


async def _seed_contract_with_blocks(db, content_hash, blocks):
    """blocks: list[dict(page_no, sort_order, text, bbox)]"""
    from app.services.contract_service import create_contract
    contract = await create_contract(db, content_hash=content_hash)
    for b in blocks:
        db.add(OCRBlock(
            contract_id=contract.id,
            page_no=b["page_no"],
            block_type="text",
            text=b["text"],
            bbox=b.get("bbox"),
            confidence=0.95,
            sort_order=b["sort_order"],
            page_width=1240,
            page_height=1754,
        ))
    return contract


@pytest.mark.asyncio
async def test_clause_split_across_two_blocks_merges_bbox():
    """Text-based PDF: PyMuPDF split one clause into two consecutive blocks.
    The merged window must take the union bbox covering both halves."""
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        contract = await _seed_contract_with_blocks(db, "merge-2-blocks", [
            {"page_no": 1, "sort_order": 1, "text": "乙方应在合同签订后三日内",
             "bbox": _bbox_dict(100, 200, 600, 240)},
            {"page_no": 1, "sort_order": 2, "text": "向甲方支付首期款项人民币拾万元整。",
             "bbox": _bbox_dict(100, 245, 600, 285)},
        ])
        field = ExtractedField(
            field_key="payment-term", field_name="付款条款",
            value="首期款项",
            source_text="乙方应在合同签订后三日内向甲方支付首期款项人民币拾万元整。",
            page_no=1, confidence=0.9,
        )
        await _attach_source_bboxes(db, contract.id, [field])
        assert field.bbox is not None
        # union of both blocks
        assert field.bbox.x1 == 100 and field.bbox.y1 == 200
        assert field.bbox.x2 == 600 and field.bbox.y2 == 285


@pytest.mark.asyncio
async def test_clause_split_across_three_blocks_merges_bbox():
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        contract = await _seed_contract_with_blocks(db, "merge-3-blocks", [
            {"page_no": 1, "sort_order": 1, "text": "甲方同意将其拥有的",
             "bbox": _bbox_dict(80, 300, 580, 340)},
            {"page_no": 1, "sort_order": 2, "text": "位于北京市朝阳区的",
             "bbox": _bbox_dict(80, 345, 580, 385)},
            {"page_no": 1, "sort_order": 3, "text": "办公场地出租给乙方使用。",
             "bbox": _bbox_dict(80, 390, 580, 430)},
        ])
        field = ExtractedField(
            field_key="lease-subject", field_name="租赁标的",
            value="办公场地",
            source_text="甲方同意将其拥有的位于北京市朝阳区的办公场地出租给乙方使用。",
            page_no=1, confidence=0.9,
        )
        await _attach_source_bboxes(db, contract.id, [field])
        assert field.bbox is not None
        assert field.bbox.y1 == 300 and field.bbox.y2 == 430


@pytest.mark.asyncio
async def test_noncontiguous_sort_order_does_not_merge():
    """Blocks far apart in sort_order (different section/column) must not merge."""
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        contract = await _seed_contract_with_blocks(db, "no-merge-gap", [
            {"page_no": 1, "sort_order": 1, "text": "首期款支付时间为签约后三日。",
             "bbox": _bbox_dict(100, 200, 600, 240)},
            {"page_no": 1, "sort_order": 8, "text": "尾款支付时间为验收后五日。",
             "bbox": _bbox_dict(100, 800, 600, 840)},
        ])
        # source spans both, but they are not contiguous -> no merge, falls back
        field = ExtractedField(
            field_key="payment", field_name="付款",
            value="分期",
            source_text="首期款支付时间为签约后三日。尾款支付时间为验收后五日。",
            page_no=1, confidence=0.9,
        )
        await _attach_source_bboxes(db, contract.id, [field])
        # Should not be the union (which would span y 200..840).
        if field.bbox is not None:
            assert not (field.bbox.y1 == 200 and field.bbox.y2 == 840)


@pytest.mark.asyncio
async def test_exact_single_block_hit_no_merge():
    """Scanned-PDF fast path: source fully in one block -> single block bbox,
    no merge triggered (must equal the pre-fix behaviour)."""
    from tests.conftest import test_session_factory
    async with test_session_factory() as db:
        contract = await _seed_contract_with_blocks(db, "exact-hit", [
            {"page_no": 1, "sort_order": 1, "text": "甲方：北京日新科技有限公司",
             "bbox": _bbox_dict(120, 140, 520, 175)},
            {"page_no": 1, "sort_order": 2, "text": "乙方：上海某某贸易公司",
             "bbox": _bbox_dict(120, 180, 520, 215)},
        ])
        field = ExtractedField(
            field_key="party-a-name", field_name="甲方名称",
            value="北京日新科技有限公司",
            source_text="甲方：北京日新科技有限公司",
            page_no=1, confidence=0.96,
        )
        await _attach_source_bboxes(db, contract.id, [field])
        assert field.bbox is not None
        assert (field.bbox.x1, field.bbox.y1, field.bbox.x2, field.bbox.y2) \
            == (120, 140, 520, 175)


@pytest.mark.asyncio
async def test_all_below_threshold_keeps_none():
    """Nothing matches -> bbox stays None (front-end shows page-only)."""
    async with test_session_factory() as db:
        contract = await _seed_contract_with_blocks(db, "no-match", [
            {"page_no": 1, "sort_order": 1, "text": "本合同由双方友好协商签订。",
             "bbox": _bbox_dict(100, 200, 600, 240)},
        ])
        field = ExtractedField(
            field_key="x", field_name="x",
            value="无关值",
            source_text="一个与任何OCR块都不相关的超长提取原文描述内容",
            page_no=1, confidence=0.9,
        )
        await _attach_source_bboxes(db, contract.id, [field])
        assert field.bbox is None


@pytest.mark.asyncio
async def test_short_source_text_skipped():
    """Source below _SOURCE_MIN_LEN is unreliable to score -> skipped, bbox None."""
    async with test_session_factory() as db:
        contract = await _seed_contract_with_blocks(db, "short-src", [
            {"page_no": 1, "sort_order": 1, "text": "甲方信息",
             "bbox": _bbox_dict(100, 200, 600, 240)},
        ])
        field = ExtractedField(
            field_key="x", field_name="x",
            value="甲方",
            source_text="甲方",  # length 2 < 6
            page_no=1, confidence=0.9,
        )
        await _attach_source_bboxes(db, contract.id, [field])
        assert field.bbox is None


@pytest.mark.asyncio
async def test_window_with_missing_bbox_uses_remaining_union():
    """If one block in the merge window has no bbox, the union uses the others."""
    async with test_session_factory() as db:
        contract = await _seed_contract_with_blocks(db, "merge-nil-bbox", [
            {"page_no": 1, "sort_order": 1, "text": "乙方应在合同签订后三日内",
             "bbox": _bbox_dict(100, 200, 600, 240)},
            {"page_no": 1, "sort_order": 2, "text": "向甲方支付首期款项人民币拾万元整。",
             "bbox": None},
        ])
        field = ExtractedField(
            field_key="payment-term", field_name="付款条款",
            value="首期款项",
            source_text="乙方应在合同签订后三日内向甲方支付首期款项人民币拾万元整。",
            page_no=1, confidence=0.9,
        )
        await _attach_source_bboxes(db, contract.id, [field])
        assert field.bbox is not None
        # only first block has bbox
        assert (field.bbox.x1, field.bbox.y1, field.bbox.x2, field.bbox.y2) \
            == (100, 200, 600, 240)
