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
from concurrent.futures import ThreadPoolExecutor
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
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    # Block-label mapping for parsing_res_list items from layoutParsingResults.
    # Adapted to the real PaddleX PP-StructureV3 serving output (result.layoutParsingResults[].prunedResult.parsing_res_list).
    _LABEL_MAP = {
        "title": "title", "doc_title": "title", "paragraph_title": "title",
        "text": "text", "number": "text", "aside_text": "text",
        "vision_footnote": "text", "figure_title": "text",
        "table": "table",
    }
    # Block labels whose content is placeholder HTML (images/seals) — no text value, skip.
    _SKIP_LABELS = {"figure", "seal", "header_image", "footer_image"}

    # ------------------------------------------------------------------
    # OCRProvider interface
    # ------------------------------------------------------------------

    def extract_detailed(self, file_path: str, file_type: str) -> OCRDetailedResult:
        payload = self._build_payload(file_path, file_type)
        data = self._http_post(self._url, payload)
        return self._normalize(data)

    def extract_from_images(self, page_images: list[bytes]) -> OCRDetailedResult:
        """OCR each pre-rasterized page image (fileType=1) and aggregate.

        Because we sent OUR images, the returned bbox lives in each image's
        pixel space — identical to the page image we persist for display.
        """
        if not page_images:
            return OCRDetailedResult(pages=[], provider="ppstructurev3")

        def extract_one(idx_and_bytes: tuple[int, bytes]) -> OCRPageResult:
            idx, img_bytes = idx_and_bytes
            encoded = base64.b64encode(img_bytes).decode("ascii")
            payload = {"file": encoded, "fileType": 1, "useLayout": True, "useTable": True}
            data = self._http_post(self._url, payload)
            pages_raw = self._extract_payload(data) or []
            page_raw = pages_raw[0] if pages_raw else {}
            blocks = self._blocks_for(page_raw)
            pruned = page_raw.get("prunedResult") or {}
            return OCRPageResult(
                page_no=idx,
                blocks=blocks,
                width=pruned.get("width"),
                height=pruned.get("height"),
            )

        indexed_images = list(enumerate(page_images, start=1))
        concurrency = max(1, min(settings.ppocr_page_concurrency, len(indexed_images)))
        if concurrency == 1:
            pages = [extract_one(item) for item in indexed_images]
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                pages = list(executor.map(extract_one, indexed_images))
        return OCRDetailedResult(pages=pages, provider="ppstructurev3")

    # ------------------------------------------------------------------
    # HTTP (mockable seam — tests override _http_post)
    # ------------------------------------------------------------------

    def _http_post(self, url: str, payload: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(_HTTP_RETRIES + 1):
            try:
                resp = self._get_client().post(url, json=payload)
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
        pages_raw = self._extract_payload(data)
        if not pages_raw:
            raise RuntimeError("PP-StructureV3 returned no parseable results")

        pages: list[OCRPageResult] = []
        for idx, page_raw in enumerate(pages_raw):
            blocks = self._blocks_for(page_raw)
            if not blocks:
                continue
            pruned = page_raw.get("prunedResult") or {}
            pages.append(OCRPageResult(
                page_no=idx + 1,
                blocks=blocks,
                width=pruned.get("width"),
                height=pruned.get("height"),
            ))
        return OCRDetailedResult(pages=pages, provider="ppstructurev3")

    @staticmethod
    def _extract_payload(data: dict) -> list[dict] | None:
        """Return the ``result.layoutParsingResults`` page array.

        Matches the real PaddleX PP-StructureV3 serving output; falls back to
        top-level ``layoutParsingResults`` for deployments that omit ``result``.
        """
        if not isinstance(data, dict):
            return None
        container = data.get("result") if isinstance(data.get("result"), dict) else data
        pages = container.get("layoutParsingResults")
        return pages if isinstance(pages, list) else None

    @staticmethod
    def _blocks_for(page_raw: dict) -> list[OCRTextBlock]:
        """Map ``prunedResult.parsing_res_list`` blocks to OCRTextBlocks. Skips
        image/seal blocks (placeholder HTML). Table blocks keep their HTML
        ``block_content`` verbatim so ``to_markdown`` preserves cell structure.
        """
        pruned = page_raw.get("prunedResult") or {}
        blocks_raw = pruned.get("parsing_res_list") or []
        blocks: list[OCRTextBlock] = []
        for sort_order, raw in enumerate(blocks_raw, start=1):
            label = raw.get("block_label", "text")
            if label in PPStructureV3Provider._SKIP_LABELS:
                continue
            text = (raw.get("block_content") or "").strip()
            if not text:
                continue
            blocks.append(OCRTextBlock(
                block_type=PPStructureV3Provider._LABEL_MAP.get(label, "text"),
                text=text,
                bbox=PPStructureV3Provider._bbox(raw.get("block_bbox")),
                confidence=0.0,  # blocks carry no confidence; layout_det_res.boxes[].score does
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
