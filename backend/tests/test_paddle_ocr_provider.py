"""Tests for the Paddle OCR provider's PPStructure adapter."""

from __future__ import annotations

import pytest

from app.extraction.ocr.paddle import PaddleOCRProvider


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_ppstructure_layout_parsing_payload_and_response(monkeypatch):
    provider = PaddleOCRProvider()
    captured: dict = {}

    def fake_post(url: str, payload: dict, timeout: int):
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout"] = timeout
        return _FakeResponse({
            "logId": "log-1",
            "errorCode": 0,
            "errorMsg": "Success",
            "result": {
                "layoutParsingResults": [{
                    "prunedResult": {
                        "parsing_res_list": [{
                            "block_label": "text",
                            "block_content": "甲方名称：北京日新科技有限公司",
                            "block_bbox": [10, 20, 200, 40],
                            "block_order": 3,
                        }],
                    },
                    "markdown": {"text": "fallback text", "isStart": True, "isEnd": True},
                }],
                "dataInfo": {"width": 800, "height": 600, "type": "image"},
            },
        })

    monkeypatch.setattr(PaddleOCRProvider, "_http_post", staticmethod(fake_post))

    blocks = provider._call_ppstructure(b"fake-image-bytes", page_no=1, sort_offset=10)

    assert captured["payload"]["fileType"] == 1
    assert "file" in captured["payload"]
    assert "image" not in captured["payload"]
    assert len(blocks) == 1
    assert blocks[0].text == "甲方名称：北京日新科技有限公司"
    assert blocks[0].block_type == "text"
    assert blocks[0].bbox is not None
    assert blocks[0].bbox.to_list() == [10, 20, 200, 40]
    assert blocks[0].sort_order == 13


def test_ppstructure_markdown_fallback(monkeypatch):
    provider = PaddleOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({
            "errorCode": 0,
            "result": {
                "layoutParsingResults": [{
                    "prunedResult": {"parsing_res_list": []},
                    "markdown": {"text": "合同正文\n付款条款", "isStart": True, "isEnd": True},
                }],
            },
        })

    monkeypatch.setattr(PaddleOCRProvider, "_http_post", staticmethod(fake_post))

    blocks = provider._call_ppstructure(b"fake-image-bytes", page_no=1)

    assert len(blocks) == 1
    assert blocks[0].text == "合同正文\n付款条款"


def test_ppstructure_error_response_raises(monkeypatch):
    provider = PaddleOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({
            "logId": "log-2",
            "errorCode": 422,
            "errorMsg": "Invalid input file",
        })

    monkeypatch.setattr(PaddleOCRProvider, "_http_post", staticmethod(fake_post))

    with pytest.raises(RuntimeError, match="Invalid input file"):
        provider._call_ppstructure(b"fake-image-bytes", page_no=1)


def test_ppstructure_no_text_raises(monkeypatch):
    provider = PaddleOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({
            "errorCode": 0,
            "result": {
                "layoutParsingResults": [{
                    "prunedResult": {"parsing_res_list": []},
                    "markdown": {"text": "", "isStart": True, "isEnd": True},
                }],
            },
        })

    monkeypatch.setattr(PaddleOCRProvider, "_http_post", staticmethod(fake_post))

    with pytest.raises(RuntimeError, match="no text blocks"):
        provider._call_ppstructure(b"fake-image-bytes", page_no=1)
