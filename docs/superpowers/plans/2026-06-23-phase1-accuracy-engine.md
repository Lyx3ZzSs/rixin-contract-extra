# Phase 1 · Accuracy Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise extraction accuracy on table-heavy contracts by upgrading the OCR layer to layout-aware parsing, make accuracy measurable with an eval harness, and stop long contracts from crashing via page-aware chunking.

**Architecture:** Additive changes to the existing OCR/LLM provider pipeline. (1) Give `OCRDetailedResult` a structured `to_markdown()` that preserves page boundaries and table blocks. (2) A pure field-accuracy metric + `@pytest.mark.eval` harness. (3) Page-aware chunk splitting + result merging wired into `QwenLLMProvider`. (4) A new `ppstructurev3` OCR provider behind the existing `OCRProvider` abstraction. (5) Switch the extraction pipeline to feed markdown to the LLM.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Pydantic 2, httpx, instructor/openai (Qwen), pytest + pytest-asyncio, PaddleOCR 3.0 PP-StructureV3 (external HTTP service).

**Spec:** `docs/superpowers/specs/2026-06-23-phase1-review-loop-and-accuracy-foundation-design.md` (commit `a806858`). This plan covers Phase 1 work items ①②③ only. Items ④–⑧ are separate plans.

## Global Constraints

- **No Alembic migrations, no ORM model changes** — all needed DB columns already exist (`ExtractedField`, `OCRBlock`). New data shapes are Pydantic types only.
- **Single-chunk contracts must behave identically to today** (zero regression). The chunking path only activates when a document spans more than `llm_chunk_pages` pages.
- **Provider abstraction preserved** — the new parser is a drop-in `OCRProvider`; `ppocr`/`mock` stay selectable.
- **`ocr_provider` Settings default stays `"mock"`** (deviates from spec §5.5 which said default `ppstructurev3`, for out-of-box safety: a fresh checkout must run without the GPU service). Production sets `OCR_PROVIDER=ppstructurev3` in `.env`.
- **Tests use mock providers** (`tests/conftest.py` forces `settings.ocr_provider="mock"`, `settings.llm_provider="mock"`). No test hits a real GPU service.
- **Commit style:** conventional commits (`feat(...)`, `test(...)`, `refactor(...)`). One commit per task step as shown.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/app/extraction/base.py` | Add `to_markdown()` to `OCRPageResult` / `OCRDetailedResult` + page-marker constant | 1 |
| `backend/tests/test_ocr_markdown.py` | Unit tests for markdown rendering | 1 |
| `backend/tests/eval/__init__.py` | Package marker | 2 |
| `backend/tests/eval/accuracy.py` | Pure `compute_field_accuracy()` metric | 2 |
| `backend/tests/eval/test_accuracy.py` | Unit tests for the metric | 2 |
| `backend/tests/eval/test_extraction_accuracy.py` | `@pytest.mark.eval` end-to-end on mock provider | 2 |
| `backend/tests/eval/samples/README.md` | Golden-sample format doc + provider-compare recipe | 2 |
| `backend/tests/conftest.py` | Register `eval` skip-unless-selected hook | 2 |
| `backend/pyproject.toml` | Register `eval` marker | 2 |
| `backend/app/extraction/llm/chunking.py` | `split_by_pages()` + `merge_results()` pure functions | 3 |
| `backend/tests/test_llm_chunking.py` | Unit tests for split + merge | 3 |
| `backend/app/extraction/llm/qwen.py` | Refactor single-call + chunked orchestration | 3 |
| `backend/app/config.py` | Add `llm_chunk_pages` | 3 |
| `backend/app/extraction/ocr/ppstructurev3.py` | New layout-aware OCR provider | 4 |
| `backend/app/extraction/ocr/__init__.py` | Register `ppstructurev3` in factory | 4 |
| `backend/tests/test_ppstructurev3_provider.py` | Fixture-based provider normalization tests | 4 |
| `backend/app/services/pipeline.py` | Feed `to_markdown()` to extraction (line ~234) | 5 |
| `backend/.env.example` | Document new config keys | 5 |

---

## Task 1: Structured Markdown output for OCR results

**Files:**
- Modify: `backend/app/extraction/base.py` (add methods to `OCRPageResult` and `OCRDetailedResult`)
- Test: `backend/tests/test_ocr_markdown.py`

**Interfaces:**
- Consumes: existing `OCRTextBlock` (`block_type`, `text`), `OCRPageResult` (`blocks`, `page_no`), `OCRDetailedResult` (`pages`).
- Produces: `OCRDetailedResult.to_markdown() -> str`, `OCRPageResult.to_markdown() -> str`, class const `OCRDetailedResult.PAGE_MARKER_TEMPLATE == "<!-- page: {page_no} -->"`. Downstream: Task 3 splits on this marker; Task 5 feeds the string to the LLM.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ocr_markdown.py`:

