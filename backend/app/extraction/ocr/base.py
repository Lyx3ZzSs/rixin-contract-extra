"""Abstract OCR provider interface.

Providers implement ``extract_detailed`` to return block-level OCR results
(OCRDetailedResult) with bbox, confidence, and sort_order for each block.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.extraction.base import OCRDetailedResult


class OCRProvider(ABC):

    @abstractmethod
    def extract_detailed(self, file_path: str, file_type: str) -> OCRDetailedResult:
        """Return block-level OCR results for *file_path*."""

    def extract_from_images(self, page_images: list[bytes]) -> OCRDetailedResult:
        """OCR pre-rasterized page images (Tier 2). bbox returns in the same
        pixel space as the supplied images. Providers that don't support image
        input leave this raising NotImplementedError (callers fall back to
        ``extract_detailed``)."""
        raise NotImplementedError
