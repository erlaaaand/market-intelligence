from __future__ import annotations

import logging
import random
import time

import httpx

from src.core.entities import RawTrendData
from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.core.ports import TrendProviderPort
from src.infrastructure.google_trends.constants import (
    SOURCE_NAME,
    MIN_SLEEP,
    MAX_SLEEP,
    JITTER,
    USER_AGENTS,
    random_ua,
)
from src.infrastructure.google_trends.tier1_tier2 import (
    fetch_tier1_pytrends,
    fetch_tier2_rss,
)
from src.infrastructure.google_trends.tier3 import fetch_tier3_interest_over_time

logger = logging.getLogger(__name__)


class GoogleTrendsAdapter(TrendProviderPort):
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

    # ── Public interface ──────────────────────────────────────────────

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
                    "Rate limit hit (attempt %d/%d). Sleeping %.1fs ...",
                    attempt, self._retries, sleep_s,
                )
                time.sleep(sleep_s)

            except DataExtractionError:
                raise

            except Exception as exc:
                if attempt == self._retries:
                    raise DataExtractionError(
                        source=SOURCE_NAME,
                        reason=f"All {self._retries} attempts failed: {type(exc).__name__}: {exc}",
                    ) from exc
                logger.warning(
                    "Attempt %d/%d failed (%s). Retrying ...",
                    attempt, self._retries, exc,
                )
                time.sleep(self._calc_backoff(attempt))

        raise DataExtractionError(
            source=SOURCE_NAME,
            reason=f"All {self._retries} attempts exhausted.",
        )

    # ── 3-tier fallback logic ─────────────────────────────────────────

    def _fetch_with_fallback(self, region: str) -> list[RawTrendData]:
        collected_keywords: list[str] = []

        # Tier 1
        try:
            self._polite_sleep()
            results = fetch_tier1_pytrends(region, self._hl, self._tz)
            if results:
                logger.info(
                    "Tier 1 (pytrends_trending_searches): %d record(s) for region='%s'.",
                    len(results), region,
                )
                return results
            logger.debug("Tier 1 returned 0 results - trying Tier 2.")
        except RateLimitExceededError:
            raise
        except Exception as exc:
            logger.warning("Tier 1 failed (%s: %s) - trying Tier 2.", type(exc).__name__, exc)

        # Tier 2
        try:
            self._polite_sleep()
            with self._make_httpx_client() as client:
                results = fetch_tier2_rss(region, self._hl, client)
            if results:
                logger.info(
                    "Tier 2 (google_trending_rss): %d record(s) for region='%s'.",
                    len(results), region,
                )
                collected_keywords = [r.keyword for r in results]
                return results
            logger.debug("Tier 2 returned 0 results - trying Tier 3.")
        except RateLimitExceededError:
            raise
        except Exception as exc:
            logger.warning("Tier 2 failed (%s: %s) - trying Tier 3.", type(exc).__name__, exc)

        # Tier 3
        try:
            self._polite_sleep()
            with self._make_httpx_client() as client:
                results = fetch_tier3_interest_over_time(
                    region, self._hl, self._tz,
                    collected_keywords, client,
                    self._polite_sleep,
                )
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

    # ── Shared utilities ──────────────────────────────────────────────

    def _make_httpx_client(self) -> httpx.Client:
        transport = httpx.HTTPTransport(retries=1, http2=True)
        return httpx.Client(
            headers={
                "User-Agent": random_ua(),
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
        time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))

    def _calc_backoff(self, attempt: int) -> float:
        base = self._backoff_factor * (2 ** (attempt - 1))
        return round(base + random.uniform(0.0, JITTER), 2)