```python
"""Tests for OCR result → structured markdown rendering."""
from app.extraction.base import BBox, OCRDetailedResult, OCRPageResult, OCRTextBlock


def _block(btype: str, text: str, sort_order: int) -> OCRTextBlock:
    return OCRTextBlock(
        block_type=btype, text=text, bbox=BBox.from_list([0, 0, 10, 10]),
        confidence=0.9, sort_order=sort_order,
    )


def test_page_markdown_bolds_titles():
    page = OCRPageResult(page_no=1, blocks=[
        _block("title", "第一条 总则", 1),
        _block("text", "本合同如下。", 2),
    ])
    md = page.to_markdown()
    assert "**第一条 总则**" in md
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
    assert "**标题**" in md


def test_page_marker_template_constant():
    assert OCRDetailedResult.PAGE_MARKER_TEMPLATE == "<!-- page: {page_no} -->"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_ocr_markdown.py -v`
Expected: FAIL — `AttributeError: 'OCRPageResult' object has no attribute 'to_markdown'`

- [ ] **Step 3: Implement `to_markdown()`**

In `backend/app/extraction/base.py`, add a method to `OCRPageResult` (after the `full_text` property, around line 49):

```python
    def to_markdown(self) -> str:
        """Render this page's blocks as lightweight markdown.

        Titles are bolded; ``table`` blocks are emitted verbatim (their
        ``text`` is expected to already contain a markdown table when produced
        by a layout-aware parser such as PP-StructureV3); other blocks are
        plain text.
        """
        lines: list[str] = []
        for block in self.blocks:
            if block.block_type == "title":
                lines.append(f"**{block.text}**")
            else:
                lines.append(block.text)
        return "\n\n".join(lines)
```

Add a constant + method to `OCRDetailedResult` (after its `all_blocks` property, around line 69):

```python
    PAGE_MARKER_TEMPLATE = "<!-- page: {page_no} -->"

    def to_markdown(self) -> str:
        """Render the whole document as markdown with one page marker per page.

        Markers are stable HTML comments so downstream chunking can split on
        them without changing the LLM-visible text semantics.
        """
        parts: list[str] = []
        for page in self.pages:
            parts.append(self.PAGE_MARKER_TEMPLATE.format(page_no=page.page_no))
            parts.append(page.to_markdown())
        return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_ocr_markdown.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/extraction/base.py backend/tests/test_ocr_markdown.py
git commit -m "feat(ocr): add structured to_markdown() with page markers to OCR results"
```

---

## Task 2: Field-accuracy metric + eval harness

**Files:**
- Create: `backend/tests/eval/__init__.py`
- Create: `backend/tests/eval/accuracy.py`
- Create: `backend/tests/eval/test_accuracy.py`
- Create: `backend/tests/eval/test_extraction_accuracy.py`
- Create: `backend/tests/eval/samples/README.md`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: `app.services.llm_service.LLMService.extract_fields_from_text(full_text, field_definitions=...) -> ExtractionResult` (existing).
- Produces: `tests.eval.accuracy.compute_field_accuracy(extracted, golden) -> dict` (pure). A `@pytest.mark.eval` test pattern. Eval tests are **skipped unless `-m eval` is passed**.

- [ ] **Step 1: Write the failing metric tests**

Create `backend/tests/eval/__init__.py` (empty file):

```python
```

Create `backend/tests/eval/test_accuracy.py`:

```python
"""Unit tests for the field-accuracy metric (no LLM calls)."""
from tests.eval.accuracy import compute_field_accuracy


def test_true_positive():
    r = compute_field_accuracy(
        {"party-a-name": "北京日新科技有限公司"},
        {"party-a-name": "北京日新科技有限公司"},
    )
    assert r["tp"] == 1 and r["fp"] == 0 and r["fn"] == 0
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_false_positive_wrong_value():
    r = compute_field_accuracy({"amount": "100"}, {"amount": "200"})
    assert r["fp"] == 1 and r["tp"] == 0 and r["fn"] == 0
    assert r["precision"] == 0.0 and r["f1"] == 0.0


def test_false_negative_missing_value():
    r = compute_field_accuracy({"amount": ""}, {"amount": "200"})
    assert r["fn"] == 1 and r["recall"] == 0.0


def test_golden_empty_field_is_ignored():
    # Field absent from golden → not applicable, not counted.
    r = compute_field_accuracy({"amount": "100"}, {})
    assert r["tp"] == r["fp"] == r["fn"] == 0


def test_whitespace_normalized():
    r = compute_field_accuracy({"name": "  ABC  "}, {"name": "ABC"})
    assert r["tp"] == 1


def test_aggregate_metrics_mixed():
    r = compute_field_accuracy(
        {"a": "1", "b": "wrong", "c": ""},   # tp, fp, fn
        {"a": "1", "b": "2", "c": "3"},
    )
    assert r["tp"] == 1 and r["fp"] == 1 and r["fn"] == 1
    assert r["precision"] == 0.5 and r["recall"] == 0.5 and r["f1"] == 0.5


def test_per_field_verdicts_populated():
    r = compute_field_accuracy({"a": "1"}, {"a": "1", "b": "2"})
    assert r["per_field"]["a"]["verdict"] == "tp"
    assert r["per_field"]["b"]["verdict"] == "fn"
```

