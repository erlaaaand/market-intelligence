# main.py

"""
Composition root for the agent_market_intelligence application.

This module is the ONLY place where concrete infrastructure adapters are
instantiated and wired together. The application and core layers never
import from `src.infrastructure` — only `main.py` does, keeping the
dependency graph clean and adhering to the Dependency Inversion Principle.

Entry point:
    python main.py [--region CC]
"""

import logging
import sys

from config import get_settings
from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.infrastructure.google_trends_api import GoogleTrendsAdapter
from src.infrastructure.local_storage import LocalStorageAdapter
from src.infrastructure.youtube_scraper import YouTubeScraperAdapter
from src.interfaces.cli import run_cli


def _configure_logging(level: str) -> None:
    """Set up root-level logging with a consistent format."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def main() -> None:
    """
    Application entry point.

    Execution order:
      1. Load and validate settings from environment / .env.
      2. Configure logging.
      3. Instantiate concrete infrastructure adapters.
      4. Inject adapters into the use-case (Dependency Injection).
      5. Hand control to the CLI layer.
    """
    # ── 1. Settings ───────────────────────────────────────────────────
    settings = get_settings()

    # ── 2. Logging ────────────────────────────────────────────────────
    _configure_logging(settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)
    logger.info("Starting agent_market_intelligence (provider=%s).", settings.TREND_PROVIDER)

    # ── 3. Infrastructure adapters ────────────────────────────────────
    storage_adapter = LocalStorageAdapter(
        raw_base_path=settings.RAW_DATA_PATH,
        processed_base_path=settings.PROCESSED_DATA_PATH,
    )

    if settings.TREND_PROVIDER == "youtube":
        trend_adapter = YouTubeScraperAdapter()
        logger.warning("Using YouTubeScraperAdapter (stub). Data is hardcoded.")
    else:
        trend_adapter = GoogleTrendsAdapter(
            hl=settings.PYTRENDS_HL,
            tz=settings.PYTRENDS_TZ,
            retries=settings.PYTRENDS_RETRIES,
            backoff_factor=settings.PYTRENDS_BACKOFF_FACTOR,
        )

    # ── 4. Use-case assembly (Dependency Injection) ───────────────────
    use_case = TrendAnalyzerUseCase(
        trend_provider=trend_adapter,
        storage=storage_adapter,
    )

    # ── 5. CLI handoff ────────────────────────────────────────────────
    run_cli(use_case=use_case, default_region=settings.TARGET_REGION)


if __name__ == "__main__":
    main()