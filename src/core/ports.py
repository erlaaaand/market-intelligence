# src/core/ports.py

"""
Abstract port definitions for agent_market_intelligence.

These ABCs define the *what* (interface contract) without any *how*
(implementation detail). Concrete adapters in ``src/infrastructure/``
implement these ports; the application layer depends only on these
abstractions — never on infrastructure classes directly.

Port summary
────────────
TrendProviderPort  fetch raw trending data from an external source
LLMPort            transform raw data into a deep-analytics report
StoragePort        persist raw payloads and processed reports
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.entities import MarketAnalysisReport, RawTrendData


# ---------------------------------------------------------------------------
# Trend data provider
# ---------------------------------------------------------------------------

class TrendProviderPort(ABC):
    """
    Output port: contract for any external trend data provider.

    Implementations must translate provider-specific API responses into a list
    of canonical ``RawTrendData`` domain entities.

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
            A list of ``RawTrendData`` entities ordered by the provider's
            default relevance ranking. May be empty if the provider has no
            data for the region.

        Raises:
            DataExtractionError:    On network errors or malformed responses.
            RateLimitExceededError: When the provider throttles the client.
        """
        ...


# ---------------------------------------------------------------------------
# LLM analysis port
# ---------------------------------------------------------------------------

class LLMPort(ABC):
    """
    Output port: contract for any LLM backend that produces deep analytics.

    Implementations send the raw trend data to a language model and parse the
    structured JSON response into a ``MarketAnalysisReport``.

    All implementations MUST raise only domain exceptions:
        LLMAnalysisError: If the model call fails or produces invalid output.
    """

    @abstractmethod
    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> MarketAnalysisReport:
        """
        Send raw trending data to the LLM and return a validated report.

        Args:
            raw_data:      List of raw trend records to be analysed.
            region:        ISO 3166-1 alpha-2 country code, uppercase.
            analysis_date: Date string in ``YYYY-MM-DD`` format (UTC).

        Returns:
            A fully validated ``MarketAnalysisReport`` populated by the LLM.

        Raises:
            LLMAnalysisError: If the model cannot produce a valid report after
                              all retry/recovery attempts are exhausted.
        """
        ...


# ---------------------------------------------------------------------------
# Storage port
# ---------------------------------------------------------------------------

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
                      (e.g. ``raw_US_120045Z.json``).

        Raises:
            StorageError: If the file cannot be written.
        """
        ...

    @abstractmethod
    def save_processed(
        self, report: MarketAnalysisReport, filename: str
    ) -> None:
        """
        Persist a ``MarketAnalysisReport`` to the processed data store.

        The storage backend is responsible for constructing the full path,
        which by convention follows the pattern:

            ``<processed_base>/{region}/{date}/{filename}``

        where ``region`` and ``date`` are read from ``report.metadata``.

        Args:
            report:   Fully validated ``MarketAnalysisReport`` to persist.
            filename: Target filename (e.g. ``market_data_120045Z.json``).

        Raises:
            StorageError: If the file cannot be written.
        """
        ...