- [ ] **Step 2: Run metric tests to verify they fail**

Run: `cd backend && python -m pytest tests/eval/test_accuracy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.eval.accuracy'`

- [ ] **Step 3: Implement the metric**

Create `backend/tests/eval/accuracy.py`:

```python
"""Pure field-level accuracy metric for contract extraction evaluation.

Semantics (per spec §4.1):
  - A field is a True Positive when extracted (non-empty) AND equals golden.
  - False Positive: extracted non-empty but wrong.
  - False Negative: golden non-empty but extracted empty/missing.
  - Fields absent from ``golden`` (or empty golden) are "not applicable"
    and skipped — they never count against accuracy.
Values are whitespace-normalized before comparison.
"""
from __future__ import annotations


def _normalize(value: str | None) -> str:
    return (value or "").strip()


def compute_field_accuracy(
    extracted: dict[str, str | None],
    golden: dict[str, str],
) -> dict:
    tp = fp = fn = 0
    per_field: dict[str, dict] = {}
    for key in set(extracted) | set(golden):
        g = _normalize(golden.get(key))
        e = _normalize(extracted.get(key))
        if not g:
            continue  # not applicable
        if e == g:
            verdict, hit = "tp", True
        elif e:
            verdict, hit = "fp", False
        else:
            verdict, hit = "fn", False
        per_field[key] = {"golden": g, "extracted": e, "verdict": verdict}
        if verdict == "tp":
            tp += 1
        elif verdict == "fp":
            fp += 1
        else:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "per_field": per_field,
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
```

- [ ] **Step 4: Run metric tests to verify they pass**

Run: `cd backend && python -m pytest tests/eval/test_accuracy.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Register the `eval` marker + skip-by-default hook**

In `backend/pyproject.toml`, replace the `[tool.pytest.ini_options]` block:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "eval: end-to-end extraction accuracy tests (run explicitly with: -m eval)",
]
```

Append to `backend/tests/conftest.py` (after the existing `sample_pdf_content` fixture):

```python
# ---------------------------------------------------------------------------
# Eval tests: skipped unless explicitly selected with `-m eval`.
# -------------------------------------------------------------------
def pytest_collection_modifyitems(config, items):
    marker_expr = config.getoption("-m") or ""
    eval_selected = "eval" in marker_expr
    skip_eval = pytest.mark.skip(
        reason="eval tests run only with -m eval (need real/configured providers)",
    )
    for item in items:
        if "eval" in item.keywords and not eval_selected:
            item.add_marker(skip_eval)
```

- [ ] **Step 6: Write the eval end-to-end test (mock baseline)**

Create `backend/tests/eval/test_extraction_accuracy.py`:

```python
"""End-to-end extraction accuracy baseline.

This is the measurement harness plumbing. With mock providers it validates the
wiring only — real accuracy numbers require real samples under
``samples/`` and real providers configured via .env (see samples/README.md).
Run explicitly:  pytest -m eval
"""
import pytest

from app.extraction.base import FieldSpec
from app.extraction.ocr.mock import MOCK_CONTRACT_TEXT
from app.services.llm_service import LLMService
from tests.eval.accuracy import compute_field_accuracy

# Default field set (mirrors the mock contract's seeded content).
_FIELD_SPECS = [
    FieldSpec(field_key="party-a-name", field_name="甲方名称"),
    FieldSpec(field_key="party-b-name", field_name="乙方名称"),
    FieldSpec(field_key="contract-amount", field_name="合同金额"),
]

# Golden values derived from app/extraction/ocr/mock.py _MOCK_BLOCKS.
_GOLDEN = {
    "party-a-name": "北京日新科技有限公司",
    "party-b-name": "上海恒信信息技术有限公司",
    "contract-amount": "1,200,000.00",
}


@pytest.mark.eval
async def test_mock_extraction_accuracy_runs():
    extracted_result = await LLMService.extract_fields_from_text(
        MOCK_CONTRACT_TEXT, field_definitions=_FIELD_SPECS,
    )
    extracted = {f.field_key: (f.value or "") for f in extracted_result.fields}
    report = compute_field_accuracy(extracted, _GOLDEN)

    # Harness plumbing assertions (mock LLM output is canned; do not assert a
    # specific F1 here — that is the job of real-sample eval).
    assert set(report) >= {"tp", "fp", "fn", "precision", "recall", "f1", "per_field"}
    assert 0.0 <= report["f1"] <= 1.0
    assert all(k in report["per_field"] for k in _GOLDEN if k in extracted)
```

