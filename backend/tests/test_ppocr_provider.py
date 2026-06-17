"""Tests for the PP-OCR provider adapter."""

from __future__ import annotations

import pytest

from app.extraction.base import OCRPageResult, OCRTextBlock
from app.extraction.ocr.ppocr import PPOCRProvider


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_ppocr_payload_and_dict_response(monkeypatch):
    provider = PPOCRProvider()
    captured: dict = {}

    def fake_post(url: str, payload: dict, timeout: int):
        captured["url"] = url
        captured["payload"] = payload
        captured["timeout"] = timeout
        return _FakeResponse({
            "errorCode": 0,
            "result": [
                {
                    "text": "甲方名称：北京日新科技有限公司",
                    "bbox": [10, 20, 200, 40],
                    "confidence": 0.93,
                },
            ],
        })

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    blocks = provider._call_ppocr(b"fake-image-bytes", page_no=1, sort_offset=5)

    assert captured["payload"]["fileType"] == 1
    assert "file" in captured["payload"]
    assert len(blocks) == 1
    assert blocks[0].text == "甲方名称：北京日新科技有限公司"
    assert blocks[0].bbox is not None
    assert blocks[0].bbox.to_list() == [10, 20, 200, 40]
    assert blocks[0].confidence == 0.93
    assert blocks[0].sort_order == 6


def test_ppocr_paddleocr_list_response(monkeypatch):
    provider = PPOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse([
            [
                [[10, 20], [200, 20], [200, 40], [10, 40]],
                ("乙方名称：上海测试公司", 0.88),
            ],
        ])

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    blocks = provider._call_ppocr(b"fake-image-bytes", page_no=1)

    assert len(blocks) == 1
    assert blocks[0].text == "乙方名称：上海测试公司"
    assert blocks[0].bbox is not None
    assert blocks[0].bbox.to_list() == [10, 20, 200, 40]
    assert blocks[0].confidence == 0.88


def test_ppocr_rec_texts_response(monkeypatch):
    provider = PPOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({
            "errorCode": 0,
            "result": {
                "rec_texts": ["合同编号：HT-001"],
                "rec_scores": [0.91],
                "dt_polys": [[[1, 2], [30, 2], [30, 12], [1, 12]]],
            },
        })

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    blocks = provider._call_ppocr(b"fake-image-bytes", page_no=1)

    assert blocks[0].text == "合同编号：HT-001"
    assert blocks[0].confidence == 0.91
    assert blocks[0].bbox is not None
    assert blocks[0].bbox.to_list() == [1, 2, 30, 12]


def test_ppocr_aistudio_ocr_results_response(monkeypatch):
    provider = PPOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({
            "errorCode": 0,
            "result": {
                "ocrResults": [
                    {
                        "prunedResult": {
                            "rec_texts": ["甲方名称：北京日新科技有限公司"],
                            "rec_scores": [0.96],
                            "rec_polys": [[[10, 20], [200, 20], [200, 40], [10, 40]]],
                        },
                    },
                ],
                "dataInfo": {"type": "image", "width": 800, "height": 600},
            },
        })

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    blocks = provider._call_ppocr(b"fake-image-bytes", page_no=1)

    assert len(blocks) == 1
    assert blocks[0].text == "甲方名称：北京日新科技有限公司"
    assert blocks[0].confidence == 0.96
    assert blocks[0].bbox is not None
    assert blocks[0].bbox.to_list() == [10, 20, 200, 40]


