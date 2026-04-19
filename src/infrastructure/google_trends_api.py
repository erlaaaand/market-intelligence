# src/infrastructure/google_trends_api.py

"""
Google Trends adapter — concrete implementation of `TrendProviderPort`.

Uses `pytrends` to query the Google Trends Realtime Search Trends endpoint.
Includes defensive measures against HTTP 429 (rate limiting):
  - Randomised sleep between requests.
  - Fake user-agent rotation via the `requests` session.
  - Configurable retry attempts with exponential back-off.
"""

import logging
import random
import time

from pytrends.exceptions import ResponseError
from pytrends.request import TrendReq

from src.core.entities import RawTrendData
from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.core.ports import TrendProviderPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
_SOURCE_NAME: str = "google_trends"
_DEFAULT_CATEGORY: int = 0          # All categories
_DEFAULT_TIMEFRAME: str = "now 1-d"  # Last 24 hours
_MAX_RETRIES: int = 3
_BASE_BACKOFF_SECONDS: float = 5.0
_MAX_JITTER_SECONDS: float = 3.0

# Rotate through realistic browser User-Agent strings to reduce 429 probability
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
]


class GoogleTrendsAdapter(TrendProviderPort):
    """
    Fetches trending topics from Google Trends via `pytrends`.

    Each call to `fetch_trends` creates a fresh `TrendReq` session with a
    randomly selected User-Agent to mitigate rate-limiting. Retries with
    exponential back-off are applied automatically on 429 responses.
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
            hl:             Host language for the Trends UI (e.g. "en-US").
            tz:             Timezone offset in minutes from UTC (360 = UTC-6).
            retries:        Maximum number of retry attempts on transient errors.
            backoff_factor: Base sleep duration (seconds) for exponential back-off.
        """
        self._hl = hl
        self._tz = tz
        self._retries = retries
        self._backoff_factor = backoff_factor

    # ------------------------------------------------------------------
    # TrendProviderPort implementation
    # ------------------------------------------------------------------

    def fetch_trends(self, region: str) -> list[RawTrendData]:
        """
        Fetch real-time trending topics for the given region.

        Args:
            region: ISO 3166-1 alpha-2 country code (e.g. "US", "ID").

        Returns:
            A list of `RawTrendData` entities parsed from the Google Trends
            "trending searches" response.

        Raises:
            RateLimitExceededError: If Google returns HTTP 429 after all retries.
            DataExtractionError:    For any other failure (network, parsing, etc.).
        """
        region = region.upper().strip()
        logger.info("Fetching Google Trends for region='%s'.", region)

        for attempt in range(1, self._retries + 1):
            try:
                pytrends = self._build_client()
                return self._query_trending_searches(pytrends, region)

            except ResponseError as exc:
                status_code = self._extract_status_code(exc)

                if status_code == 429:
                    if attempt == self._retries:
                        logger.error(
                            "Rate limit hit on final attempt (%d/%d) for region='%s'.",
                            attempt,
                            self._retries,
                            region,
                        )
                        raise RateLimitExceededError(source=_SOURCE_NAME) from exc

                    sleep_duration = self._backoff_with_jitter(attempt)
                    logger.warning(
                        "Rate limit (429) on attempt %d/%d. Sleeping %.1fs before retry.",
                        attempt,
                        self._retries,
                        sleep_duration,
                    )
                    time.sleep(sleep_duration)

                else:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"HTTP {status_code} — {exc}",
                    ) from exc

            except Exception as exc:  # noqa: BLE001
                raise DataExtractionError(
                    source=_SOURCE_NAME,
                    reason=str(exc),
                ) from exc

        # Should be unreachable, but satisfies the type checker.
        raise DataExtractionError(
            source=_SOURCE_NAME, reason="Exhausted all retries without success."
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_client(self) -> TrendReq:
        """Create a new `TrendReq` session with a random User-Agent."""
        user_agent = random.choice(_USER_AGENTS)
        return TrendReq(
            hl=self._hl,
            tz=self._tz,
            requests_args={"headers": {"User-Agent": user_agent}},
        )

    def _query_trending_searches(
        self, pytrends: TrendReq, region: str
    ) -> list[RawTrendData]:
        """
        Execute the trending searches query and map results to `RawTrendData`.

        Google Trends' `trending_searches` endpoint returns a DataFrame where:
          - The index represents a relative rank (0 = most trending).
          - Column 0 contains the keyword string.

        We convert the rank into a synthetic search volume score (100 → 1)
        because the endpoint does not return absolute volume figures.

        Args:
            pytrends: A configured `TrendReq` instance.
            region:   ISO 3166-1 alpha-2 country code (e.g. "US").

        Returns:
            List of `RawTrendData` entities.
        """
        # Polite delay before the request
        time.sleep(random.uniform(1.0, 2.5))

        df = pytrends.trending_searches(pn=region.lower())

        if df is None or df.empty:
            logger.warning("Google Trends returned an empty dataset for region='%s'.", region)
            return []

        total: int = len(df)
        raw_records: list[RawTrendData] = []

        for rank, keyword in enumerate(df[0].tolist()):
            if not isinstance(keyword, str) or not keyword.strip():
                continue

            # Synthetic volume: top-ranked = 100, scales linearly to minimum 1
            synthetic_volume: int = max(1, round(100 * (1 - rank / total)))

            raw_records.append(
                RawTrendData(
                    keyword=keyword.strip(),
                    region=region,
                    raw_value=synthetic_volume,
                    source=_SOURCE_NAME,
                    metadata={"rank": rank, "total_results": total},
                )
            )

        logger.info(
            "Parsed %d trend record(s) from Google Trends for region='%s'.",
            len(raw_records),
            region,
        )
        return raw_records

    def _backoff_with_jitter(self, attempt: int) -> float:
        """Calculate exponential back-off with random jitter."""
        exponential = self._backoff_factor * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, _MAX_JITTER_SECONDS)
        return exponential + jitter

    @staticmethod
    def _extract_status_code(exc: ResponseError) -> int:
        """Safely extract the HTTP status code from a `ResponseError`."""
        try:
            return int(exc.response.status_code)
        except (AttributeError, ValueError):
            return 0