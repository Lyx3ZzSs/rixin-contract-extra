"""Tests for page-aware chunking and per-chunk result merging."""
from app.extraction.base import ExtractedField, ExtractionResult
from app.extraction.llm.chunking import merge_results, split_by_pages


def _md(pages: int) -> str:
    return "\n\n".join(f"<!-- page: {n} -->\n\npage {n} body" for n in range(1, pages + 1))


# --- split_by_pages -----------------------------------------------------

def test_split_short_text_returns_single_chunk():
    chunks = split_by_pages(_md(3), pages_per_chunk=6)
    assert chunks == [_md(3)]


def test_split_no_markers_returns_single_chunk():
    chunks = split_by_pages("just plain text no markers", pages_per_chunk=6)
    assert chunks == ["just plain text no markers"]


def test_split_empty_returns_empty():
    assert split_by_pages("   ", pages_per_chunk=6) == []


def test_split_long_text_chunks_by_page_with_overlap():
    chunks = split_by_pages(_md(10), pages_per_chunk=6, overlap=1)
    assert len(chunks) == 2
    # chunk 0 covers pages 1-6; chunk 1 covers pages 6-10 (page 6 overlaps)
    assert "<!-- page: 1 -->" in chunks[0] and "<!-- page: 6 -->" in chunks[0]
    assert "<!-- page: 6 -->" in chunks[1] and "<!-- page: 10 -->" in chunks[1]
    assert "<!-- page: 1 -->" not in chunks[1]


def test_split_rejects_invalid_window():
    import pytest
    with pytest.raises(ValueError):
        split_by_pages(_md(10), pages_per_chunk=0)


# --- merge_results ------------------------------------------------------

def _f(key, value, conf=0.9, page=1):
    return ExtractedField(field_key=key, value=value, confidence=conf, page_no=page)


def test_merge_non_empty_beats_empty():
    a = ExtractionResult(fields=[_f("x", None, 0.9)])
    b = ExtractionResult(fields=[_f("x", "VAL", 0.5)])
    merged = merge_results([a, b])
    assert merged.fields[0].value == "VAL"


def test_merge_higher_confidence_wins():
    a = ExtractionResult(fields=[_f("x", "A", 0.9)])
    b = ExtractionResult(fields=[_f("x", "B", 0.99)])
    merged = merge_results([a, b])
    assert merged.fields[0].value == "B"


def test_merge_confidence_tie_earlier_page_wins():
    a = ExtractionResult(fields=[_f("x", "LATE", 0.9, page=5)])
    b = ExtractionResult(fields=[_f("x", "EARLY", 0.9, page=1)])
    merged = merge_results([a, b])
    assert merged.fields[0].value == "EARLY"


def test_merge_both_empty_keeps_first():
    a = ExtractionResult(fields=[_f("x", None, 0.9, page=1)])
    b = ExtractionResult(fields=[_f("x", None, 0.9, page=2)])
    merged = merge_results([a, b])
    assert len(merged.fields) == 1
    assert merged.fields[0].value is None


def test_merge_picks_first_known_contract_type():
    a = ExtractionResult(contract_type="unknown", contract_type_confidence=0.1, fields=[])
    b = ExtractionResult(contract_type="service", contract_type_confidence=0.9, fields=[])
    merged = merge_results([a, b])
    assert merged.contract_type == "service" and merged.contract_type_confidence == 0.9


def test_merge_disjoint_keys_all_kept():
    a = ExtractionResult(fields=[_f("x", "1")])
    b = ExtractionResult(fields=[_f("y", "2")])
    merged = merge_results([a, b])
    assert {f.field_key for f in merged.fields} == {"x", "y"}