def test_ppocr_whole_pdf_response_maps_pages(monkeypatch):
    provider = PPOCRProvider()
    fallback_pages = [
        OCRPageResult(page_no=1, width=100, height=200, blocks=[]),
        OCRPageResult(page_no=2, width=110, height=210, blocks=[]),
    ]

    def fake_post(_url: str, payload: dict, _timeout: int):
        assert payload["fileType"] == 0
        assert payload["visualize"] is False
        assert payload["returnWordBox"] is False
        return _FakeResponse({
            "errorCode": 0,
            "result": {
                "ocrResults": [
                    {"prunedResult": {"rec_texts": ["第一页"], "rec_scores": [0.9], "rec_polys": []}},
                    {"prunedResult": {"rec_texts": ["第二页"], "rec_scores": [0.8], "rec_polys": []}},
                ],
                "dataInfo": {
                    "type": "pdf",
                    "numPages": 2,
                    "pages": [
                        {"width": 1000, "height": 2000},
                        {"width": 1100, "height": 2100},
                    ],
                },
            },
        })

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    pages = provider._call_ppocr_pdf(b"%PDF fake", fallback_pages)

    assert [page.page_no for page in pages] == [1, 2]
    assert [page.full_text for page in pages] == ["第一页", "第二页"]
    assert pages[0].width == 1000
    assert pages[1].height == 2100


def test_ppocr_error_response_raises(monkeypatch):
    provider = PPOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({
            "logId": "log-1",
            "errorCode": 422,
            "errorMsg": "Invalid input file",
        })

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    with pytest.raises(RuntimeError, match="Invalid input file"):
        provider._call_ppocr(b"fake-image-bytes", page_no=1)


def test_ppocr_no_text_raises(monkeypatch):
    provider = PPOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({"errorCode": 0, "result": []})

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    with pytest.raises(RuntimeError, match="no text blocks"):
        provider._call_ppocr(b"fake-image-bytes", page_no=1)


def test_ppocr_no_text_allow_empty(monkeypatch):
    provider = PPOCRProvider()

    def fake_post(_url: str, _payload: dict, _timeout: int):
        return _FakeResponse({"errorCode": 0, "result": []})

    monkeypatch.setattr(PPOCRProvider, "_http_post", staticmethod(fake_post))

    assert provider._call_ppocr(b"fake-image-bytes", page_no=1, allow_empty=True) == []


def test_pdf_text_extraction_skips_ppocr(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Service contract text " * 20)
    doc.save(pdf_path)
    doc.close()

    provider = PPOCRProvider()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("PP-OCR should not be called for text PDF")

    monkeypatch.setattr(provider, "_call_ppocr_pdf", fail_if_called)

    result = provider._extract_pdf(str(pdf_path))

    assert result.provider == "ppocr_pdf_text"
    assert "Service contract text" in result.full_text


def test_pdf_uses_whole_pdf_ocr_for_scanned_pdf(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    provider = PPOCRProvider()
    captured: dict = {}

    def fake_whole_pdf(pdf_bytes, fallback_pages):
        captured["pdf_bytes"] = pdf_bytes
        captured["fallback_pages"] = fallback_pages
        return [
            OCRPageResult(
                page_no=1,
                width=100,
                height=100,
                blocks=[OCRTextBlock(text="整份OCR文本", confidence=0.9, sort_order=1)],
            ),
        ]

    monkeypatch.setattr(provider, "_call_ppocr_pdf", fake_whole_pdf)

    result = provider._extract_pdf(str(pdf_path))

    assert result.provider == "ppocr_pdf_whole"
    assert captured["pdf_bytes"].startswith(b"%PDF")
    assert captured["fallback_pages"][0].page_no == 1
    assert result.full_text == "整份OCR文本"


def test_pdf_whole_ocr_failure_falls_back_to_page_ocr(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "fallback.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    provider = PPOCRProvider()

    def fail_whole_pdf(*_args, **_kwargs):
        raise RuntimeError("whole pdf unsupported")

    def fake_page_ocr(_img_bytes, page_no, sort_offset=0, allow_empty=False):
        if page_no == 1:
            return []
        return [OCRTextBlock(text="第二页分页OCR", confidence=0.8, sort_order=sort_offset + 1)]

    monkeypatch.setattr(provider, "_call_ppocr_pdf", fail_whole_pdf)
    monkeypatch.setattr(provider, "_call_ppocr", fake_page_ocr)

    result = provider._extract_pdf(str(pdf_path))

    assert result.provider == "ppocr_pdf_page_fallback"
    assert result.pages[0].full_text == ""
    assert result.pages[1].full_text == "第二页分页OCR"
