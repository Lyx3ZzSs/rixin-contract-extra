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
