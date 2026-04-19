from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.entities import CreativeDocumentBatch, RawTrendData


class TrendProviderPort(ABC):
    @abstractmethod
    def fetch_trends(self, region: str) -> list[RawTrendData]:
        ...


class LLMPort(ABC):
    @abstractmethod
    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> CreativeDocumentBatch:
        ...


class StoragePort(ABC):
    @abstractmethod
    def save_raw(self, data: dict[str, object], filename: str) -> None:
        ...

    @abstractmethod
    def save_processed(self, batch: CreativeDocumentBatch, filename: str) -> None:
        ...