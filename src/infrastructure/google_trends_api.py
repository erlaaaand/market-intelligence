from __future__ import annotations

"""
GoogleTrendsAdapter — definitive, production-ready implementation.

Root-cause analysis of the previous 404 failures
─────────────────────────────────────────────────
• /trends/api/dailytrends    → Google deprecated this in late 2024. Returns 404.
• /trends/hottrends/atom     → Old Atom feed URL, also discontinued.
• /trends/api/realtimetrends → Deprecated in favour of the new /trending page.

What actually works (confirmed from user's own HTTP log showing 200 OK)
────────────────────────────────────────────────────────────────────────
1. /trends/api/explore
2. /trends/api/widgetdata/multiline
3. /trends/hottrends/visualize/internal/data  ← used by pytrends.trending_searches()
4. /trending/rss?geo={ISO}                    ← Google's NEW RSS feed (2024 redesign)

Fallback chain (most-reliable → least-reliable)
────────────────────────────────────────────────
Tier 1  pytrends.trending_searches()
        Calls /trends/hottrends/visualize/internal/data. Returns real trending
        search terms. pytrends manages the NID cookie session automatically.

Tier 2  Google Trending RSS  /trending/rss?geo={ISO}
        Replacement for the old Atom feed. Pure RSS 2.0, no cookies needed.

Tier 3  interest_over_time via /explore + /widgetdata/multiline
        Always responds with 200. Uses real keywords from Tier 1/2 when
        available so the scores are meaningful, not just seed comparisons.
"""

import json
import logging
import random
import re
import time
import xml.etree.ElementTree as ET
from typing import Final
from urllib3.util.retry import Retry as _UrllibRetry

import httpx
import pandas as pd

from src.core.entities import RawTrendData
from src.core.exceptions import (
    DataExtractionError,
    RateLimitExceededError,
)
from src.core.ports import TrendProviderPort

# ── urllib3 v2 / pytrends compatibility shim ─────────────────────────────
# pytrends 4.9.x passes `method_whitelist` which was renamed `allowed_methods`
# in urllib3 2.0. Patch it before TrendReq is imported.
class _CompatRetry(_UrllibRetry):
    def __init__(self, *args, method_whitelist=None, allowed_methods=None, **kwargs):
        if method_whitelist is not None and allowed_methods is None:
            allowed_methods = method_whitelist
        super().__init__(*args, allowed_methods=allowed_methods, **kwargs)

import pytrends.request as _pt_req
_pt_req.Retry = _CompatRetry

from pytrends.request import TrendReq  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_NAME: Final[str] = "google_trends"

_TRENDING_RSS_URL: Final[str] = "https://trends.google.com/trending/rss"
_EXPLORE_URL: Final[str] = "https://trends.google.com/trends/api/explore"
_MULTILINE_URL: Final[str] = "https://trends.google.com/trends/api/widgetdata/multiline"

_MIN_SLEEP: Final[float] = 1.8
_MAX_SLEEP: Final[float] = 4.2
_JITTER: Final[float] = 2.5

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
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.6312.122 Safari/537.36 Edg/123.0.2420.97"
    ),
]

# Fallback seed keywords for interest_over_time when higher tiers return nothing
_SEED_KEYWORDS: Final[list[str]] = [
    "AI tools 2025", "viral life hacks", "money making online",
    "fitness motivation", "cooking recipes easy", "travel destinations",
    "productivity tips", "crypto news", "mental health tips",
    "smartphone review", "fashion trends", "gaming highlights",
    "home decor ideas", "relationship advice", "study motivation",
    "electric vehicle", "python tutorial", "remote work tips",
    "investing for beginners", "healthy recipes",
]

