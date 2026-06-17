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
    raise ValueError(f"Unknown OCR provider: {name}")
