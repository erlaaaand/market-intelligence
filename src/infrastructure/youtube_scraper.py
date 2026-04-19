from __future__ import annotations

import logging
import os
import random
import time
from typing import Final

import httpx

from src.core.entities import RawTrendData
from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.core.ports import TrendProviderPort
from src.infrastructure.youtube_parser import (
    parse_innertube_response,
    score_from_rank,
    extract_text,
    parse_view_count,
)

logger = logging.getLogger(__name__)

_SOURCE_NAME: Final[str] = "youtube_trending"

_INNERTUBE_BASE: Final[str] = "https://www.youtube.com/youtubei/v1"
_INNERTUBE_KEY: Final[str] = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"  # public YT web key
_INNERTUBE_CLIENT_NAME: Final[str] = "WEB"
_INNERTUBE_CLIENT_VERSION: Final[str] = "2.20240529.01.00"
_BROWSE_ID: Final[str] = "FEtrending"

_YT_DATA_BASE: Final[str] = "https://www.googleapis.com/youtube/v3"
_YT_DATA_MAX_RESULTS: Final[int] = 50

_POLITE_DELAY: Final[tuple[float, float]] = (1.0, 2.5)

_USER_AGENTS: Final[list[str]] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.6367.118 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
]


class YouTubeScraperAdapter(TrendProviderPort):

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = _YT_DATA_MAX_RESULTS,
        retries: int = 3,
        warn_on_use: bool = False,  # kept for backward compat
    ) -> None:
        self._api_key: str | None = api_key or os.environ.get("YOUTUBE_API_KEY")
        self._max_results = min(max(1, max_results), 50)
        self._retries = retries

    def fetch_trends(self, region: str) -> list[RawTrendData]:
        region = region.upper().strip()
        logger.info("YouTubeScraperAdapter.fetch_trends  region='%s'", region)

        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            headers={"User-Agent": random.choice(_USER_AGENTS)},
        ) as client:

            if self._api_key:
                try:
                    results = self._fetch_via_data_api(client, region)
                    if results:
                        logger.info(
                            "Data API returned %d record(s) for region='%s'.",
                            len(results), region,
                        )
                        return results
                    logger.debug("Data API returned 0 results — falling back to Innertube.")
                except RateLimitExceededError:
                    logger.warning(
                        "YouTube Data API quota exceeded — falling back to Innertube."
                    )
                except Exception as exc:
                    logger.warning("Data API failed (%s) — falling back to Innertube.", exc)

            try:
                results = self._fetch_via_innertube(client, region)
                if results:
                    logger.info(
                        "Innertube returned %d record(s) for region='%s'.",
                        len(results), region,
                    )
                    return results
            except RateLimitExceededError:
                raise
            except Exception as exc:
                raise DataExtractionError(
                    source=_SOURCE_NAME,
                    reason=f"Innertube fetch failed: {type(exc).__name__}: {exc}",
                ) from exc

        logger.warning(
            "YouTubeScraperAdapter: no results for region='%s'. "
            "Check connectivity or try a different region.",
            region,
        )
        return []

    # ── Innertube ─────────────────────────────────────────────────────

    def _fetch_via_innertube(
        self, client: httpx.Client, region: str
    ) -> list[RawTrendData]:
        payload = {
            "browseId": _BROWSE_ID,
            "context": {
                "client": {
                    "clientName": _INNERTUBE_CLIENT_NAME,
                    "clientVersion": _INNERTUBE_CLIENT_VERSION,
                    "gl": region,
                    "hl": "en",
                    "utcOffsetMinutes": 0,
                }
            },
        }

        time.sleep(random.uniform(*_POLITE_DELAY))
        resp = client.post(
            f"{_INNERTUBE_BASE}/browse",
            params={"key": _INNERTUBE_KEY, "prettyPrint": "false"},
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-YouTube-Client-Name": "1",
                "X-YouTube-Client-Version": _INNERTUBE_CLIENT_VERSION,
                "Origin": "https://www.youtube.com",
                "Referer": "https://www.youtube.com/feed/trending",
            },
            timeout=25.0,
        )

        if resp.status_code == 429:
            raise RateLimitExceededError(source=_SOURCE_NAME)
        if resp.status_code >= 400:
            raise DataExtractionError(
                source=_SOURCE_NAME,
                reason=f"Innertube HTTP {resp.status_code}",
            )

        return parse_innertube_response(resp.json(), region, _SOURCE_NAME)

    # ── YouTube Data API v3 ───────────────────────────────────────────

    def _fetch_via_data_api(
        self, client: httpx.Client, region: str
    ) -> list[RawTrendData]:
        params = {
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": region,
            "maxResults": str(self._max_results),
            "key": self._api_key,
        }
        time.sleep(random.uniform(*_POLITE_DELAY))
        resp = client.get(
            f"{_YT_DATA_BASE}/videos",
            params=params,
            timeout=20.0,
        )

        if resp.status_code == 429 or (
            resp.status_code == 403 and "quotaExceeded" in resp.text
        ):
            raise RateLimitExceededError(source=_SOURCE_NAME)
        if resp.status_code >= 400:
            raise DataExtractionError(
                source=_SOURCE_NAME,
                reason=f"Data API HTTP {resp.status_code}: {resp.text[:200]}",
            )

        items: list[dict[str, object]] = resp.json().get("items", [])
        if not items:
            return []

        total = len(items)
        records: list[RawTrendData] = []
        for rank, item in enumerate(items):
            snippet: dict[str, object] = item.get("snippet", {})
            stats: dict[str, object] = item.get("statistics", {})
            title = str(snippet.get("title", "")).strip()
            if not title:
                continue

            view_count_str = str(stats.get("viewCount", "0"))
            view_count = int(view_count_str) if view_count_str.isdigit() else 0

            records.append(
                RawTrendData(
                    keyword=title,
                    region=region,
                    raw_value=score_from_rank(rank, total),
                    source=_SOURCE_NAME,
                    metadata={
                        "video_id": str(item.get("id", "")),
                        "channel": str(snippet.get("channelTitle", "")),
                        "category_id": str(snippet.get("categoryId", "")),
                        "view_count": view_count,
                        "rank": rank,
                        "total_results": total,
                        "endpoint": "data_api_v3",
                    },
                )
            )

        return records