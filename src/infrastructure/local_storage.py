from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.core.entities import RawTrendData, TrendTopic
from src.core.exceptions import DataExtractionError
from src.core.ports import StoragePort, TrendProviderPort

logger = logging.getLogger(__name__)

_TOP_N_TOPICS: int = 5
_GROWTH_THRESHOLD: int = 60
_HIGH_VOLUME_THRESHOLD: int = 80
_RAW_FILENAME_TEMPLATE: str = "raw_{region}_{ts}.json"
_PROCESSED_FILENAME_TEMPLATE: str = "processed_{region}_{ts}.json"


class TrendAnalyzerUseCase:
    """
    Orchestrates the full trend-analysis pipeline:
        fetch raw  →  save raw  →  process  →  save processed  →  return topics

    Date-partitioned filenames: the *date* lives in the storage adapter's
    folder structure (``YYYY-MM-DD/``), so filenames here only carry the
    *time* component for uniqueness within a day.
    """

    def __init__(
        self,
        trend_provider: TrendProviderPort,
        storage: StoragePort,
        top_n: int = _TOP_N_TOPICS,
        growth_threshold: int = _GROWTH_THRESHOLD,
    ) -> None:
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
        region = region.upper().strip()
        # Timestamp used only for the *filename* (folder = date from adapter)
        timestamp: str = datetime.now(tz=timezone.utc).strftime("%H%M%SZ")

        logger.info(
            "Pipeline start  region='%s'  top_n=%d", region, self._top_n
        )

        raw_data = self._fetch_raw(region)

        raw_filename = _RAW_FILENAME_TEMPLATE.format(region=region, ts=timestamp)
        self._save_raw(raw_data, raw_filename)

        processed = self._process(raw_data, region)

        if processed:
            processed_filename = _PROCESSED_FILENAME_TEMPLATE.format(
                region=region, ts=timestamp
            )
            self._storage.save_processed(processed, processed_filename)
            logger.info(
                "Saved %d processed topic(s)  → '%s'",
                len(processed),
                processed_filename,
            )
        else:
            logger.warning(
                "No processed topics for region='%s'. Skipping processed write.",
                region,
            )

        logger.info("Pipeline complete. %d topic(s) ready.", len(processed))
        return processed

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_raw(self, region: str) -> list[RawTrendData]:
        try:
            raw_data = self._trend_provider.fetch_trends(region)
            logger.info(
                "Fetched %d raw record(s) for region='%s'.", len(raw_data), region
            )
            return raw_data
        except DataExtractionError:
            logger.exception("Data extraction failed for region='%s'.", region)
            raise

    def _save_raw(self, raw_data: list[RawTrendData], filename: str) -> None:
        payload: dict[str, object] = {
            "count": len(raw_data),
            "records": [item.model_dump(mode="json") for item in raw_data],
        }
        self._storage.save_raw(payload, filename)
        logger.info("Saved %d raw record(s)  → '%s'.", len(raw_data), filename)

    def _process(self, raw_data: list[RawTrendData], region: str) -> list[TrendTopic]:
        if not raw_data:
            logger.warning(
                "No raw data to process for region='%s'. Returning empty list.",
                region,
            )
            return []

        top_n = sorted(raw_data, key=lambda r: r.raw_value, reverse=True)[
            : self._top_n
        ]

        topics = [
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
            "Processed %d topic(s) from %d raw record(s).",
            len(topics),
            len(raw_data),
        )
        return topics

    def _generate_angle(self, keyword: str, volume: int) -> str:
        if volume >= _HIGH_VOLUME_THRESHOLD:
            return f"Breaking: Why '{keyword}' is dominating search right now"
        if volume >= self._growth_threshold:
            return f"Emerging trend: What you need to know about '{keyword}'"
        return f"Deep-dive: Exploring the growing interest in '{keyword}'"