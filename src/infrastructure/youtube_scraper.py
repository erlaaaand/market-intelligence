# src/infrastructure/youtube_scraper.py

"""
YouTube Trends adapter — stub implementation of `TrendProviderPort`.

This adapter is a placeholder for a future YouTube Data API or scraping
integration. It currently returns a small set of hardcoded `RawTrendData`
records so that the rest of the pipeline can be exercised end-to-end
without a live YouTube API key.

TODO:
  - Integrate with the YouTube Data API v3 (`/videos?chart=mostPopular`).
  - Parse video titles/tags into `RawTrendData.keyword`.
  - Map view-count percentile to `raw_value` (0–100 scale).
"""

import logging

from src.core.entities import RawTrendData
from src.core.ports import TrendProviderPort

logger = logging.getLogger(__name__)

_SOURCE_NAME: str = "youtube_stub"

# Hardcoded representative data for development / smoke-testing
_STUB_KEYWORDS: list[tuple[str, int]] = [
    ("AI Video Generation Tools", 95),
    ("Viral Travel Destinations 2025", 88),
    ("Budget Smartphone Review", 76),
    ("Home Workout No Equipment", 65),
    ("Python for Beginners Tutorial", 55),
]


class YouTubeScraperAdapter(TrendProviderPort):
    """
    Stub implementation of `TrendProviderPort` backed by hardcoded data.

    Replace the body of `fetch_trends` with real YouTube API calls
    once an API key is available and the integration is prioritised.
    """

    def fetch_trends(self, region: str) -> list[RawTrendData]:
        """
        Return a hardcoded list of trending YouTube topics.

        Args:
            region: ISO 3166-1 alpha-2 country code (ignored in stub).

        Returns:
            A fixed list of `RawTrendData` entities for smoke-testing.
        """
        region = region.upper().strip()
        logger.warning(
            "YouTubeScraperAdapter is a STUB. "
            "Returning hardcoded data for region='%s'. "
            "Replace with a real YouTube API integration before production use.",
            region,
        )

        return [
            RawTrendData(
                keyword=keyword,
                region=region,
                raw_value=volume,
                source=_SOURCE_NAME,
                metadata={"stub": True},
            )
            for keyword, volume in _STUB_KEYWORDS
        ]