"""PP-StructureV3 provider normalization tests (no live HTTP).

The provider is given a captured response payload matching the real PaddleX
PP-StructureV3 serving contract and must turn it into an ``OCRDetailedResult``
with correct block types, page numbers and table text.
"""
from unittest.mock import MagicMock

import httpx
import pytest

from app.extraction.ocr.ppstructurev3 import PPStructureV3Provider


@pytest.fixture(autouse=True)
def _stub_build_payload(monkeypatch):
    """``extract_detailed`` calls ``_build_payload`` before the mocked
    ``_http_post``. The tests pass placeholder paths like ``"/tmp/x.pdf"``;
    stub ``_build_payload`` so no real file read occurs.
    """
    monkeypatch.setattr(
        "app.extraction.ocr.ppstructurev3.PPStructureV3Provider._build_payload",
        staticmethod(lambda file_path, file_type: {"file": "stub", "fileType": 0}),
    )


# Real PaddleX PP-StructureV3 serving contract (result.layoutParsingResults).
_FIXTURE = {
    "logId": "test",
    "result": {
        "layoutParsingResults": [
            {
                "prunedResult": {
                    "width": 1192,
                    "height": 1636,
                    "parsing_res_list": [
                        {"block_label": "doc_title", "block_content": "# 采购合同",
                         "block_bbox": [151, 353, 1011, 515], "block_id": 2, "block_order": 3},
                        {"block_label": "table",
                         "block_content": "<table><tr><td>甲方</td><td>南京瑞尚</td></tr></table>",
                         "block_bbox": [181, 968, 982, 1211], "block_id": 3, "block_order": None},
                        {"block_label": "text", "block_content": "合同金额120万元。",
                         "block_bbox": [120, 1300, 900, 1340], "block_id": 4, "block_order": 4},
                        {"block_label": "seal", "block_content": "<img src='imgs/x.jpg'/>",
                         "block_bbox": [400, 400, 500, 500], "block_id": 5, "block_order": None},
                    ],
                    "layout_det_res": {"boxes": []},
                },
                "markdown": {"text": "# 采购合同", "isStart": True, "isEnd": False, "images": {}},
            }
        ]
    },
    "errorCode": 0,
    "errorMsg": "Success",
}


def _provider_returning(payload):
    p = PPStructureV3Provider()
    p._http_post = MagicMock(return_value=payload)  # type: ignore[method-assign]
    return p


def test_normalizes_single_page():
    result = _provider_returning(_FIXTURE).extract_detailed("/tmp/x.pdf", "pdf")
    assert len(result.pages) == 1
    assert result.pages[0].page_no == 1


def test_block_types_and_table_text_preserved():
    result = _provider_returning(_FIXTURE).extract_detailed("/tmp/x.pdf", "pdf")
    page1 = result.pages[0]
    # seal is skipped via _SKIP_LABELS
    assert [b.block_type for b in page1.blocks] == ["title", "table", "text"]
    table_block = page1.blocks[1]
    assert table_block.text.startswith("<table>")
    assert table_block.bbox is not None
    assert table_block.confidence == 0.0  # blocks carry no confidence


def test_sort_order_assigned_in_reading_order():
    result = _provider_returning(_FIXTURE).extract_detailed("/tmp/x.pdf", "pdf")
    # seal skipped; remaining 3 blocks get sort_order 1, 2, 3
    assert [b.sort_order for b in result.pages[0].blocks] == [1, 2, 3]


def test_empty_regions_page_is_skipped_silently():
    payload = {"result": {"layoutParsingResults": [{"prunedResult": {"parsing_res_list": []}}]}}
    result = _provider_returning(payload).extract_detailed("/tmp/x.pdf", "pdf")
    assert result.pages == []


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
