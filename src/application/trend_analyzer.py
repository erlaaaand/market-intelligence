# src/application/trend_analyzer.py

"""
Application use-case: TrendAnalyzerUseCase.

This module contains the sole orchestration logic for the market
intelligence pipeline. It depends exclusively on abstract ports defined
in `src.core.ports` — never on concrete infrastructure classes.
This keeps business rules fully decoupled from I/O and external services.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.core.entities import RawTrendData, TrendTopic
from src.core.exceptions import DataExtractionError
from src.core.ports import StoragePort, TrendProviderPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants — adjust via dependency injection or subclassing if needed
# ---------------------------------------------------------------------------
_TOP_N_TOPICS: int = 5
_GROWTH_THRESHOLD: int = 60        # raw_value >= this → topic is "growing"
_HIGH_VOLUME_THRESHOLD: int = 80   # raw_value >= this → "breaking" angle
_RAW_FILENAME_TEMPLATE: str = "raw_trends_{region}_{ts}.json"
_PROCESSED_FILENAME_TEMPLATE: str = "processed_trends_{region}_{ts}.json"


class TrendAnalyzerUseCase:
    """
    Orchestrates the end-to-end market-intelligence data pipeline.

    Pipeline steps (see `execute`):
        1. Fetch raw trends from the injected `TrendProviderPort`.
        2. Persist raw payload to the raw data store.
        3. Filter, rank, and select top-N topics by search volume.
        4. Enrich each topic with a suggested content angle.
        5. Persist processed `TrendTopic` entities.
        6. Return the processed list to the caller.

    All dependencies are injected at construction time, making this class
    fully unit-testable without any network calls or file-system side-effects.
    """

    def __init__(
        self,
        trend_provider: TrendProviderPort,
        storage: StoragePort,
        top_n: int = _TOP_N_TOPICS,
        growth_threshold: int = _GROWTH_THRESHOLD,
    ) -> None:
        """
        Initialise the use-case with its required ports.

        Args:
            trend_provider:   Concrete adapter for fetching trend data.
            storage:          Concrete adapter for persisting results.
            top_n:            Number of top topics to select (default: 5).
            growth_threshold: Minimum raw_value to flag a topic as "growing".
        """
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        if not (0 <= growth_threshold <= 100):
            raise ValueError(
                f"growth_threshold must be in 0–100, got {growth_threshold}"
            )
        self._trend_provider = trend_provider
        self._storage = storage
        self._top_n = top_n
        self._growth_threshold = growth_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, region: str) -> list[TrendTopic]:
        """
        Run the full market-intelligence pipeline for the given region.

        Args:
            region: ISO 3166-1 alpha-2 country code (e.g. "US", "ID").

        Returns:
            A list of up to `top_n` processed and enriched `TrendTopic` entities,
            sorted by search_volume descending.

        Raises:
            DataExtractionError:    Propagated from the trend provider.
            RateLimitExceededError: Propagated from the trend provider.
            StorageError:           Propagated from the storage adapter.
        """
        region = region.upper().strip()
        timestamp: str = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        logger.info(
            "Starting trend analysis pipeline  region='%s'  top_n=%d",
            region,
            self._top_n,
        )

        # ── Step 1: Fetch raw data ────────────────────────────────────
        raw_data: list[RawTrendData] = self._fetch_raw(region)

        # ── Step 2: Persist raw data ──────────────────────────────────
        raw_filename = _RAW_FILENAME_TEMPLATE.format(region=region, ts=timestamp)
        self._save_raw(raw_data, raw_filename)

        # ── Step 3–5: Filter, rank, enrich ───────────────────────────
        processed: list[TrendTopic] = self._process(raw_data, region)

        # ── Step 6: Persist processed data ───────────────────────────
        processed_filename = _PROCESSED_FILENAME_TEMPLATE.format(
            region=region, ts=timestamp
        )
        self._storage.save_processed(processed, processed_filename)
        logger.info(
            "Saved %d processed topic(s) → '%s'", len(processed), processed_filename
        )

        logger.info("Pipeline complete. %d topic(s) ready.", len(processed))
        return processed

    # ------------------------------------------------------------------
    # Private pipeline steps
    # ------------------------------------------------------------------

    def _fetch_raw(self, region: str) -> list[RawTrendData]:
        """Delegate fetching to the injected provider with structured logging."""
        try:
            raw_data = self._trend_provider.fetch_trends(region)
            logger.info(
                "Fetched %d raw record(s) from provider for region='%s'.",
                len(raw_data),
                region,
            )
            return raw_data
        except DataExtractionError:
            logger.exception("Data extraction failed for region='%s'.", region)
            raise

    def _save_raw(self, raw_data: list[RawTrendData], filename: str) -> None:
        """Serialise raw entities to a JSON-compatible dict and delegate saving."""
        payload: dict[str, object] = {
            "count": len(raw_data),
            "records": [item.model_dump(mode="json") for item in raw_data],
        }
        self._storage.save_raw(payload, filename)
        logger.info("Saved %d raw record(s) → '%s'.", len(raw_data), filename)

    def _process(self, raw_data: list[RawTrendData], region: str) -> list[TrendTopic]:
        """
        Apply domain filtering, ranking, and enrichment rules.

        Logic:
            - Sort descending by `raw_value` (highest search volume first).
            - Select top-N records.
            - Flag as growing if `raw_value >= growth_threshold`.
            - Attach a lightweight editorial angle per topic.

        Args:
            raw_data: Full list of raw trend records from the provider.
            region:   Target country code (propagated into `TrendTopic`).

        Returns:
            List of enriched `TrendTopic` entities (may be empty if no raw data).
        """
        if not raw_data:
            logger.warning(
                "No raw data to process for region='%s'. Returning empty list.", region
            )
            return []

        ranked: list[RawTrendData] = sorted(
            raw_data, key=lambda r: r.raw_value, reverse=True
        )
        top_n: list[RawTrendData] = ranked[: self._top_n]

        topics: list[TrendTopic] = [
            TrendTopic(
                topic_name=record.keyword,
                search_volume=record.raw_value,
                target_country=region,
                suggested_angle=self._generate_angle(record.keyword, record.raw_value),
                is_growing=record.raw_value >= self._growth_threshold,
            )
            for record in top_n
        ]

        logger.debug(
            "Processed %d topic(s) from %d raw record(s).", len(topics), len(raw_data)
        )
        return topics

    def _generate_angle(self, keyword: str, volume: int) -> str:
        """
        Generate a lightweight editorial content angle.

        Heuristic tiers (based on volume score):
            >= 80 → "Breaking" (extremely high search interest)
            >= 60 → "Emerging" (growing but not yet dominant)
            <  60 → "Deep-dive" (niche / early-stage interest)

        In a production system this method could be replaced with an LLM
        call, a rules engine, or a classification model without changing
        any other part of the pipeline.

        Args:
            keyword: The raw trend keyword.
            volume:  Normalised search interest score (0–100).

        Returns:
            A short, human-readable editorial angle string.
        """
        if volume >= _HIGH_VOLUME_THRESHOLD:
            return f"Breaking: Why '{keyword}' is dominating search right now"
        if volume >= self._growth_threshold:
            return f"Emerging trend: What you need to know about '{keyword}'"
        return f"Deep-dive: Exploring the growing interest in '{keyword}'"