- [ ] **Step 7: Run eval test (explicitly) to verify it passes**

Run: `cd backend && python -m pytest -m eval -v`
Expected: PASS (1 test) — the mock baseline runs end-to-end through `LLMService`.

Run the default suite to confirm eval is skipped:
Run: `cd backend && python -m pytest tests/eval -v`
Expected: the `@pytest.mark.eval` test shows `SKIPPED`; `test_accuracy.py` passes (7).

- [ ] **Step 8: Document the golden-sample format**

Create `backend/tests/eval/samples/README.md`:

```markdown
# Extraction eval samples

Each sample is a pair of files sharing a base name:

- `<name>.pdf` (or `.png`/`.jpg`) — the raw contract document.
- `<name>.golden.json` — expected field values:

```json
{
  "party-a-name": "北京日新科技有限公司",
  "party-b-name": "上海恒信信息技术有限公司",
  "contract-amount": "1,200,000.00"
}
```

## Running

```
# baseline (mock providers — plumbing only):
pytest -m eval

# real provider comparison (configure .env first):
OCR_PROVIDER=ppocr      pytest -m eval     # record baseline F1
OCR_PROVIDER=ppstructurev3 pytest -m eval  # record upgraded F1
```

Compare the two F1 reports — especially on table-heavy fields
(`contract-amount`, `prepayment-ratio`, payment-schedule items) — to validate
the PP-StructureV3 upgrade (spec acceptance criterion §7.1).

Place real desensitized samples here. Until then the harness runs on the mock
provider only.
```

- [ ] **Step 9: Commit**

```bash
git add backend/tests/eval backend/tests/conftest.py backend/pyproject.toml
git commit -m "test(eval): add field-accuracy metric and -m eval harness with mock baseline"
```

---

## Task 3: Long-contract chunking & merge

**Files:**
- Create: `backend/app/extraction/llm/chunking.py`
- Create: `backend/tests/test_llm_chunking.py`
- Modify: `backend/app/extraction/llm/qwen.py` (refactor + chunked orchestration)
- Modify: `backend/app/config.py` (add `llm_chunk_pages`)

**Interfaces:**
- Consumes: `OCRDetailedResult.PAGE_MARKER_TEMPLATE` (Task 1) to split markdown; `ExtractionResult`/`ExtractedField` (`app.extraction.base`) to merge.
- Produces: `chunking.split_by_pages(markdown, pages_per_chunk=6, overlap=1) -> list[str]`, `chunking.merge_results(per_chunk: list[ExtractionResult]) -> ExtractionResult`. `QwenLLMProvider.extract_fields` now chunks internally when input spans > `settings.llm_chunk_pages` pages; single-chunk input is byte-identical to prior behavior. Config `settings.llm_chunk_pages: int` (default 6).

- [ ] **Step 1: Write the failing split/merge tests**

Create `backend/tests/test_llm_chunking.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_llm_chunking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.extraction.llm.chunking'`

- [ ] **Step 3: Implement the pure chunking module**

Create `backend/app/extraction/llm/chunking.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_llm_chunking.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Add the `llm_chunk_pages` config**

In `backend/app/config.py`, add inside `Settings` (after `llm_timeout`, line ~31):

```python
    # Long-contract chunking: pages per LLM call (1-page overlap between chunks).
    llm_chunk_pages: int = 6
