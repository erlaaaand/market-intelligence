# src/infrastructure/google_trends_api.py

"""
Google Trends adapter — concrete implementation of `TrendProviderPort`.

Strategy:
  Primary endpoint   → `today_searches(pn=ISO_CODE)`
                        Returns a pandas Series of trending title strings.
                        Accepts ISO 3166-1 alpha-2 codes directly (e.g. "US", "ID").
  Fallback endpoint  → `realtime_trending_searches(pn=ISO_CODE)`
                        Returns a DataFrame with `entityNames` and `title` columns.

Defensive measures against HTTP 429:
  - Randomised pre-request sleep (polite crawling delay).
  - Fake User-Agent rotation.
  - Configurable retry count with exponential back-off + jitter.
  - Explicit guard so domain exceptions are never swallowed by the bare
    `except Exception` handler.
"""
from __future__ import annotations

import logging
import random
import time

from pytrends.exceptions import ResponseError
from pytrends.request import TrendReq

from src.core.entities import RawTrendData
from src.core.exceptions import (
    AgentMarketIntelligenceError,
    DataExtractionError,
    RateLimitExceededError,
)
from src.core.ports import TrendProviderPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SOURCE_NAME: str = "google_trends"
_MAX_RETRIES: int = 3
_BASE_BACKOFF_SECONDS: float = 5.0
_MAX_JITTER_SECONDS: float = 3.0
_MIN_POLITE_DELAY: float = 1.5   # seconds to sleep before each request
_MAX_POLITE_DELAY: float = 3.0

# Realistic browser User-Agent strings — rotated per session to reduce 429s
_USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
    ),
]


