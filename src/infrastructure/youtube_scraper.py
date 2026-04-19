# src/infrastructure/youtube_scraper.py

"""
YouTube Trends adapter — stub implementation of `TrendProviderPort`.

Current status: STUB (returns hardcoded data for smoke-testing).

Roadmap to production:
  1. Obtain a YouTube Data API v3 key and set YOUTUBE_API_KEY in .env.
  2. Call GET /videos?part=snippet&chart=mostPopular&regionCode={ISO}&maxResults=20.
  3. Parse `snippet.title` and `snippet.tags` into `RawTrendData.keyword`.
  4. Map view-count percentile within the result set to `raw_value` (0–100).
  5. Remove the `NotImplementedError` guard below.
"""
from __future__ import annotations

import logging

from src.core.entities import RawTrendData
from src.core.ports import TrendProviderPort

logger = logging.getLogger(__name__)

_SOURCE_NAME: str = "youtube_stub"

# Representative hardcoded keywords for development and smoke-testing.
# Values are (keyword, synthetic_volume) pairs.
_STUB_KEYWORDS: list[tuple[str, int]] = [
    ("AI Video Generation Tools", 95),
    ("Viral Travel Destinations 2025", 88),
    ("Budget Smartphone Review", 76),
    ("Home Workout No Equipment", 65),
    ("Python for Beginners Tutorial", 55),
    ("Healthy Meal Prep Ideas", 48),
    ("Electric Vehicle Review", 42),
    ("Crypto Market Analysis", 38),
]


class YouTubeScraperAdapter(TrendProviderPort):
    """
    Stub implementation of `TrendProviderPort` backed by hardcoded data.

    Safe to use in integration tests, CI pipelines, and local development.
    Replace the body of `fetch_trends` with real YouTube API calls once
    the integration is prioritised.
    """

    def __init__(self, *, warn_on_use: bool = True) -> None:
        """
        Args:
            warn_on_use: If True (default), emit a WARNING log on every call
                         as a reminder that this adapter is not production-ready.
        """
        self._warn_on_use = warn_on_use

    def fetch_trends(self, region: str) -> list[RawTrendData]:
        """
        Return a hardcoded list of trending YouTube topics.

        Args:
            region: ISO 3166-1 alpha-2 country code (noted in metadata,
                    but not used to filter stub data).

        Returns:
            A fixed list of `RawTrendData` entities for smoke-testing.
        """
        region = region.upper().strip()

        if self._warn_on_use:
            logger.warning(
                "YouTubeScraperAdapter is a STUB returning hardcoded data "
                "for region='%s'. Do NOT use in production.",
                region,
            )

        return [
            RawTrendData(
                keyword=keyword,
                region=region,
                raw_value=volume,
                source=_SOURCE_NAME,
                metadata={"stub": True, "hardcoded_rank": idx},
            )
            for idx, (keyword, volume) in enumerate(_STUB_KEYWORDS)
        ]