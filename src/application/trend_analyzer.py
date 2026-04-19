# src/application/trend_analyzer.py

"""
Application use-case: TrendAnalyzerUseCase.

This module contains the sole orchestration logic for the market
intelligence pipeline. It depends only on abstract ports (never on
concrete infrastructure classes), keeping the core business rules
fully decoupled from frameworks and external services.
"""

import logging
from datetime import datetime, timezone

from src.core.entities import RawTrendData, TrendTopic
from src.core.exceptions import DataExtractionError
from src.core.ports import StoragePort, TrendProviderPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TOP_N_TOPICS: int = 5
_GROWTH_THRESHOLD: int = 60  # topics with raw_value >= this are considered growing
_RAW_FILENAME_TEMPLATE: str = "raw_trends_{region}_{ts}.json"
_PROCESSED_FILENAME_TEMPLATE: str = "processed_trends_{region}_{ts}.json"


class TrendAnalyzerUseCase:
    """
    Orchestrates the end-to-end market-intelligence pipeline.

    Responsibilities:
      1. Delegate data retrieval to a `TrendProviderPort` adapter.
      2. Apply domain-level filtering and enrichment rules.
      3. Convert raw data into `TrendTopic` domain entities.
      4. Persist both raw and processed artefacts via `StoragePort`.

    All dependencies are injected, making this class fully unit-testable
    without any network calls or file-system side-effects.
    """

    def __init__(
        self,
        trend_provider: TrendProviderPort,
        storage: StoragePort,
    ) -> None:
        """
        Initialise the use-case with its required ports.

        Args:
            trend_provider: Concrete adapter for fetching trend data.
            storage:        Concrete adapter for persisting results.
        """
        self._trend_provider = trend_provider
        self._storage = storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, region: str) -> list[TrendTopic]:
        """
        Run the full market-intelligence pipeline for the given region.

        Pipeline steps:
          1. Fetch raw trends from the provider.
          2. Save raw payload to `data/raw/`.
          3. Filter and rank topics by search volume.
          4. Select the top-N trending topics.
          5. Enrich each topic with a suggested content angle.
          6. Save processed entities to `data/processed/`.
          7. Return the final list of `TrendTopic` entities.

        Args:
            region: ISO 3166-1 alpha-2 country code (e.g. "US", "ID").

        Returns:
            A list of up to `_TOP_N_TOPICS` processed `TrendTopic` entities.

        Raises:
            DataExtractionError: Propagated from the trend provider.
            StorageError:        Propagated from the storage adapter.
        """
        region = region.upper().strip()
        timestamp: str = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        logger.info("Starting trend analysis pipeline for region='%s'.", region)

        # ── Step 1: Fetch raw data ────────────────────────────────────
        raw_data: list[RawTrendData] = self._fetch_raw(region)

        # ── Step 2: Persist raw data ──────────────────────────────────
        raw_filename = _RAW_FILENAME_TEMPLATE.format(region=region, ts=timestamp)
        self._save_raw(raw_data, raw_filename)

        # ── Step 3–5: Filter, rank, and enrich ───────────────────────
        processed: list[TrendTopic] = self._process(raw_data, region)

        # ── Step 6: Persist processed data ───────────────────────────
        processed_filename = _PROCESSED_FILENAME_TEMPLATE.format(
            region=region, ts=timestamp
        )
        self._storage.save_processed(processed, processed_filename)
        logger.info(
            "Saved %d processed topics to '%s'.", len(processed), processed_filename
        )

        logger.info("Pipeline completed. %d topic(s) returned.", len(processed))
        return processed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_raw(self, region: str) -> list[RawTrendData]:
        """Delegate fetching to the injected provider, with top-level logging."""
        try:
            raw_data = self._trend_provider.fetch_trends(region)
            logger.info("Fetched %d raw trend record(s) for '%s'.", len(raw_data), region)
            return raw_data
        except DataExtractionError:
            logger.exception("Data extraction failed for region='%s'.", region)
            raise

    def _save_raw(self, raw_data: list[RawTrendData], filename: str) -> None:
        """Serialise raw entities to a JSON-compatible dict and persist."""
        payload: dict[str, object] = {
            "count": len(raw_data),
            "records": [item.model_dump(mode="json") for item in raw_data],
        }
        self._storage.save_raw(payload, filename)
        logger.info("Saved %d raw record(s) to '%s'.", len(raw_data), filename)

    def _process(self, raw_data: list[RawTrendData], region: str) -> list[TrendTopic]:
        """
        Apply domain filtering and enrichment rules.

        Filtering logic:
          - Sort all records by `raw_value` descending (highest volume first).
          - Select the top `_TOP_N_TOPICS` records.
          - Mark topics with `raw_value >= _GROWTH_THRESHOLD` as growing.
          - Generate a lightweight suggested content angle for each topic.

        Args:
            raw_data: The full list of raw trend records.
            region:   The target country code (used in TrendTopic fields).

        Returns:
            A list of enriched `TrendTopic` entities.
        """
        if not raw_data:
            logger.warning("No raw data available to process for region='%s'.", region)
            return []

        ranked: list[RawTrendData] = sorted(
            raw_data, key=lambda r: r.raw_value, reverse=True
        )
        top_n: list[RawTrendData] = ranked[:_TOP_N_TOPICS]

        topics: list[TrendTopic] = [
            TrendTopic(
                topic_name=record.keyword,
                search_volume=record.raw_value,
                target_country=region,
                suggested_angle=self._generate_angle(record.keyword, record.raw_value),
                is_growing=record.raw_value >= _GROWTH_THRESHOLD,
            )
            for record in top_n
        ]

        logger.debug(
            "Processed %d topic(s) from %d raw record(s).", len(topics), len(raw_data)
        )
        return topics

    @staticmethod
    def _generate_angle(keyword: str, volume: int) -> str:
        """
        Generate a minimal suggested content angle based on keyword and volume.

        This is intentionally kept as a simple heuristic. In production,
        this could be replaced by an LLM call or a rules engine.

        Args:
            keyword: The raw trend keyword.
            volume:  The normalised search interest score.

        Returns:
            A short editorial angle string.
        """
        if volume >= 80:
            return f"Breaking: Why '{keyword}' is dominating search right now"
        if volume >= _GROWTH_THRESHOLD:
            return f"Emerging trend: What you need to know about '{keyword}'"
        return f"Deep-dive: Exploring the growing interest in '{keyword}'"