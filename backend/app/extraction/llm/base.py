from abc import ABC, abstractmethod

from app.extraction.base import ExtractionResult


class LLMProvider(ABC):
    @abstractmethod
    def extract_fields(self, full_text: str, contract_type: str | None = None) -> ExtractionResult:
        ...

    @abstractmethod
    def classify_contract_type(self, full_text: str) -> tuple[str, float]:
        ...
