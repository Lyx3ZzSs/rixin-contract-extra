"""PP-StructureV3 provider normalization tests (no live HTTP).

The provider is given a captured response payload (the contract a PaddleX
PP-StructureV3 endpoint must satisfy) and must turn it into an
``OCRDetailedResult`` with correct block types, page numbers and table text.
"""
from unittest.mock import MagicMock

import httpx
import pytest

from app.extraction.ocr.ppstructurev3 import PPStructureV3Provider


@pytest.fixture(autouse=True)
def _stub_build_payload(monkeypatch):
    """``extract_detailed`` calls ``_build_payload`` before the mocked
    ``_http_post``. The brief's tests pass placeholder paths like
    ``"/tmp/x.pdf"``; stub ``_build_payload`` so no real file read occurs.
    """
    monkeypatch.setattr(
        "app.extraction.ocr.ppstructurev3.PPStructureV3Provider._build_payload",
        staticmethod(lambda file_path, file_type: {"file": "stub", "fileType": 0}),
    )


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
    with pytest.raises(RuntimeError):
        _provider_returning({"unexpected": True}).extract_detailed("/tmp/x.pdf", "pdf")


def test_http_failure_retried_then_raises():
    p = PPStructureV3Provider()
    call_count = {"n": 0}

    class _FakeClient:
        def post(self, *args, **kwargs):
            call_count["n"] += 1
            raise httpx.HTTPError("boom")

    # Retry logic lives INSIDE _http_post (which calls self._get_client().post),
    # so mock _get_client to return a fake client whose .post always raises —
    # this actually exercises the retry loop.
    p._get_client = lambda: _FakeClient()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        p.extract_detailed("/tmp/x.pdf", "pdf")
    assert call_count["n"] >= 2  # retried (_HTTP_RETRIES + 1 attempts)
