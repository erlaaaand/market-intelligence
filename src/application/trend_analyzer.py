from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.core.entities import MarketAnalysisReport, RawTrendData, ReportMetadata
from src.core.exceptions import DataExtractionError, LLMAnalysisError
from src.core.ports import LLMPort, StoragePort, TrendProviderPort

logger = logging.getLogger(__name__)

_TOP_N_TOPICS: int = 10
_RAW_FILENAME_TEMPLATE: str = "raw_{region}_{ts}.json"
_PROCESSED_FILENAME_TEMPLATE: str = "market_data_{ts}.json"


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

    def execute(self, region: str) -> MarketAnalysisReport:
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

        report = self._analyze_with_llm(top_raw, region, analysis_date)

        if report.market_trends:
            processed_filename = _PROCESSED_FILENAME_TEMPLATE.format(ts=ts_str)
            self._storage.save_processed(report, processed_filename)
            logger.info(
                "Report saved  region='%s'  trends=%d  file='%s'",
                region,
                len(report.market_trends),
                processed_filename,
            )

        logger.info(
            "Pipeline complete  region='%s'  trends=%d.",
            region,
            len(report.market_trends),
        )
        return report

    def _fetch_raw(self, region: str) -> list[RawTrendData]:
        try:
            raw_data = self._trend_provider.fetch_trends(region)
            logger.info(
                "Fetched %d raw record(s) for region='%s'.",
                len(raw_data),
                region,
            )
            return raw_data
        except DataExtractionError:
            logger.exception(
                "Trend provider failed for region='%s'.", region
            )
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
    ) -> MarketAnalysisReport:
        if not raw_data:
            logger.warning(
                "No raw data available for region='%s'. Returning empty report.",
                region,
            )
            return MarketAnalysisReport(
                metadata=ReportMetadata(region=region, date=analysis_date),
                market_trends=[],
            )

        try:
            report = self._llm.analyze_trends(
                raw_data=raw_data,
                region=region,
                analysis_date=analysis_date,
            )
            logger.info(
                "LLM analysis complete  region='%s'  trends=%d.",
                region,
                len(report.market_trends),
            )
            return report
        except LLMAnalysisError:
            logger.exception(
                "LLM analysis failed for region='%s'.", region
            )
            raise