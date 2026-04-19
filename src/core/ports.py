# src/core/ports.py

"""
Abstract port definitions for the agent_market_intelligence module.

Following the Ports & Adapters (Hexagonal) pattern, these ABCs define
the *what* (interface contract) without any *how* (implementation detail).
Concrete adapters in `src/infrastructure/` implement these ports.
"""

from abc import ABC, abstractmethod

from src.core.entities import RawTrendData, TrendTopic


class TrendProviderPort(ABC):
    """
    Output port: contract for any external trend data provider.

    Implementations must translate provider-specific responses into
    a list of canonical `RawTrendData` entities.

    Raises:
        DataExtractionError:    If the provider returns unusable data.
        RateLimitExceededError: If the provider signals HTTP 429.
    """

    @abstractmethod
    def fetch_trends(self, region: str) -> list[RawTrendData]:
        """
        Fetch trending search topics for the specified region.

        Args:
            region: ISO 3166-1 alpha-2 country code (e.g. "US", "ID").

        Returns:
            A list of `RawTrendData` entities ordered by the provider's
            default ranking.
        """
        ...


class StoragePort(ABC):
    """
    Output port: contract for any persistence backend.

    Implementations handle serialisation and directory management
    transparently from the application layer's perspective.

    Raises:
        StorageError: If the underlying I/O operation fails.
    """

    @abstractmethod
    def save_raw(self, data: dict[str, object], filename: str) -> None:
        """
        Persist raw (pre-processing) data to the raw data store.

        Args:
            data:     The raw payload to serialise (must be JSON-serialisable).
            filename: Target filename (without directory prefix).
        """
        ...

    @abstractmethod
    def save_processed(self, data: list[TrendTopic], filename: str) -> None:
        """
        Persist a list of processed `TrendTopic` entities to the processed store.

        Args:
            data:     The processed entities to serialise.
            filename: Target filename (without directory prefix).
        """
        ...