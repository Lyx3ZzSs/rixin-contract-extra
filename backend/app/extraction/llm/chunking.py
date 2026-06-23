"""Page-aware chunking and per-chunk extraction-result merging.

The OCR layer emits one ``<!-- page: N -->`` marker per page (see
``OCRDetailedResult.to_markdown``). Long contracts are split into windowed
chunks so each LLM call stays within the model context window; results are
merged with deterministic arbitration.
"""
from __future__ import annotations

import re

from app.extraction.base import ExtractionResult, ExtractedField

# Matches a line that is exactly a page marker produced by to_markdown().
_PAGE_MARKER_RE = re.compile(
    r"^" + re.escape("<!-- page: ") + r"(\d+)" + re.escape(" -->") + r"$",
    re.MULTILINE,
)
# Sentinel used when a field has no page number.
_NO_PAGE = float("inf")


def split_by_pages(
    markdown: str,
    pages_per_chunk: int = 6,
    overlap: int = 1,
) -> list[str]:
    """Split ``markdown`` into chunks of at most ``pages_per_chunk`` pages.

    Consecutive chunks overlap by ``overlap`` pages so fields that straddle a
    boundary remain extractable from at least one chunk. Returns the whole
    string as a single chunk when the document has fewer pages than the window
    (including the marker-less case).
    """
    if pages_per_chunk < 1:
        raise ValueError("pages_per_chunk must be >= 1")
    if not markdown.strip():
        return []
    starts = [m.start() for m in _PAGE_MARKER_RE.finditer(markdown)]
    page_count = len(starts)
    if page_count <= pages_per_chunk:
        return [markdown]
    step = max(1, pages_per_chunk - overlap)
    chunks: list[str] = []
    i = 0
    while i < page_count:
        end = min(i + pages_per_chunk, page_count)
        chunk_start = starts[i]
        chunk_end = starts[end] if end < page_count else len(markdown)
        chunks.append(markdown[chunk_start:chunk_end])
        if end >= page_count:
            break
        i += step
    return chunks


def _is_empty(field: ExtractedField) -> bool:
    return not (field.value and field.value.strip())


def _beats(candidate: ExtractedField, current: ExtractedField) -> bool:
    """True when ``candidate`` should replace ``current`` for the same key."""
    cand_empty, cur_empty = _is_empty(candidate), _is_empty(current)
    if cand_empty != cur_empty:
        return not cand_empty                       # non-empty beats empty
    if cand_empty:
        return False                                # both empty → keep current
    if candidate.confidence != current.confidence:
        return candidate.confidence > current.confidence
    cand_page = candidate.page_no if candidate.page_no is not None else _NO_PAGE
    cur_page = current.page_no if current.page_no is not None else _NO_PAGE
    return cand_page < cur_page                     # tie → earlier page wins


def merge_results(per_chunk: list[ExtractionResult]) -> ExtractionResult:
    """Merge per-chunk results into one with deterministic field arbitration."""
    best: dict[str, ExtractedField] = {}
    contract_type: str | None = None
    contract_type_confidence = 0.0
    for res in per_chunk:
        if (contract_type in (None, "unknown")
                and res.contract_type
                and res.contract_type != "unknown"):
            contract_type = res.contract_type
            contract_type_confidence = res.contract_type_confidence
        for field in res.fields:
            cur = best.get(field.field_key)
            if cur is None or _beats(field, cur):
                best[field.field_key] = field
    return ExtractionResult(
        contract_type=contract_type,
        contract_type_confidence=contract_type_confidence,
        fields=list(best.values()),
    )


# Re-export so callers don't need a second import.
__all__ = ["split_by_pages", "merge_results"]
