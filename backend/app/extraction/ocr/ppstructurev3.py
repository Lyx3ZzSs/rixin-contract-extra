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
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

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
