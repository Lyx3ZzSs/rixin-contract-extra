from app.config import settings
from app.extraction.llm.base import LLMProvider

def get_llm_provider() -> LLMProvider:
    name = settings.llm_provider
    if name == "mock":
        from app.extraction.llm.mock import MockLLMProvider
        return MockLLMProvider()
    if name == "qwen":
        from app.extraction.llm.qwen import QwenLLMProvider
        return QwenLLMProvider()
    raise ValueError(f"Unknown LLM provider: {name}")
