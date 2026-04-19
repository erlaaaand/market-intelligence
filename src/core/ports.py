# src/core/ports.py

"""
Abstract port definitions for the agent_market_intelligence module.

These ABCs define the *what* (interface contract) without any *how*
(implementation detail). Concrete adapters in `src/infrastructure/`
implement these ports, and the application layer depends only on these
abstractions — never on infrastructure classes directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.entities import RawTrendData, TrendTopic


class TrendProviderPort(ABC):
    """
    Output port: contract for any external trend data provider.

    Implementations must translate provider-specific API responses into
    a list of canonical `RawTrendData` domain entities.

    All implementations MUST raise only domain exceptions:
        DataExtractionError:    If the provider returns unusable data.
        RateLimitExceededError: If the provider signals HTTP 429 after retries.
    """

    @abstractmethod
    def fetch_trends(self, region: str) -> list[RawTrendData]:
        """
        Fetch trending search topics for the given region.

        Args:
            region: ISO 3166-1 alpha-2 country code, uppercase (e.g. "US", "ID").

        Returns:
            A non-empty list of `RawTrendData` entities, ordered by the
            provider's default relevance ranking.

        Raises:
            DataExtractionError:    On network errors, malformed responses, or
                                    unexpected data formats.
            RateLimitExceededError: When the provider rate-limits the client
                                    after all retry attempts are exhausted.
        """
        ...


class StoragePort(ABC):
    """
    Output port: contract for any persistence backend.

    Implementations handle serialisation, directory management, and
    error handling transparently from the application layer's perspective.

    All implementations MUST raise only domain exceptions:
        StorageError: If the underlying I/O operation fails.
    """

    @abstractmethod
    def save_raw(self, data: dict[str, object], filename: str) -> None:
        """
        Persist a raw data dictionary to the raw data store.

        Args:
            data:     JSON-serialisable dictionary to persist.
            filename: Target filename without directory prefix
                      (e.g. "raw_trends_US_20240601T120000Z.json").

        Raises:
            StorageError: If the file cannot be written.
        """
        ...

    @abstractmethod
    def save_processed(self, data: list[TrendTopic], filename: str) -> None:
        """
        Persist a list of processed `TrendTopic` entities to the processed store.

        Args:
            data:     List of validated, enriched trend topic entities.
            filename: Target filename without directory prefix.

        Raises:
            StorageError: If the file cannot be written.
        """
        ...