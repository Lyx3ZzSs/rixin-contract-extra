from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "rixin-contract-extract"
    debug: bool = False

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://app:app_password@localhost:5432/contract_extract"


    # Provider selection
    ocr_provider: str = "mock"
    llm_provider: str = "mock"

    # PPStructure (OCR + layout for scans/images)
    ppstructure_url: str = "http://10.8.7.76:8081/"
    ocr_timeout: int = 120

    # LLM (Qwen3-30B-A3B, OpenAI-compatible)
    llm_api_url: str = "http://10.10.10.245:8000/v1/chat/completions"
    llm_api_key: str = ""
    llm_model_name: str = "qwen3-30b-a3b"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1
    llm_timeout: int = 120

    # DSPy — optional prompt optimization
    llm_use_dspy: bool = False
    llm_dspy_examples_path: str = "data/extraction_examples.jsonl"

    # Upload
    upload_dir: str = "uploads/contracts"
    max_file_size: int = 100 * 1024 * 1024  # 100 MB

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