```

- [ ] **Step 6: Refactor `qwen.py` for chunked orchestration**

In `backend/app/extraction/llm/qwen.py`:

(a) Add imports at the top (after the existing `from app.extraction.base import (...)` block, around line 21):

```python
from app.extraction.llm.chunking import merge_results, split_by_pages
```

(b) Rename the current single-call body of `extract_fields` into a private `_extract_single` and add chunked orchestration. Replace the existing `extract_fields` method (lines 169–216) with:

```python
    def extract_fields(
        self,
        full_text: str,
        contract_type: str | None = None,
        field_definitions: list | None = None,
    ) -> ExtractionResult:
        """Extract fields, chunking long contracts by page.

        Single-chunk input (short contracts, or text without page markers)
        takes the original one-call path — byte-identical behavior. Long
        contracts are split into overlapping page windows; each window is
        extracted independently and the results merged.
        """
        if not field_definitions:
            raise ValueError("Field definitions are required for Qwen extraction")

        chunks = split_by_pages(full_text, pages_per_chunk=settings.llm_chunk_pages)
        if len(chunks) <= 1:
            return self._extract_single(full_text, contract_type, field_definitions)

        per_chunk: list[ExtractionResult] = []
        for chunk in chunks:
            try:
                per_chunk.append(self._extract_single(chunk, contract_type, field_definitions))
            except Exception as exc:  # one bad chunk must not sink the whole doc
                logger.warning("Chunk extraction failed, skipping chunk: %s", exc)
        if not per_chunk:
            raise RuntimeError("All chunk extractions failed")
        return merge_results(per_chunk)

    def _extract_single(
        self,
        full_text: str,
        contract_type: str | None,
        field_definitions: list,
    ) -> ExtractionResult:
        """Single LLM call — the pre-chunking extraction path."""
        user_msg = _build_dynamic_prompt(field_definitions, full_text, contract_type)
        system_msg = (
            "你是一位资深合同审核专家，擅长从合同文本中精准提取结构化信息。"
        )
        logger.debug("Extraction prompt (first 500 chars): %s", user_msg[:500])
        try:
            raw: RawExtractionResult = self._client.chat.completions.create(
                model=settings.llm_model_name,
                response_model=RawExtractionResult,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                max_retries=2,
            )
        except Exception as exc:
            logger.error("Instructor extraction failed: %s", exc, exc_info=True)
            raise RuntimeError(f"Instructor extraction failed: {exc}") from exc

        fields = _convert_raw_fields(raw, field_definitions)
        return ExtractionResult(
            contract_type=raw.contract_type or contract_type,
            contract_type_confidence=raw.contract_type_confidence,
            fields=fields,
        )
```

- [ ] **Step 7: Verify the refactor didn't break the suite**

Run: `cd backend && python -m pytest tests/test_llm_chunking.py tests/test_llm_service.py -v`
Expected: PASS. (`test_llm_service.py` exercises the mock provider, not Qwen, so it confirms no import/syntax breakage. If that file name differs in the repo, run the full suite instead: `python -m pytest -q`.)

- [ ] **Step 8: Commit**

```bash
git add backend/app/extraction/llm/chunking.py backend/tests/test_llm_chunking.py \
        backend/app/extraction/llm/qwen.py backend/app/config.py
git commit -m "feat(llm): page-aware chunking and result merging for long contracts"
```

---

## Task 4: PP-StructureV3 OCR provider

**Files:**
- Create: `backend/app/extraction/ocr/ppstructurev3.py`
- Modify: `backend/app/extraction/ocr/__init__.py` (factory registration)
- Modify: `backend/app/config.py` (add `ppstructurev3_url`)
- Test: `backend/tests/test_ppstructurev3_provider.py`

**Interfaces:**
- Consumes: `OCRProvider.extract_detailed(file_path, file_type) -> OCRDetailedResult` (abstract, `ocr/base.py`); `OCRTextBlock`/`OCRPageResult`/`OCRDetailedResult`/`BBox` (`app.extraction.base`).
- Produces: `PPStructureV3Provider` registered under `settings.ocr_provider == "ppstructurev3"`. Returns an `OCRDetailedResult` whose table-region blocks carry markdown-table `text` (consumed verbatim by `to_markdown()` from Task 1).

**⚠️ Deployment note (read before implementing):** The provider parses the response contract documented below. PP-StructureV3 is served via PaddleX / PaddleOCR 3.0; the exact JSON field names (`results`/`regions`/`type`/`bbox`/`confidence`) vary by deployment version. **Step 3's parser is written against the documented contract; the implementer must confirm the field names against their live PaddleX endpoint** and adjust the `_extract_payload` key paths accordingly. The fixture in Step 1 pins the contract the parser must satisfy.

- [ ] **Step 1: Write the failing provider tests (fixture-based)**

Create `backend/tests/test_ppstructurev3_provider.py`:

```python
"""PP-StructureV3 provider normalization tests (no live HTTP).

The provider is given a captured response payload (the contract a PaddleX
PP-StructureV3 endpoint must satisfy) and must turn it into an
``OCRDetailedResult`` with correct block types, page numbers and table text.
"""
from unittest.mock import MagicMock, patch

from app.extraction.ocr.ppstructurev3 import PPStructureV3Provider

# Representative PP-StructureV3 response (the contract this parser supports).
_FIXTURE = {
    "results": [
        {
            "page_no": 1,
            "regions": [
                {"type": "title", "text": "第一条 付款方式", "bbox": [120, 80, 380, 115], "confidence": 0.97},
                {"type": "table",
                 "text": "| 期次 | 比例 | 金额 |\n|---|---|---|\n| 1 | 30% | 36万 |",
                 "bbox": [120, 130, 900, 300], "confidence": 0.92},
                {"type": "text", "text": "本合同总金额为120万元。", "bbox": [120, 310, 900, 340], "confidence": 0.95},
            ],
        },
        {
            "page_no": 2,
            "regions": [
                {"type": "text", "text": "第二页内容。", "bbox": [120, 80, 900, 110], "confidence": 0.93},
            ],
        },
    ]
}


