from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "rixin-contract-extract"
    debug: bool = False

    # Database (SQLite-first for local/single-machine deployment)
    database_url: str = "sqlite+aiosqlite:///./data/contract_extract.db"


    # Provider selection
    ocr_provider: str = "mock"
    llm_provider: str = "mock"

    # PP-StructureV3 (layout-aware: tables, regions, reading order)
    ppstructurev3_url: str = "http://10.8.7.76:8082/structure"

    # PP-OCR (fast OCR for scans/images)
    ppocr_url: str = "http://10.8.7.76:8081/ocr"
    ppocr_timeout: int = 60
    ppocr_pdf_text_min_chars: int = 100
    ppocr_pdf_text_page_ratio: float = 0.8
    ppocr_pdf_dpi: int = 200
    ppocr_page_concurrency: int = 3

    # Tier 2 traceability: rasterize PDFs locally so bbox lives in the same
    # pixel space as the page image we serve for highlight overlay. When False,
    # fall back to the legacy PDF-bytes path (provider.extract_detailed).
    ocr_rasterize_locally: bool = True

    # LLM (Qwen3-30B-A3B, OpenAI-compatible)
    llm_api_url: str = "http://10.10.10.245:8000/v1/chat/completions"
    llm_api_key: str = ""
    llm_model_name: str = "qwen3-30b-a3b"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1
    llm_timeout: int = 120

    # Long-contract chunking: pages per LLM call (1-page overlap between chunks).
    llm_chunk_pages: int = 6

    # Upload
    upload_dir: str = "uploads/contracts"
    max_file_size: int = 100 * 1024 * 1024  # 100 MB

    # Minimum auth: comma-separated valid API keys. Empty = open mode (dev);
    # set in production to require an X-API-Key header on all /api/v1 routes.
    app_api_keys: str = ""
    # CORS: comma-separated allowed origins. Empty = allow any (credentials off).
    allowed_origins: str = ""

    # Phase 2 kill-switch: gate rule validation in the extraction pipeline.
    enable_rule_validation: bool = True

    model_config = {
        "env_file": (
            # Resolve .env relative to the backend dir (parent of app/),
            # so it works regardless of CWD (e.g. PyCharm run from project root).
            str(Path(__file__).resolve().parent.parent / ".env"),
            ".env",
        ),
        "env_file_encoding": "utf-8",
    }


settings = Settings()
