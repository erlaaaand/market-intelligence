# main.py

"""
Composition root for agent_market_intelligence.

This is the ONLY module that imports from `src.infrastructure`.
All concrete adapters are instantiated here and injected into the
use-case, keeping the application and core layers free of infrastructure
dependencies (Dependency Inversion Principle).

Entry point:
    python main.py [--region CC]
"""
from __future__ import annotations

import logging
import sys

from config import get_settings
from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.infrastructure.google_trends_api import GoogleTrendsAdapter
from src.infrastructure.local_storage import LocalStorageAdapter
from src.infrastructure.youtube_scraper import YouTubeScraperAdapter
from src.interfaces.cli import run_cli


def _configure_logging(level: str) -> None:
    """Configure root logger with a timestamp + level + name format."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,   # Override any handler installed by imported libs.
    )


def main() -> None:
    """
    Application entry point.

    Execution order:
        1. Load and validate settings from .env / environment.
        2. Configure root-level logging.
        3. Instantiate the storage adapter.
        4. Instantiate the trend provider adapter (based on TREND_PROVIDER).
        5. Wire everything into `TrendAnalyzerUseCase`.
        6. Hand control to the CLI layer.
    """
    # ── 1. Settings ───────────────────────────────────────────────────
    settings = get_settings()

    # ── 2. Logging ────────────────────────────────────────────────────
    _configure_logging(settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)
    logger.info(
        "agent_market_intelligence starting  provider='%s'  region='%s'",
        settings.TREND_PROVIDER,
        settings.TARGET_REGION,
    )

    # ── 3. Storage adapter ────────────────────────────────────────────
    storage_adapter = LocalStorageAdapter(
        raw_base_path=settings.RAW_DATA_PATH,
        processed_base_path=settings.PROCESSED_DATA_PATH,
    )

    # ── 4. Trend provider adapter ─────────────────────────────────────
    if settings.TREND_PROVIDER == "youtube":
        trend_adapter: GoogleTrendsAdapter | YouTubeScraperAdapter = (
            YouTubeScraperAdapter(warn_on_use=True)
        )
        logger.warning(
            "YouTubeScraperAdapter selected — this is a STUB returning hardcoded data."
        )
    else:
        trend_adapter = GoogleTrendsAdapter(
            hl=settings.PYTRENDS_HL,
            tz=settings.PYTRENDS_TZ,
            retries=settings.PYTRENDS_RETRIES,
            backoff_factor=settings.PYTRENDS_BACKOFF_FACTOR,
        )

    # ── 5. Use-case assembly ──────────────────────────────────────────
    use_case = TrendAnalyzerUseCase(
        trend_provider=trend_adapter,
        storage=storage_adapter,
    )

    # ── 6. CLI handoff ────────────────────────────────────────────────
    run_cli(use_case=use_case, default_region=settings.TARGET_REGION)


if __name__ == "__main__":
    main()