def _provider_returning(payload):
    p = PPStructureV3Provider()
    p._http_post = MagicMock(return_value=payload)  # type: ignore[method-assign]
    return p


def test_normalizes_two_pages():
    result = _provider_returning(_FIXTURE).extract_detailed("/tmp/x.pdf", "pdf")
    assert len(result.pages) == 2
    assert result.pages[0].page_no == 1
    assert result.pages[1].page_no == 2


def test_block_types_and_table_text_preserved():
    result = _provider_returning(_FIXTURE).extract_detailed("/tmp/x.pdf", "pdf")
    page1 = result.pages[0]
    assert [b.block_type for b in page1.blocks] == ["title", "table", "text"]
    table_block = page1.blocks[1]
    assert table_block.text.startswith("| 期次 | 比例 | 金额 |")
    assert table_block.bbox is not None
    assert table_block.confidence == 0.92


def test_sort_order_assigned_in_reading_order():
    result = _provider_returning(_FIXTURE).extract_detailed("/tmp/x.pdf", "pdf")
    assert [b.sort_order for b in result.pages[0].blocks] == [1, 2, 3]


def test_empty_regions_page_is_skipped_silently():
    payload = {"results": [{"page_no": 1, "regions": []}]}
    result = _provider_returning(payload).extract_detailed("/tmp/x.pdf", "pdf")
    assert result.pages == [] or all(not p.blocks for p in result.pages)


def test_malformed_payload_raises():
    import pytest
    with pytest.raises(RuntimeError):
        _provider_returning({"unexpected": True}).extract_detailed("/tmp/x.pdf", "pdf")


def test_http_failure_retried_then_raises(monkeypatch):
    import pytest
    import httpx
    p = PPStructureV3Provider()
    call_count = {"n": 0}

    def boom(url, payload):
        call_count["n"] += 1
        raise httpx.HTTPError("boom")

    p._http_post = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        p.extract_detailed("/tmp/x.pdf", "pdf")
    assert call_count["n"] >= 2  # retried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_ppstructurev3_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.extraction.ocr.ppstructurev3'`

- [ ] **Step 3: Implement the provider**

Create `backend/app/extraction/ocr/ppstructurev3.py`:

```python
"""PP-StructureV3 OCR provider (PaddleOCR 3.0 layout-aware parsing).

Unlike PP-OCR (text-only), PP-StructureV3 returns layout regions (title /
text / table / figure / list) with bounding boxes, table structure, reading
order and formula/stamp recognition. Table regions carry markdown-table text
so downstream ``to_markdown()`` preserves cell structure for the LLM.

Response contract parsed here (verify against your live PaddleX deployment):
    {
      "results": [
        {"page_no": 1, "regions": [
            {"type": "title"|"text"|"table"|..., "text": str,
             "bbox": [x1,y1,x2,y2], "confidence": float}
        ]}
      ]
    }
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

import httpx

from app.config import settings
from app.extraction.base import (
    BBox,
    OCRDetailedResult,
    OCRPageResult,
    OCRTextBlock,
)
from app.extraction.ocr.base import OCRProvider

logger = logging.getLogger(__name__)

_HTTP_RETRIES = 2
_RETRY_BACKOFF = 0.5


class PPStructureV3Provider(OCRProvider):
    """Calls the PP-StructureV3 HTTP service and normalizes its response."""

    def __init__(self) -> None:
        self._url = settings.ppstructurev3_url
        self._timeout = settings.ppocr_timeout  # reuse the existing timeout knob
        self._client = httpx.Client(timeout=self._timeout)

    # ------------------------------------------------------------------
    # OCRProvider interface
    # ------------------------------------------------------------------

    def extract_detailed(self, file_path: str, file_type: str) -> OCRDetailedResult:
        payload = self._build_payload(file_path, file_type)
        data = self._http_post(self._url, payload)
        return self._normalize(data)

    # ------------------------------------------------------------------
    # HTTP (mockable seam — tests override _http_post)
    # ------------------------------------------------------------------

    def _http_post(self, url: str, payload: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(_HTTP_RETRIES + 1):
            try:
                resp = self._client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("PP-StructureV3 HTTP error (attempt %d): %s", attempt + 1, exc)
                if attempt < _HTTP_RETRIES:
                    time.sleep(_RETRY_BACKOFF * (attempt + 1))
        raise RuntimeError(f"PP-StructureV3 request failed after retries: {last_exc}")

    @staticmethod
    def _build_payload(file_path: str, file_type: str) -> dict:
        path = Path(file_path)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        # fileType: 0 = pdf, 1 = image (matches the existing PP-OCR convention).
        kind = 0 if file_type.lower() == "pdf" else 1
        return {"file": encoded, "fileType": kind, "useLayout": True, "useTable": True}

    # ------------------------------------------------------------------
    # Response normalization
    # ------------------------------------------------------------------

    def _normalize(self, data: dict) -> OCRDetailedResult:
        results = self._extract_payload(data)
        if results is None:
            raise RuntimeError("PP-StructureV3 returned no parseable results")

        pages: list[OCRPageResult] = []
        for idx, page_raw in enumerate(results):
            page_no = int(page_raw.get("page_no", idx + 1))
            blocks = self._blocks_for(page_raw)
            if not blocks:
                continue
            pages.append(OCRPageResult(page_no=page_no, blocks=blocks))
        return OCRDetailedResult(pages=pages, provider="ppstructurev3")

    @staticmethod
    def _extract_payload(data: dict) -> list[dict] | None:
        """Return the list of page objects from a PP-StructureV3 response.

        Handles common wrapper keys; adjust to match the live deployment.
        """
        if not isinstance(data, dict):
            return None
        for key in ("results", "data", "pages"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        return None

    @staticmethod
    def _blocks_for(page_raw: dict) -> list[OCRTextBlock]:
        regions = page_raw.get("regions") or page_raw.get("layout_regions") or []
        blocks: list[OCRTextBlock] = []
        for sort_order, region in enumerate(regions, start=1):
            text = (region.get("text") or "").strip()
            if not text:
                continue
            bbox = PPStructureV3Provider._bbox(region.get("bbox"))
            blocks.append(OCRTextBlock(
                block_type=region.get("type", "text"),
                text=text,
                bbox=bbox,
                confidence=float(region.get("confidence", 0.0)),
                sort_order=sort_order,
            ))
        return blocks

    @staticmethod
    def _bbox(raw) -> BBox | None:
        if isinstance(raw, list) and len(raw) == 4:
            try:
                return BBox(x1=float(raw[0]), y1=float(raw[1]), x2=float(raw[2]), y2=float(raw[3]))
            except (TypeError, ValueError):
                return None
        return None
```

