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
