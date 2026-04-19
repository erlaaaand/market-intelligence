from __future__ import annotations

# main.py

import logging
import sys

from config import get_settings
from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.infrastructure.google_trends_api import GoogleTrendsAdapter
from src.infrastructure.llm_adapter import MockLLMAdapter, OllamaLLMAdapter
from src.infrastructure.local_storage import LocalStorageAdapter
from src.infrastructure.youtube_scraper import YouTubeScraperAdapter
from src.interfaces.cli import run_cli


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def main() -> None:
    settings = get_settings()

    _configure_logging(settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)
    logger.info(
        "agent_market_intelligence starting  "
        "trend_provider='%s'  llm_provider='%s'  region='%s'",
        settings.TREND_PROVIDER,
        settings.LLM_PROVIDER,
        settings.TARGET_REGION,
    )

    # ── Storage adapter ───────────────────────────────────────────────
    storage_adapter = LocalStorageAdapter(
        raw_base_path=settings.RAW_DATA_PATH,
        processed_base_path=settings.PROCESSED_DATA_PATH,
        briefs_base_path=settings.BRIEFS_DATA_PATH,
    )

    # ── Trend provider adapter ────────────────────────────────────────
    if settings.TREND_PROVIDER == "youtube":
        trend_adapter: GoogleTrendsAdapter | YouTubeScraperAdapter = (
            YouTubeScraperAdapter()
        )
        logger.info("Trend provider: YouTubeScraperAdapter (Innertube)")
    else:
        trend_adapter = GoogleTrendsAdapter(
            hl=settings.PYTRENDS_HL,
            tz=settings.PYTRENDS_TZ,
            retries=settings.PYTRENDS_RETRIES,
            backoff_factor=settings.PYTRENDS_BACKOFF_FACTOR,
        )
        logger.info("Trend provider: GoogleTrendsAdapter (3-tier fallback)")

    # ── LLM adapter ───────────────────────────────────────────────────
    if settings.LLM_PROVIDER == "ollama":
        llm_adapter: OllamaLLMAdapter | MockLLMAdapter = OllamaLLMAdapter(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            timeout=settings.OLLAMA_TIMEOUT,
            retries=settings.OLLAMA_RETRIES,
        )
        logger.info(
            "LLM adapter: OllamaLLMAdapter  url='%s'  model='%s'",
            settings.OLLAMA_BASE_URL,
            settings.OLLAMA_MODEL,
        )
    else:
        llm_adapter = MockLLMAdapter()
        logger.info(
            "LLM adapter: MockLLMAdapter (offline mode — set LLM_PROVIDER=ollama "
            "in .env to use a real model)"
        )

    # ── Use-case ──────────────────────────────────────────────────────
    use_case = TrendAnalyzerUseCase(
        trend_provider=trend_adapter,
        storage=storage_adapter,
        llm=llm_adapter,
        top_n=settings.LLM_TOP_N,
    )

    run_cli(use_case=use_case, default_region=settings.TARGET_REGION)


if __name__ == "__main__":
    main()