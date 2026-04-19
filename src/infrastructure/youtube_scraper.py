from __future__ import annotations

import json
import logging
import os
import random
import time
from typing import Final

import httpx

from src.core.entities import RawTrendData
from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.core.ports import TrendProviderPort

logger = logging.getLogger(__name__)

_SOURCE_NAME: Final[str] = "youtube_trending"

_INNERTUBE_BASE: Final[str] = "https://www.youtube.com/youtubei/v1"
_INNERTUBE_KEY: Final[str] = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"  # public YT web key
_INNERTUBE_CLIENT_NAME: Final[str] = "WEB"
_INNERTUBE_CLIENT_VERSION: Final[str] = "2.20240529.01.00"
_BROWSE_ID: Final[str] = "FEtrending"  # YouTube's internal trending page ID

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

            # Try Data API first if key is available (more reliable metadata)
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
                    logger.warning(
                        "Data API failed (%s) — falling back to Innertube.", exc
                    )

            # Primary / fallback: Innertube
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

    def _fetch_via_innertube(
        self, client: httpx.Client, region: str
    ) -> list[RawTrendData]:
        payload = {
            "browseId": _BROWSE_ID,
            "context": {
                "client": {
                    "clientName": _INNERTUBE_CLIENT_NAME,
                    "clientVersion": _INNERTUBE_CLIENT_VERSION,
                    "gl": region,      # country code
                    "hl": "en",        # language
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

        data: dict[str, object] = resp.json()
        return self._parse_innertube_response(data, region)

    def _parse_innertube_response(
        self, data: dict[str, object], region: str
    ) -> list[RawTrendData]:
        video_renderers: list[dict[str, object]] = []

        tabs: list[dict[str, object]] = (
            data.get("contents", {})
            .get("twoColumnBrowseResultsRenderer", {})
            .get("tabs", [])
        )
        for tab in tabs:
            tab_content = (
                tab.get("tabRenderer", {})
                .get("content", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            for section in tab_content:
                items = section.get("itemSectionRenderer", {}).get("contents", [])
                for item in items:
                    shelf = item.get("shelfRenderer", {})
                    shelf_items = (
                        shelf.get("content", {})
                        .get("expandedShelfContentsRenderer", {})
                        .get("items", [])
                    )
                    for shelf_item in shelf_items:
                        vr = shelf_item.get("videoRenderer")
                        if vr:
                            video_renderers.append(vr)

        if not video_renderers:
            # Try a flatter path used by some region responses
            video_renderers = self._extract_flat_video_renderers(data)

        if not video_renderers:
            return []

        return self._video_renderers_to_records(video_renderers, region)

    def _extract_flat_video_renderers(
        self, data: dict[str, object]
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                if "videoId" in node and "title" in node:
                    results.append(node)  # type: ignore[arg-type]
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(data)
        return results

    def _video_renderers_to_records(
        self,
        renderers: list[dict[str, object]],
        region: str,
    ) -> list[RawTrendData]:
        """Convert raw videoRenderer dicts → RawTrendData entities."""
        total = len(renderers)
        records: list[RawTrendData] = []

        for rank, vr in enumerate(renderers):
            title = self._extract_text(vr.get("title"))
            if not title:
                continue

            video_id = str(vr.get("videoId", ""))
            channel = self._extract_text(
                vr.get("longBylineText") or vr.get("ownerText")
            )
            view_count_text = self._extract_text(
                vr.get("viewCountText") or vr.get("shortViewCountText")
            )
            view_count = self._parse_view_count(view_count_text)

            records.append(
                RawTrendData(
                    keyword=title,
                    region=region,
                    raw_value=_score_from_rank(rank, total),
                    source=_SOURCE_NAME,
                    metadata={
                        "video_id": video_id,
                        "channel": channel,
                        "view_count_text": view_count_text,
                        "view_count_approx": view_count,
                        "rank": rank,
                        "total_results": total,
                        "endpoint": "innertube",
                    },
                )
            )

        return records

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
            resp.status_code == 403
            and "quotaExceeded" in resp.text
        ):
            raise RateLimitExceededError(source=_SOURCE_NAME)
        if resp.status_code >= 400:
            raise DataExtractionError(
                source=_SOURCE_NAME,
                reason=f"Data API HTTP {resp.status_code}: {resp.text[:200]}",
            )

        data: dict[str, object] = resp.json()
        items: list[dict[str, object]] = data.get("items", [])
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
                    raw_value=_score_from_rank(rank, total),
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

    @staticmethod
    def _extract_text(obj: object) -> str:
        if obj is None:
            return ""
        if isinstance(obj, str):
            return obj.strip()
        if isinstance(obj, dict):
            if "simpleText" in obj:
                return str(obj["simpleText"]).strip()
            if "runs" in obj:
                runs: list[dict[str, str]] = obj["runs"]
                return "".join(r.get("text", "") for r in runs).strip()
        return ""

    @staticmethod
    def _parse_view_count(text: str) -> int:
        if not text:
            return 0
        t = text.lower().replace("views", "").replace("watching", "").strip()
        multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
        for suffix, mult in multipliers.items():
            if t.endswith(suffix):
                try:
                    return int(float(t[:-1].replace(",", "")) * mult)
                except ValueError:
                    return 0
        try:
            return int(t.replace(",", "").split(".")[0])
        except ValueError:
            return 0

def _score_from_rank(rank: int, total: int) -> int:
    if total <= 1:
        return 100
    return max(1, round(100 * (1 - rank / total)))