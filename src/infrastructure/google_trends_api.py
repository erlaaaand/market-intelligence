# src/infrastructure/google_trends_api.py

"""
Google Trends adapter — concrete implementation of TrendProviderPort.

ROOT CAUSE ANALYSIS (pytrends 4.9.2):
  - today_searches()           → HTTP 404 (endpoint parameter berubah di Google)
  - realtime_trending_searches() → HTTP 404 (endpoint tidak stabil)
  - trending_searches()          → STABLE ✓ (hottrends/visualize/internal/data)

STRATEGI ENDPOINT (urutan prioritas):
  1. trending_searches(pn=country_name)  — paling stabil, tidak butuh auth/cookie
  2. realtime_trending_searches(pn=ISO)  — fallback jika tersedia
  3. today_searches(pn=ISO)              — fallback terakhir dengan parsing fix
  4. Empty list                          — dikembalikan jika semua gagal (non-fatal)

Mapping ISO 3166-1 alpha-2 → pytrends country name dibutuhkan untuk endpoint #1.
Endpoint #2 dan #3 menggunakan ISO code langsung.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Final

import pandas as pd
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
_SOURCE_NAME: Final[str] = "google_trends"
_MAX_RETRIES: Final[int] = 3
_BASE_BACKOFF_SECONDS: Final[float] = 5.0
_MAX_JITTER_SECONDS: Final[float] = 3.0
_MIN_POLITE_DELAY: Final[float] = 1.5
_MAX_POLITE_DELAY: Final[float] = 3.5

_USER_AGENTS: Final[list[str]] = [
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

# ---------------------------------------------------------------------------
# ISO 3166-1 alpha-2  →  pytrends country name mapping
#
# trending_searches() menggunakan endpoint hottrends/visualize/internal/data
# yang mengembalikan dict dengan key = snake_case country name (bukan ISO code).
# Mapping ini WAJIB untuk endpoint tersebut.
# ---------------------------------------------------------------------------
_ISO_TO_PYTRENDS_NAME: Final[dict[str, str]] = {
    "AR": "argentina",
    "AT": "austria",
    "AU": "australia",
    "BD": "bangladesh",
    "BE": "belgium",
    "BR": "brazil",
    "CA": "canada",
    "CH": "switzerland",
    "CL": "chile",
    "CO": "colombia",
    "CZ": "czech_republic",
    "DE": "germany",
    "DK": "denmark",
    "EG": "egypt",
    "ES": "spain",
    "FI": "finland",
    "FR": "france",
    "GB": "united_kingdom",
    "GH": "ghana",
    "GR": "greece",
    "HK": "hong_kong",
    "HU": "hungary",
    "ID": "indonesia",
    "IL": "israel",
    "IN": "india",
    "IT": "italy",
    "JP": "japan",
    "KE": "kenya",
    "KR": "south_korea",
    "MX": "mexico",
    "MY": "malaysia",
    "NG": "nigeria",
    "NL": "netherlands",
    "NO": "norway",
    "NZ": "new_zealand",
    "PE": "peru",
    "PH": "philippines",
    "PK": "pakistan",
    "PL": "poland",
    "PT": "portugal",
    "RO": "romania",
    "RU": "russia",
    "SA": "saudi_arabia",
    "SE": "sweden",
    "SG": "singapore",
    "TH": "thailand",
    "TR": "turkey",
    "TW": "taiwan",
    "UA": "ukraine",
    "US": "united_states",
    "VE": "venezuela",
    "VN": "vietnam",
    "ZA": "south_africa",
}


class GoogleTrendsAdapter(TrendProviderPort):
    """
    Mengambil trending topics dari Google Trends via pytrends.

    Urutan endpoint yang dicoba:
        1. trending_searches — hottrends internal, paling stabil
        2. realtime_trending_searches — realtime stories, fallback
        3. today_searches — daily API, fallback terakhir (parsing manual)

    Jika semua endpoint gagal karena non-fatal error (bukan 429),
    mengembalikan list kosong agar pipeline tetap berjalan.
    HTTP 429 (rate limit) setelah semua retry → raise RateLimitExceededError.
    """

    def __init__(
        self,
        hl: str = "en-US",
        tz: int = 360,
        retries: int = _MAX_RETRIES,
        backoff_factor: float = _BASE_BACKOFF_SECONDS,
    ) -> None:
        """
        Args:
            hl:             Host language (e.g. "en-US", "id-ID").
            tz:             UTC offset in minutes (420 = WIB/UTC+7).
            retries:        Max retry attempts saat HTTP 429.
            backoff_factor: Base delay (detik) untuk exponential backoff.
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
        Ambil trending topics untuk region yang diberikan.

        Mencoba tiga endpoint secara berurutan. Rate limit (429) di-retry
        dengan exponential backoff. Error non-fatal di-log dan lanjut ke
        endpoint berikutnya.

        Args:
            region: ISO 3166-1 alpha-2 code (e.g. "US", "ID").

        Returns:
            List RawTrendData, bisa kosong jika semua endpoint tidak tersedia.

        Raises:
            RateLimitExceededError: Setelah semua retry habis pada HTTP 429.
            DataExtractionError:    Pada error fatal yang tidak bisa di-recover.
        """
        region = region.upper().strip()
        logger.info("Fetching Google Trends — region='%s'", region)

        for attempt in range(1, self._retries + 1):
            try:
                client = self._build_client()
                return self._fetch_with_fallback(client, region)

            except AgentMarketIntelligenceError:
                # Jangan pernah telan domain exception sendiri.
                raise

            except ResponseError as exc:
                status = self._extract_status_code(exc)
                if status == 429:
                    if attempt == self._retries:
                        logger.error(
                            "Rate limit (429) habis setelah %d attempt — region='%s'.",
                            self._retries,
                            region,
                        )
                        raise RateLimitExceededError(source=_SOURCE_NAME) from exc
                    sleep_s = self._calc_backoff(attempt)
                    logger.warning(
                        "Rate limit (429) attempt %d/%d. Tunggu %.1fs…",
                        attempt,
                        self._retries,
                        sleep_s,
                    )
                    time.sleep(sleep_s)
                else:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"HTTP {status} dari Google Trends: {exc}",
                    ) from exc

            except Exception as exc:  # noqa: BLE001
                if attempt == self._retries:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"{type(exc).__name__}: {exc}",
                    ) from exc
                logger.warning(
                    "Attempt %d/%d gagal (%s: %s). Retry…",
                    attempt,
                    self._retries,
                    type(exc).__name__,
                    exc,
                )
                time.sleep(self._calc_backoff(attempt))

        # Unreachable — untuk type-checker.
        raise DataExtractionError(
            source=_SOURCE_NAME,
            reason=f"Semua {self._retries} attempt gagal.",
        )

    # ------------------------------------------------------------------
    # Orchestration: coba endpoint satu per satu
    # ------------------------------------------------------------------

    def _fetch_with_fallback(
        self, client: TrendReq, region: str
    ) -> list[RawTrendData]:
        """
        Coba ketiga endpoint secara berurutan.
        Endpoint gagal non-fatal → log warning → coba berikutnya.
        HTTP 429 → propagate langsung ke caller.
        """
        # ── Endpoint 1: trending_searches (PALING STABIL) ─────────────
        try:
            results = self._try_trending_searches(client, region)
            if results:
                logger.info(
                    "trending_searches: %d record(s) untuk region='%s'.",
                    len(results),
                    region,
                )
                return results
            logger.debug("trending_searches: kosong untuk region='%s'.", region)
        except ResponseError as exc:
            if self._extract_status_code(exc) == 429:
                raise
            logger.warning(
                "trending_searches gagal (HTTP %d) untuk region='%s'. Lanjut fallback.",
                self._extract_status_code(exc),
                region,
            )
        except KeyError:
            logger.warning(
                "Region '%s' tidak ditemukan di response trending_searches. "
                "Coba tambahkan mapping di _ISO_TO_PYTRENDS_NAME.",
                region,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trending_searches error (%s: %s) untuk region='%s'. Lanjut fallback.",
                type(exc).__name__,
                exc,
                region,
            )

        # ── Endpoint 2: realtime_trending_searches ────────────────────
        try:
            results = self._try_realtime_searches(client, region)
            if results:
                logger.info(
                    "realtime_trending_searches: %d record(s) untuk region='%s'.",
                    len(results),
                    region,
                )
                return results
            logger.debug(
                "realtime_trending_searches: kosong untuk region='%s'.", region
            )
        except ResponseError as exc:
            if self._extract_status_code(exc) == 429:
                raise
            logger.warning(
                "realtime_trending_searches gagal (HTTP %d) region='%s'. Lanjut fallback.",
                self._extract_status_code(exc),
                region,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "realtime_trending_searches error (%s) region='%s'. Lanjut fallback.",
                type(exc).__name__,
                region,
            )

        # ── Endpoint 3: today_searches (dengan manual parsing fix) ────
        try:
            results = self._try_today_searches_raw(region)
            if results:
                logger.info(
                    "today_searches (raw): %d record(s) untuk region='%s'.",
                    len(results),
                    region,
                )
                return results
            logger.debug("today_searches: kosong untuk region='%s'.", region)
        except ResponseError as exc:
            if self._extract_status_code(exc) == 429:
                raise
            logger.warning(
                "today_searches gagal (HTTP %d) region='%s'.",
                self._extract_status_code(exc),
                region,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "today_searches error (%s) region='%s'.",
                type(exc).__name__,
                region,
            )

        # ── Semua endpoint gagal: kembalikan list kosong ──────────────
        logger.error(
            "Semua 3 endpoint Google Trends gagal untuk region='%s'. "
            "Kembalikan list kosong. Periksa koneksi/VPN/cookie.",
            region,
        )
        return []

    # ------------------------------------------------------------------
    # Endpoint implementations
    # ------------------------------------------------------------------

    def _try_trending_searches(
        self, client: TrendReq, region: str
    ) -> list[RawTrendData]:
        """
        Endpoint: hottrends/visualize/internal/data (paling stabil).

        Mengembalikan dict {country_name: [keyword, ...]} untuk semua negara
        dalam satu request. Tidak membutuhkan auth/cookie.

        Membutuhkan mapping ISO → pytrends country name.
        Raise KeyError jika region tidak ada di _ISO_TO_PYTRENDS_NAME.
        """
        country_name = _ISO_TO_PYTRENDS_NAME.get(region)
        if not country_name:
            raise KeyError(
                f"'{region}' tidak ada di _ISO_TO_PYTRENDS_NAME. "
                f"Tambahkan mapping secara manual."
            )

        self._polite_sleep()
        df: pd.DataFrame = client.trending_searches(pn=country_name)

        if df is None or df.empty:
            return []

        keywords: list[str] = df[0].dropna().tolist()
        total = len(keywords)
        return [
            RawTrendData(
                keyword=kw.strip(),
                region=region,
                raw_value=max(1, round(100 * (1 - rank / max(total, 1)))),
                source=_SOURCE_NAME,
                metadata={
                    "rank": rank,
                    "total_results": total,
                    "endpoint": "trending_searches",
                    "country_name": country_name,
                },
            )
            for rank, kw in enumerate(keywords)
            if isinstance(kw, str) and kw.strip()
        ]

    def _try_realtime_searches(
        self, client: TrendReq, region: str
    ) -> list[RawTrendData]:
        """
        Endpoint: realtimetrends (ISO code langsung).

        Mengembalikan DataFrame dengan kolom 'title' dan 'entityNames'.
        """
        self._polite_sleep()
        df: pd.DataFrame = client.realtime_trending_searches(
            pn=region, cat="all", count=20
        )

        if df is None or df.empty:
            return []

        total = len(df)
        records: list[RawTrendData] = []
        for rank, row in enumerate(df.itertuples(index=False)):
            title: str = str(getattr(row, "title", "") or "").strip()
            if not title:
                continue
            records.append(
                RawTrendData(
                    keyword=title,
                    region=region,
                    raw_value=max(1, round(100 * (1 - rank / max(total, 1)))),
                    source=_SOURCE_NAME,
                    metadata={
                        "rank": rank,
                        "total_results": total,
                        "endpoint": "realtime_trending_searches",
                    },
                )
            )
        return records

    def _try_today_searches_raw(self, region: str) -> list[RawTrendData]:
        """
        Endpoint: dailytrends (manual request — bypass pytrends parsing bug).

        pytrends 4.9.2 today_searches() punya bug: .iloc[:, -1] mengambil
        kolom 'exploreLink' bukan 'query'. Kita hit API langsung dan parse
        'query' secara manual dari response JSON.
        """
        import json as _json

        import requests as _requests

        self._polite_sleep()

        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept-Language": self._hl.replace("-", "_"),
            "Referer": "https://trends.google.com/",
        }
        params = {
            "ns": 15,
            "geo": region,
            "tz": str(-self._tz),  # pytrends convention: negate UTC offset
            "hl": self._hl,
        }

        resp = _requests.get(
            "https://trends.google.com/trends/api/dailytrends",
            params=params,
            headers=headers,
            timeout=(10, 30),
        )

        if resp.status_code != 200:
            from pytrends.exceptions import ResponseError
            raise ResponseError.from_response(resp)

        # Response diawali dengan 5 karakter garbage: ")]}',\n"
        data = _json.loads(resp.text[5:])
        days: list[dict] = (
            data.get("default", {}).get("trendingSearchesDays", [])
        )

        if not days:
            return []

        searches: list[dict] = days[0].get("trendingSearches", [])
        queries: list[str] = [
            s.get("title", {}).get("query", "").strip()
            for s in searches
            if s.get("title", {}).get("query", "").strip()
        ]

        total = len(queries)
        return [
            RawTrendData(
                keyword=q,
                region=region,
                raw_value=max(1, round(100 * (1 - rank / max(total, 1)))),
                source=_SOURCE_NAME,
                metadata={
                    "rank": rank,
                    "total_results": total,
                    "endpoint": "today_searches_raw",
                },
            )
            for rank, q in enumerate(queries)
            if q
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_client(self) -> TrendReq:
        """Buat sesi TrendReq baru dengan random User-Agent."""
        return TrendReq(
            hl=self._hl,
            tz=self._tz,
            timeout=(10, 30),
            requests_args={"headers": {"User-Agent": random.choice(_USER_AGENTS)}},
        )

    def _polite_sleep(self) -> None:
        """Tidur sebentar sebelum request untuk hindari rate limit."""
        time.sleep(random.uniform(_MIN_POLITE_DELAY, _MAX_POLITE_DELAY))

    def _calc_backoff(self, attempt: int) -> float:
        """Exponential backoff dengan random jitter."""
        return round(
            self._backoff_factor * (2 ** (attempt - 1))
            + random.uniform(0.0, _MAX_JITTER_SECONDS),
            2,
        )

    @staticmethod
    def _extract_status_code(exc: ResponseError) -> int:
        """Ambil HTTP status code dari ResponseError dengan aman."""
        try:
            return int(exc.response.status_code)
        except (AttributeError, ValueError, TypeError):
            return 0