class GoogleTrendsAdapter(TrendProviderPort):
    """
    Fetches real-time trending topics from Google Trends via `pytrends`.

    Endpoint strategy (with automatic fallback):
        1. `today_searches(pn=region)` — daily trending searches (ISO code).
        2. `realtime_trending_searches(pn=region)` — realtime stories (ISO code).

    Each call builds a fresh `TrendReq` session with a random User-Agent.
    Retries with exponential back-off + jitter are applied on HTTP 429.
    """

    def __init__(
        self,
        hl: str = "en-US",
        tz: int = 360,
        retries: int = _MAX_RETRIES,
        backoff_factor: float = _BASE_BACKOFF_SECONDS,
    ) -> None:
        """
        Initialise the adapter.

        Args:
            hl:             Host language for the Trends UI (e.g. "en-US", "id-ID").
            tz:             UTC offset in minutes (420 = UTC+7 for WIB).
            retries:        Maximum retry attempts on transient HTTP errors.
            backoff_factor: Base sleep duration (s) for exponential back-off.
        """
        self._hl = hl
        self._tz = tz
        self._retries = retries
        self._backoff_factor = backoff_factor

    # ------------------------------------------------------------------
    # TrendProviderPort
    # ------------------------------------------------------------------

    def fetch_trends(self, region: str) -> list[RawTrendData]:
        """
        Fetch trending topics for *region* with retry and fallback logic.

        Args:
            region: ISO 3166-1 alpha-2 country code (e.g. "US", "ID").

        Returns:
            List of `RawTrendData` entities, ordered by synthetic volume score.

        Raises:
            RateLimitExceededError: After all retries are exhausted on HTTP 429.
            DataExtractionError:    On any other unrecoverable failure.
        """
        region = region.upper().strip()
        logger.info("Fetching Google Trends for region='%s'.", region)

        last_exc: Exception | None = None

        for attempt in range(1, self._retries + 1):
            try:
                client = self._build_client()
                # ── Primary endpoint ──────────────────────────────────
                results = self._try_today_searches(client, region)
                if results:
                    return results

                # ── Fallback endpoint ─────────────────────────────────
                logger.info(
                    "today_searches returned no data; trying realtime endpoint "
                    "for region='%s'.",
                    region,
                )
                return self._try_realtime_searches(client, region)

            except AgentMarketIntelligenceError:
                # Never swallow our own domain exceptions — re-raise immediately.
                raise

            except ResponseError as exc:
                status_code = self._extract_status_code(exc)
                last_exc = exc

                if status_code == 429:
                    if attempt == self._retries:
                        logger.error(
                            "Rate limit (429) on final attempt %d/%d for region='%s'.",
                            attempt,
                            self._retries,
                            region,
                        )
                        raise RateLimitExceededError(source=_SOURCE_NAME) from exc

                    sleep_s = self._backoff_with_jitter(attempt)
                    logger.warning(
                        "Rate limit (429) on attempt %d/%d. Sleeping %.1fs…",
                        attempt,
                        self._retries,
                        sleep_s,
                    )
                    time.sleep(sleep_s)

                else:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"HTTP {status_code} from Google Trends: {exc}",
                    ) from exc

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Attempt %d/%d failed with %s: %s",
                    attempt,
                    self._retries,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self._retries:
                    time.sleep(self._backoff_with_jitter(attempt))
                else:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"{type(exc).__name__}: {exc}",
                    ) from exc

        # Unreachable in practice — satisfies the type-checker.
        raise DataExtractionError(
            source=_SOURCE_NAME,
            reason=f"All {self._retries} attempts failed. Last error: {last_exc}",
        )

    # ------------------------------------------------------------------
    # Endpoint implementations
    # ------------------------------------------------------------------

    def _try_today_searches(
        self, client: TrendReq, region: str
    ) -> list[RawTrendData]:
        """
        Query `today_searches` (daily trending searches).

        Returns a pandas Series where each value is a trend title string.
        Accepts ISO 3166-1 alpha-2 codes directly.

        Args:
            client: Configured `TrendReq` session.
            region: ISO country code.

        Returns:
            Parsed list of `RawTrendData`, or empty list on no data.
        """
        time.sleep(random.uniform(_MIN_POLITE_DELAY, _MAX_POLITE_DELAY))

        series = client.today_searches(pn=region)

        if series is None or series.empty:
            logger.debug(
                "today_searches returned empty result for region='%s'.", region
            )
            return []

        titles: list[str] = series.dropna().tolist()
        total = len(titles)
        records: list[RawTrendData] = []

        for rank, title in enumerate(titles):
            if not isinstance(title, str) or not title.strip():
                continue
            synthetic_volume = max(1, round(100 * (1 - rank / max(total, 1))))
            records.append(
                RawTrendData(
                    keyword=title.strip(),
                    region=region,
                    raw_value=synthetic_volume,
                    source=_SOURCE_NAME,
                    metadata={
                        "rank": rank,
                        "total_results": total,
                        "endpoint": "today_searches",
                    },
                )
            )

        logger.info(
            "today_searches: parsed %d record(s) for region='%s'.",
            len(records),
            region,
        )
        return records

    def _try_realtime_searches(
        self, client: TrendReq, region: str
    ) -> list[RawTrendData]:
        """
        Query `realtime_trending_searches` (realtime trending stories).

        Returns a DataFrame with columns `entityNames` (list) and `title` (str).
        Accepts ISO 3166-1 alpha-2 codes directly.

        Args:
            client: Configured `TrendReq` session.
            region: ISO country code.

        Returns:
            Parsed list of `RawTrendData`, or empty list on no data.
        """
        time.sleep(random.uniform(_MIN_POLITE_DELAY, _MAX_POLITE_DELAY))

        df = client.realtime_trending_searches(pn=region, cat="all", count=20)

        if df is None or df.empty:
            logger.warning(
                "realtime_trending_searches returned empty result for region='%s'.",
                region,
            )
            return []

        records: list[RawTrendData] = []
        total = len(df)

        for rank, row in enumerate(df.itertuples(index=False)):
            # Each row has: title (str), entityNames (list[str])
            title: str = getattr(row, "title", "") or ""
            if not title.strip():
                continue

            synthetic_volume = max(1, round(100 * (1 - rank / max(total, 1))))
            records.append(
                RawTrendData(
                    keyword=title.strip(),
                    region=region,
                    raw_value=synthetic_volume,
                    source=_SOURCE_NAME,
                    metadata={
                        "rank": rank,
                        "total_results": total,
                        "endpoint": "realtime_trending_searches",
                    },
                )
            )

        logger.info(
            "realtime_trending_searches: parsed %d record(s) for region='%s'.",
            len(records),
            region,
        )
        return records

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_client(self) -> TrendReq:
        """Build a fresh `TrendReq` session with a random User-Agent."""
        user_agent = random.choice(_USER_AGENTS)
        return TrendReq(
            hl=self._hl,
            tz=self._tz,
            timeout=(10, 30),
            requests_args={"headers": {"User-Agent": user_agent}},
        )

    def _backoff_with_jitter(self, attempt: int) -> float:
        """Exponential back-off with uniform random jitter."""
        exponential = self._backoff_factor * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, _MAX_JITTER_SECONDS)
        return round(exponential + jitter, 2)

    @staticmethod
    def _extract_status_code(exc: ResponseError) -> int:
        """Safely extract the HTTP status code from a `ResponseError`."""
        try:
            return int(exc.response.status_code)
        except (AttributeError, ValueError, TypeError):
            return 0