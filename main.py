from __future__ import annotations

import logging
import sys

from rich.logging import RichHandler

from config import get_settings
from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.infrastructure.google_trends_api import GoogleTrendsAdapter
from src.infrastructure.llm_adapter import MockLLMAdapter, OllamaLLMAdapter
from src.infrastructure.local_storage import LocalStorageAdapter
from src.infrastructure.youtube_scraper import YouTubeScraperAdapter
from src.infrastructure.web_search import DuckDuckGoSearchAdapter
from src.interfaces.cli import run_cli


def _configure_logging(level: str) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                rich_tracebacks=False,
                markup=True,
                show_path=False,
                omit_repeated_times=False,
            )
        ],
        force=True,
    )


def main() -> None:
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)
    logger = logging.getLogger(__name__)

    logger.info(
        "agent_market_intelligence starting  "
        "trend_provider=[cyan]'%s'[/cyan]  "
        "llm_provider=[magenta]'%s'[/magenta]  "
        "region=[yellow]'%s'[/yellow]  "
        "default_top_n=[green]%d[/green]",
        settings.TREND_PROVIDER,
        settings.LLM_PROVIDER,
        settings.TARGET_REGION,
        settings.LLM_TOP_N,
    )

    storage_adapter = LocalStorageAdapter(
        raw_base_path=settings.RAW_DATA_PATH,
        processed_base_path=settings.PROCESSED_DATA_PATH,
    )

    # ── Trend provider ────────────────────────────────────────────────
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
        web_searcher = DuckDuckGoSearchAdapter(region="wt-wt")
        logger.info("RAG: DuckDuckGoSearchAdapter aktif (real-time grounding).")

        llm_adapter: OllamaLLMAdapter | MockLLMAdapter = OllamaLLMAdapter(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            timeout=settings.OLLAMA_TIMEOUT,
            retries=settings.OLLAMA_RETRIES,
            web_searcher=web_searcher,
            chunk_size=3,   # 3 keyword/call — aman untuk model 7b
        )
        logger.info(
            "LLM adapter: OllamaLLMAdapter  url='%s'  model='%s'  chunk_size=3",
            settings.OLLAMA_BASE_URL,
            settings.OLLAMA_MODEL,
        )
    else:
        llm_adapter = MockLLMAdapter()
        logger.info(
            "LLM adapter: MockLLMAdapter (offline — set LLM_PROVIDER=ollama untuk model nyata)"
        )

    # top_n di use_case = default fallback jika CLI tidak override
    use_case = TrendAnalyzerUseCase(
        trend_provider=trend_adapter,
        storage=storage_adapter,
        llm=llm_adapter,
        top_n=settings.LLM_TOP_N,
    )

    run_cli(
        use_case=use_case,
        default_region=settings.TARGET_REGION,
        default_top_n=settings.LLM_TOP_N,   # ← diteruskan ke CLI untuk prompt default
    )


if __name__ == "__main__":
    main()