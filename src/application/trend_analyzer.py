from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.core.entities import CreativeDocumentBatch, RawTrendData
from src.core.exceptions import DataExtractionError, LLMAnalysisError
from src.core.ports import LLMPort, StoragePort, TrendProviderPort

logger = logging.getLogger(__name__)

_TOP_N_TOPICS: int = 10
_RAW_FILENAME_TEMPLATE: str = "raw_{region}_{ts}.json"
_PROCESSED_FILENAME_TEMPLATE: str = "creative_docs_{ts}.json"


class TrendAnalyzerUseCase:
    def __init__(
        self,
        trend_provider: TrendProviderPort,
        storage: StoragePort,
        llm: LLMPort,
        top_n: int = _TOP_N_TOPICS,
    ) -> None:
        if top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {top_n}")
        self._trend_provider = trend_provider
        self._storage = storage
        self._llm = llm
        self._top_n = top_n

    def execute(self, region: str) -> CreativeDocumentBatch:
        region = region.upper().strip()
        now: datetime = datetime.now(tz=timezone.utc)
        analysis_date: str = now.strftime("%Y-%m-%d")
        ts_str: str = now.strftime("%H%M%SZ")

        logger.info(
            "Pipeline start  region='%s'  top_n=%d  date=%s",
            region,
            self._top_n,
            analysis_date,
        )

        raw_data = self._fetch_raw(region)

        raw_filename = _RAW_FILENAME_TEMPLATE.format(region=region, ts=ts_str)
        self._save_raw(raw_data, raw_filename)

        top_raw = sorted(raw_data, key=lambda r: r.raw_value, reverse=True)[
            : self._top_n
        ]
        logger.info(
            "Forwarding %d/%d raw record(s) to LLM for region='%s'.",
            len(top_raw),
            len(raw_data),
            region,
        )

        batch = self._analyze_with_llm(top_raw, region, analysis_date)

        if batch.documents:
            processed_filename = _PROCESSED_FILENAME_TEMPLATE.format(ts=ts_str)
            self._storage.save_processed(batch, processed_filename)
            logger.info(
                "Batch saved  region='%s'  documents=%d  file='%s'",
                region,
                len(batch.documents),
                processed_filename,
            )

        logger.info(
            "Pipeline complete  region='%s'  documents=%d.",
            region,
            len(batch.documents),
        )
        return batch

    def _fetch_raw(self, region: str) -> list[RawTrendData]:
        try:
            raw_data = self._trend_provider.fetch_trends(region)
            logger.info(
                "Fetched %d raw record(s) for region='%s'.",
                len(raw_data),
                region,
            )
            return raw_data
        except DataExtractionError as exc:
            logger.error("Trend provider failed for region='%s': %s", region, exc.message)
            raise

    def _save_raw(self, raw_data: list[RawTrendData], filename: str) -> None:
        payload: dict[str, object] = {
            "count": len(raw_data),
            "records": [item.model_dump(mode="json") for item in raw_data],
        }
        self._storage.save_raw(payload, filename)
        logger.info("Raw data persisted  → '%s'  (%d record(s)).", filename, len(raw_data))

    def _analyze_with_llm(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> CreativeDocumentBatch:
        if not raw_data:
            logger.warning(
                "No raw data available for region='%s'. Returning empty batch.",
                region,
            )
            return CreativeDocumentBatch(region=region, date=analysis_date)

        try:
            batch = self._llm.analyze_trends(
                raw_data=raw_data,
                region=region,
                analysis_date=analysis_date,
            )
            logger.info(
                "LLM analysis complete  region='%s'  documents=%d.",
                region,
                len(batch.documents),
            )
            return batch
        except LLMAnalysisError as exc:
            logger.error("LLM analysis failed for region='%s': %s", region, exc.message)
            raise