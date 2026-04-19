from __future__ import annotations

# src/application/trend_analyzer.py

"""
TrendAnalyzerUseCase — orchestrates the full market-intelligence pipeline.

Pipeline flow (v2 — LLM-powered)
──────────────────────────────────
1. fetch_raw(region)          → list[RawTrendData]   via TrendProviderPort
2. save_raw(payload)          → side-effect           via StoragePort
3. llm.analyze_trends(...)    → MarketAnalysisReport  via LLMPort
4. save_processed(report)     → side-effect           via StoragePort
5. return MarketAnalysisReport

Design notes
────────────
• The use-case knows nothing about HTTP, files, or LLM providers — it only
  speaks to ports.
• ``top_n`` caps the number of raw records forwarded to the LLM; sending
  fewer tokens reduces latency and cost.
• When the LLM call fails with ``LLMAnalysisError``, the exception is logged
  and re-raised so the CLI can show a friendly error without crashing.
• The raw data is always persisted (step 2) even if the LLM later fails,
  providing an audit trail and enabling manual re-processing.
"""

import logging
from datetime import datetime, timezone

from src.core.entities import MarketAnalysisReport, RawTrendData
from src.core.exceptions import DataExtractionError, LLMAnalysisError
from src.core.ports import LLMPort, StoragePort, TrendProviderPort

logger = logging.getLogger(__name__)

_TOP_N_TOPICS: int = 10          # max raw records sent to the LLM
_RAW_FILENAME_TEMPLATE: str = "raw_{region}_{ts}.json"
_PROCESSED_FILENAME_TEMPLATE: str = "market_data_{ts}.json"


class TrendAnalyzerUseCase:
    """
    Application-layer orchestrator for the trend-analysis pipeline.

    Args:
        trend_provider: Adapter for fetching raw trending data.
        storage:        Adapter for persisting raw and processed outputs.
        llm:            Adapter for the LLM deep-analytics call.
        top_n:          Maximum number of raw records to forward to the LLM.
                        Defaults to 10.
    """

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, region: str) -> MarketAnalysisReport:
        """
        Run the full pipeline for *region* and return the analysis report.

        Args:
            region: ISO 3166-1 alpha-2 country code (case-insensitive).

        Returns:
            A validated ``MarketAnalysisReport`` populated by the LLM.

        Raises:
            DataExtractionError: If the trend provider fails.
            LLMAnalysisError:    If the LLM cannot produce a valid report.
            StorageError:        If either write operation fails.
        """
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

        # ── Step 1: fetch ─────────────────────────────────────────────
        raw_data = self._fetch_raw(region)

        # ── Step 2: persist raw (always, even if LLM later fails) ─────
        raw_filename = _RAW_FILENAME_TEMPLATE.format(region=region, ts=ts_str)
        self._save_raw(raw_data, raw_filename)

        # ── Step 3: select top-N records for the LLM ──────────────────
        top_raw = sorted(raw_data, key=lambda r: r.raw_value, reverse=True)[
            : self._top_n
        ]
        logger.info(
            "Forwarding %d/%d raw record(s) to LLM for region='%s'.",
            len(top_raw),
            len(raw_data),
            region,
        )

        # ── Step 4: LLM deep analysis ──────────────────────────────────
        report = self._analyze_with_llm(top_raw, region, analysis_date)

        # ── Step 5: persist processed report ──────────────────────────
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
                "Trend provider failed for region='%s'. Raw data unavailable.",
                region,
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
        """
        Call the LLM port and return a validated report.

        If the LLM returns an empty ``market_trends`` list (e.g. all tiers
        failed and the raw data was empty), a degenerate report with zero
        trends is still returned so the pipeline completes gracefully.
        """
        if not raw_data:
            logger.warning(
                "No raw data available for region='%s'. "
                "Returning empty MarketAnalysisReport without LLM call.",
                region,
            )
            from src.core.entities import ReportMetadata  # local import avoids circularity
            return MarketAnalysisReport(
                metadata=ReportMetadata(
                    region=region,
                    date=analysis_date,
                ),
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
                "LLM analysis failed for region='%s'. "
                "Raw data is still available in the raw store for re-processing.",
                region,
            )
            raise