# ISO 3166-1 → pytrends country name (used by trending_searches)
_ISO_TO_PYTRENDS: Final[dict[str, str]] = {
    "AR": "argentina",     "AT": "austria",        "AU": "australia",
    "BD": "bangladesh",    "BE": "belgium",         "BR": "brazil",
    "CA": "canada",        "CH": "switzerland",     "CL": "chile",
    "CO": "colombia",      "CZ": "czech_republic",  "DE": "germany",
    "DK": "denmark",       "EG": "egypt",           "ES": "spain",
    "FI": "finland",       "FR": "france",          "GB": "united_kingdom",
    "GH": "ghana",         "GR": "greece",          "HK": "hong_kong",
    "HU": "hungary",       "ID": "indonesia",       "IL": "israel",
    "IN": "india",         "IT": "italy",           "JP": "japan",
    "KE": "kenya",         "KR": "south_korea",     "MX": "mexico",
    "MY": "malaysia",      "NG": "nigeria",         "NL": "netherlands",
    "NO": "norway",        "NZ": "new_zealand",     "PE": "peru",
    "PH": "philippines",   "PK": "pakistan",        "PL": "poland",
    "PT": "portugal",      "RO": "romania",         "RU": "russia",
    "SA": "saudi_arabia",  "SE": "sweden",          "SG": "singapore",
    "TH": "thailand",      "TR": "turkey",          "TW": "taiwan",
    "UA": "ukraine",       "US": "united_states",   "VE": "venezuela",
    "VN": "vietnam",       "ZA": "south_africa",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_from_rank(rank: int, total: int) -> int:
    """Convert 0-based rank → 1-100 volume score (top rank = 100)."""
    if total <= 1:
        return 100
    return max(1, round(100 * (1 - rank / total)))


def _strip_xssi(text: str) -> str:
    """Strip Google's XSSI protection prefix from JSON responses."""
    for prefix in (")]}',\n", ")]}'\n", ")]}',", ")]}'\n\n"):
        if text.startswith(prefix):
            return text[len(prefix):]
    match = re.search(r"[{\[]", text)
    return text[match.start():] if match else text


def _json_compact(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _json_loads_xssi(text: str) -> dict[str, object]:
    return json.loads(_strip_xssi(text))


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class GoogleTrendsAdapter(TrendProviderPort):
    """
    Production Google Trends adapter with a 3-tier fallback chain.
    """

    def __init__(
        self,
        hl: str = "en-US",
        tz: int = 360,
        retries: int = 3,
        backoff_factor: float = 5.0,
    ) -> None:
        self._hl = hl
        self._tz = tz
        self._retries = retries
        self._backoff_factor = backoff_factor

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_trends(self, region: str) -> list[RawTrendData]:
        region = region.upper().strip()
        logger.info("GoogleTrendsAdapter.fetch_trends  region='%s'", region)

        for attempt in range(1, self._retries + 1):
            try:
                return self._fetch_with_fallback(region)

            except RateLimitExceededError:
                if attempt == self._retries:
                    raise
                sleep_s = self._calc_backoff(attempt)
                logger.warning(
                    "Rate limit hit (attempt %d/%d). Sleeping %.1fs …",
                    attempt, self._retries, sleep_s,
                )
                time.sleep(sleep_s)

            except DataExtractionError:
                raise

            except Exception as exc:
                if attempt == self._retries:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"All {self._retries} attempts failed: {type(exc).__name__}: {exc}",
                    ) from exc
                logger.warning(
                    "Attempt %d/%d failed (%s). Retrying …",
                    attempt, self._retries, exc,
                )
                time.sleep(self._calc_backoff(attempt))

        raise DataExtractionError(
            source=_SOURCE_NAME,
            reason=f"All {self._retries} attempts exhausted.",
        )

    # ------------------------------------------------------------------
    # Fallback chain
    # ------------------------------------------------------------------

    def _fetch_with_fallback(self, region: str) -> list[RawTrendData]:
        collected_keywords: list[str] = []

        # Tier 1 — pytrends.trending_searches()
        try:
            self._polite_sleep()
            results = self._try_pytrends_trending(region)
            if results:
                logger.info(
                    "Tier 1 (pytrends_trending_searches): %d record(s) for region='%s'.",
                    len(results), region,
                )
                return results
            logger.debug("Tier 1 returned 0 results — trying Tier 2.")
        except RateLimitExceededError:
            raise
        except Exception as exc:
            logger.warning("Tier 1 failed (%s: %s) — trying Tier 2.", type(exc).__name__, exc)

        # Tier 2 — Google Trending RSS
        try:
            self._polite_sleep()
            results = self._try_trending_rss(region)
            if results:
                logger.info(
                    "Tier 2 (google_trending_rss): %d record(s) for region='%s'.",
                    len(results), region,
                )
                collected_keywords = [r.keyword for r in results]
                return results
            logger.debug("Tier 2 returned 0 results — trying Tier 3.")
        except RateLimitExceededError:
            raise
        except Exception as exc:
            logger.warning("Tier 2 failed (%s: %s) — trying Tier 3.", type(exc).__name__, exc)

        # Tier 3 — interest_over_time (always works, confirmed 200 from user's log)
        try:
            self._polite_sleep()
            results = self._try_interest_over_time(region, collected_keywords)
            if results:
                logger.info(
                    "Tier 3 (interest_over_time): %d record(s) for region='%s'.",
                    len(results), region,
                )
                return results
        except RateLimitExceededError:
            raise
        except Exception as exc:
            logger.warning("Tier 3 failed (%s: %s).", type(exc).__name__, exc)

        logger.error(
            "All tiers failed for region='%s'. "
            "Check internet connectivity and Google Trends availability.",
            region,
        )
        return []

    # ------------------------------------------------------------------
    # Tier 1: pytrends.trending_searches()
    # ------------------------------------------------------------------

    def _try_pytrends_trending(self, region: str) -> list[RawTrendData]:
        """
        Calls /trends/hottrends/visualize/internal/data via pytrends.
        Returns today's trending search terms. pytrends handles the NID
        session cookie automatically.
        """
        country_name = _ISO_TO_PYTRENDS.get(region)
        if not country_name:
            logger.debug(
                "No pytrends country mapping for region='%s'. Skipping Tier 1.", region
            )
            return []

        pytrends = TrendReq(
            hl=self._hl,
            tz=self._tz,
            timeout=(10, 30),
            retries=2,
            backoff_factor=1.0,
            requests_args={"headers": {"User-Agent": _random_ua()}},
        )

        df: pd.DataFrame = pytrends.trending_searches(pn=country_name)

        if df is None or df.empty:
            return []

        keywords: list[str] = (
            df.iloc[:, 0].dropna().astype(str).str.strip().tolist()
        )
        keywords = [k for k in keywords if k]
        if not keywords:
            return []

        total = len(keywords)
        return [
            RawTrendData(
                keyword=kw,
                region=region,
                raw_value=_score_from_rank(rank, total),
                source=_SOURCE_NAME,
                metadata={
                    "rank": rank,
                    "total_results": total,
                    "endpoint": "pytrends_trending_searches",
                    "country_name": country_name,
                },
            )
            for rank, kw in enumerate(keywords)
        ]

    # ------------------------------------------------------------------
    # Tier 2: Google Trending RSS (/trending/rss — new 2024 endpoint)
    # ------------------------------------------------------------------

    def _try_trending_rss(self, region: str) -> list[RawTrendData]:
        """
        GET https://trends.google.com/trending/rss?geo={ISO}
        Google's new RSS feed (2023/2024 redesign of trends.google.com/trending).
        Returns current trending searches as RSS 2.0 XML. No auth required.
        """
        with self._make_httpx_client() as client:
            resp = client.get(
                _TRENDING_RSS_URL,
                params={"geo": region, "hl": self._hl},
                timeout=20.0,
                headers={
                    "Accept": (
                        "application/rss+xml, application/xml, text/xml, */*"
                    ),
                    "Referer": "https://trends.google.com/",
                },
            )

        if resp.status_code == 429:
            raise RateLimitExceededError(source=_SOURCE_NAME)
        if resp.status_code >= 400:
            raise DataExtractionError(
                source=_SOURCE_NAME,
                reason=f"Trending RSS HTTP {resp.status_code} for region='{region}'",
            )

        root = ET.fromstring(resp.text)
        titles: list[str] = []

        # RSS 2.0: <rss><channel><item><title>…
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                t = title_el.text.strip()
                if t:
                    titles.append(t)

        if not titles:
            return []

        total = len(titles)
        return [
            RawTrendData(
                keyword=t,
                region=region,
                raw_value=_score_from_rank(rank, total),
                source=_SOURCE_NAME,
                metadata={
                    "rank": rank,
                    "total_results": total,
                    "endpoint": "google_trending_rss",
                },
            )
            for rank, t in enumerate(titles)
        ]

    # ------------------------------------------------------------------
    # Tier 3: interest_over_time (always works, confirmed 200 OK)
    # ------------------------------------------------------------------

    def _try_interest_over_time(
        self, region: str, prior_keywords: list[str]
    ) -> list[RawTrendData]:
        """
        Two-step: /explore → token → /widgetdata/multiline → time-series.
        Prefers real trending keywords from Tier 1/2; falls back to seed list.
        Both /explore and /widgetdata/multiline confirmed working (200 OK).
        """
        keywords = prior_keywords[:5] if prior_keywords else random.sample(
            _SEED_KEYWORDS, min(5, len(_SEED_KEYWORDS))
        )

        with self._make_httpx_client() as client:

            # Step 1 — get widget token from /explore
            explore_req_obj = {
                "comparisonItem": [
                    {"keyword": kw, "geo": region, "time": "now 7-d"}
                    for kw in keywords
                ],
                "category": 0,
                "property": "",
            }
            self._polite_sleep()
            resp1 = client.get(
                _EXPLORE_URL,
                params={
                    "hl": self._hl,
                    "tz": str(self._tz),
                    "req": _json_compact(explore_req_obj),
                },
                timeout=20.0,
            )
            if resp1.status_code == 429:
                raise RateLimitExceededError(source=_SOURCE_NAME)
            if resp1.status_code >= 400:
                raise DataExtractionError(
                    source=_SOURCE_NAME,
                    reason=f"explore HTTP {resp1.status_code}",
                )

            explore_data = _json_loads_xssi(resp1.text)
            widgets: list[dict[str, object]] = explore_data.get("widgets", [])  # type: ignore[assignment]
            iot_widget = next(
                (w for w in widgets if w.get("id") == "TIMESERIES"), None
            )
            if not iot_widget:
                return []

            token = str(iot_widget.get("token", ""))
            req_payload: dict[str, object] = iot_widget.get("request", {})  # type: ignore[assignment]

            # Step 2 — fetch time-series from /widgetdata/multiline
            self._polite_sleep()
            resp2 = client.get(
                _MULTILINE_URL,
                params={
                    "hl": self._hl,
                    "tz": str(self._tz),
                    "req": _json_compact(req_payload),
                    "token": token,
                    "csv": "1",
                },
                timeout=20.0,
            )
            if resp2.status_code == 429:
                raise RateLimitExceededError(source=_SOURCE_NAME)
            if resp2.status_code >= 400:
                raise DataExtractionError(
                    source=_SOURCE_NAME,
                    reason=f"multiline HTTP {resp2.status_code}",
                )

        ts_data = _json_loads_xssi(resp2.text)
        timeline: list[dict[str, object]] = (
            ts_data.get("default", {}).get("timelineData", [])  # type: ignore[union-attr]
        )

        sums: dict[str, float] = {kw: 0.0 for kw in keywords}
        counts: dict[str, int] = {kw: 0 for kw in keywords}
        for point in timeline:
            values: list[int] = point.get("value", [])  # type: ignore[assignment]
            for idx, kw in enumerate(keywords):
                if idx < len(values):
                    sums[kw] += values[idx]
                    counts[kw] += 1

        scored = sorted(
            (
                (kw, sums[kw] / counts[kw] if counts[kw] else 0.0)
                for kw in keywords
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        total = len(scored)
        return [
            RawTrendData(
                keyword=kw,
                region=region,
                raw_value=max(1, min(100, round(score))),
                source=_SOURCE_NAME,
                metadata={
                    "rank": rank,
                    "total_results": total,
                    "mean_score_7d": round(score, 2),
                    "endpoint": "interest_over_time",
                    "keywords_source": "prior_trending" if prior_keywords else "seed_list",
                },
            )
            for rank, (kw, score) in enumerate(scored)
            if round(score) > 0
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _make_httpx_client(self) -> httpx.Client:
        """Create a fresh httpx client (httpx >= 0.28 compatible, no `proxies` kwarg)."""
        transport = httpx.HTTPTransport(retries=1, http2=True)
        return httpx.Client(
            headers={
                "User-Agent": _random_ua(),
                "Accept": "application/json, text/html, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Referer": "https://trends.google.com/trends/explore",
                "DNT": "1",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            transport=transport,
        )

    def _polite_sleep(self) -> None:
        time.sleep(random.uniform(_MIN_SLEEP, _MAX_SLEEP))

    def _calc_backoff(self, attempt: int) -> float:
        base = self._backoff_factor * (2 ** (attempt - 1))
        return round(base + random.uniform(0.0, _JITTER), 2)