- [ ] **Step 4: Run provider tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_ppstructurev3_provider.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Register the provider in the factory**

Replace `backend/app/extraction/ocr/__init__.py` with:

```python
from app.config import settings
from app.extraction.ocr.base import OCRProvider

def get_ocr_provider() -> OCRProvider:
    name = settings.ocr_provider
    if name == "mock":
        from app.extraction.ocr.mock import MockOCRProvider
        return MockOCRProvider()
    if name == "ppocr":
        from app.extraction.ocr.ppocr import PPOCRProvider
        return PPOCRProvider()
    if name == "ppstructurev3":
        from app.extraction.ocr.ppstructurev3 import PPStructureV3Provider
        return PPStructureV3Provider()
    raise ValueError(f"Unknown OCR provider: {name}")
```

- [ ] **Step 6: Add the config key**

In `backend/app/config.py`, add inside `Settings` (after the PP-OCR block, around line 23):

```python
    # PP-StructureV3 (layout-aware: tables, regions, reading order)
    ppstructurev3_url: str = "http://10.8.7.76:8082/structure"
```

- [ ] **Step 7: Confirm the suite still selects `mock` and passes**

Run: `cd backend && python -m pytest tests/test_ppstructurev3_provider.py tests/test_ocr_markdown.py -q`
Expected: PASS — `conftest.py` keeps the default on `mock`; the new provider is only instantiated when configured.

- [ ] **Step 8: Commit**

```bash
git add backend/app/extraction/ocr/ppstructurev3.py backend/app/extraction/ocr/__init__.py \
        backend/app/config.py backend/tests/test_ppstructurev3_provider.py
git commit -m "feat(ocr): add PP-StructureV3 layout-aware provider with table structure support"
```

---

## Task 5: Feed structured markdown to the extraction pipeline

**Files:**
- Modify: `backend/app/services/pipeline.py` (line ~234: switch `full_text` → `to_markdown()`)
- Modify: `backend/.env.example` (document new keys)
- Test: extend `backend/tests/eval/test_extraction_accuracy.py` to assert the pipeline feeds markdown (regression guard)

**Interfaces:**
- Consumes: `OCRDetailedResult.to_markdown()` (Task 1); `extract_and_save(db, contract_id, full_text, field_definitions)` (existing). `llm_chunk_pages` / `ppstructurev3_url` config (Tasks 3–4).
- Produces: the extraction LLM now receives page-marked, table-structured markdown instead of flat text. Long contracts chunk correctly (Task 3) because the markers are present.

- [ ] **Step 1: Add the input-contract guard test**

Append to `backend/tests/eval/test_extraction_accuracy.py`:

```python
@pytest.mark.eval
async def test_pipeline_input_markdown_has_page_markers():
    """Input-contract guard for the Task 5 wiring change.

    The markdown the pipeline now feeds to extraction (OCRDetailedResult.to_markdown())
    must carry page markers — Task 3 chunking splits on them. This pins that
    contract. The wiring itself (pipeline.py passing to_markdown() to
    extract_and_save) is verified end-to-end by the existing extraction-pipeline
    test in test_task_api.py, which must stay green after the one-line change.
    """
    from app.extraction.ocr.mock import MOCK_DETAILED_RESULT
    assert "<!-- page:" in MOCK_DETAILED_RESULT.to_markdown()
```

- [ ] **Step 2: Run the test to verify the markdown contract holds**

Run: `cd backend && python -m pytest -m eval -v`
Expected: PASS — `MOCK_DETAILED_RESULT.to_markdown()` already contains page markers (Task 1). This test locks the contract so the Task-5 wiring change cannot silently revert.

- [ ] **Step 3: Switch the pipeline to feed markdown**

In `backend/app/services/pipeline.py`, in `_run_extraction_pipeline_inner`, change the extraction call (lines ~230–236) from:

```python
            from app.services.extraction_service import extract_and_save
            extraction = await extract_and_save(
                db,
                contract_id,
                ocr_result.full_text,
                field_definitions=field_definitions,
            )
```

to:

```python
            from app.services.extraction_service import extract_and_save
            # Feed page-marked, table-structured markdown (not flat text) so the
            # LLM sees table structure and chunking can split on page markers.
            extraction = await extract_and_save(
                db,
                contract_id,
                ocr_result.to_markdown(),
                field_definitions=field_definitions,
            )
```

Leave the title extraction above it (`_extract_title(ocr_result.full_text)`, line 175) on plain `full_text` — titles must not pick up markdown syntax.

- [ ] **Step 4: Run the full suite to confirm no regression**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all non-eval tests green; eval tests skipped.

- [ ] **Step 5: Document the new config keys**

Append to `backend/.env.example` (create sections for the new keys; keep existing keys intact):

```ini
# Long-contract chunking: pages per LLM call (1-page overlap)
LLM_CHUNK_PAGES=6

# PP-StructureV3 layout-aware OCR service (PaddleOCR 3.0 / PaddleX serving)
PPSTRUCTUREV3_URL=http://10.8.7.76:8082/structure

# Production OCR provider: ppstructurev3 (layout/tables) | ppocr (text-only) | mock
OCR_PROVIDER=ppstructurev3
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/pipeline.py backend/.env.example backend/tests/eval/test_extraction_accuracy.py
git commit -m "feat(pipeline): feed structured markdown to extraction so chunking and tables work"
```

---

## Self-Review (run after writing, before handoff)

**1. Spec coverage (Phase 1 items ①②③):**
- ① 评测集/回归基线 → Task 2 (metric + harness + golden format + skip-by-default). ✓
- ② PP-StructureV3 集成 → Task 4 (provider + config + factory) + Task 1 (table markdown contract) + Task 5 (pipeline feeds markdown). ✓
- ③ 长合同分块 → Task 3 (split + merge + qwen wiring + config) + Task 5 (markers present via markdown). ✓
- D6 (mock baseline, real-sample ready) → Task 2 samples/README + mock eval test. ✓
- D10 (markdown to LLM) → Task 5. ✓
- D3 (page-granularity chunking) → Task 3. ✓

**2. Placeholder scan:** No TBD/TODO. The only "verify against deployment" note (Task 4 ⚠️) is a documented integration reality with a fixture-pinned contract — not a placeholder.

**3. Type consistency:**
- `to_markdown()` defined Task 1, consumed Tasks 3 & 5. ✓
- `PAGE_MARKER_TEMPLATE` constant (Task 1) matches `_PAGE_MARKER_RE` regex (Task 3) — both `<!-- page: {n} -->`. ✓
- `split_by_pages` / `merge_results` signatures identical in Task 3 spec and qwen.py call site. ✓
- `compute_field_accuracy(extracted, golden)` signature identical in Task 2 impl and eval test. ✓
- `PPStructureV3Provider._http_post` overridden in tests matches the impl's signature `(self, url, payload) -> dict`. ✓
- `settings.llm_chunk_pages` (Task 3) and `settings.ppstructurev3_url` (Task 4) added to `config.py`. ✓

No gaps found for items ①②③. (Items ④–⑧ are intentionally in separate plans.)

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-23-phase1-accuracy-engine.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach? (And after Plan 1 lands, I'll write Plan 2 — Product Review Loop — and Plan 3 — Minimum Auth.)
