from __future__ import annotations

import json
import logging
import random
import time
from typing import Final

import pandas as pd
import requests
from urllib3.util.retry import Retry as _UrllibRetry

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
# Compatibility patch — urllib3 v2.x renamed `method_whitelist` → `allowed_methods`
# pytrends 4.9.2 still passes `method_whitelist`, which raises TypeError on
# urllib3 >= 2.0.  We inject a shim class into pytrends.request so TrendReq
# never touches the old kwarg name.
# ---------------------------------------------------------------------------
class _CompatRetry(_UrllibRetry):
    def __init__(self, *args, method_whitelist=None, allowed_methods=None, **kwargs):
        if method_whitelist is not None and allowed_methods is None:
            allowed_methods = method_whitelist
        super().__init__(*args, allowed_methods=allowed_methods, **kwargs)

import pytrends.request as _pytrends_request
_pytrends_request.Retry = _CompatRetry
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

_ISO_TO_PYTRENDS_NAME: Final[dict[str, str]] = {
    "AR": "argentina", "AT": "austria", "AU": "australia", "BD": "bangladesh",
    "BE": "belgium", "BR": "brazil", "CA": "canada", "CH": "switzerland",
    "CL": "chile", "CO": "colombia", "CZ": "czech_republic", "DE": "germany",
    "DK": "denmark", "EG": "egypt", "ES": "spain", "FI": "finland",
    "FR": "france", "GB": "united_kingdom", "GH": "ghana", "GR": "greece",
    "HK": "hong_kong", "HU": "hungary", "ID": "indonesia", "IL": "israel",
    "IN": "india", "IT": "italy", "JP": "japan", "KE": "kenya",
    "KR": "south_korea", "MX": "mexico", "MY": "malaysia", "NG": "nigeria",
    "NL": "netherlands", "NO": "norway", "NZ": "new_zealand", "PE": "peru",
    "PH": "philippines", "PK": "pakistan", "PL": "poland", "PT": "portugal",
    "RO": "romania", "RU": "russia", "SA": "saudi_arabia", "SE": "sweden",
    "SG": "singapore", "TH": "thailand", "TR": "turkey", "TW": "taiwan",
    "UA": "ukraine", "US": "united_states", "VE": "venezuela", "VN": "vietnam",
    "ZA": "south_africa",
}

_YOUTUBE_SHORTS_SEED_KEYWORDS: Final[list[str]] = [
    "AI tools 2025", "viral life hacks", "money making online",
    "fitness motivation", "cooking recipes easy", "travel destinations",
    "productivity tips", "crypto news", "mental health tips",
    "smartphone review", "fashion trends", "gaming highlights",
    "home decor ideas", "relationship advice", "study motivation",
]


def _score_from_rank(rank: int, total: int) -> int:
    if total <= 1:
        return 100
    return max(1, round(100 * (1 - rank / total)))


def _strip_xssi(text: str) -> str:
    for prefix in (")]}',\n", ")]}'\n", ")]}',", ")]}'" ):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


class GoogleTrendsAdapter(TrendProviderPort):

    def __init__(
        self,
        hl: str = "en-US",
        tz: int = 360,
        retries: int = _MAX_RETRIES,
        backoff_factor: float = _BASE_BACKOFF_SECONDS,
    ) -> None:
        self._hl = hl
        self._tz = tz
        self._retries = retries
        self._backoff_factor = backoff_factor

    def fetch_trends(self, region: str) -> list[RawTrendData]:
        region = region.upper().strip()
        logger.info("Fetching Google Trends — region='%s'", region)

        for attempt in range(1, self._retries + 1):
            try:
                client = self._build_client()
                return self._fetch_with_fallback(client, region)

            except AgentMarketIntelligenceError:
                raise

            except ResponseError as exc:
                status = self._extract_status_code(exc)
                if status == 429:
                    if attempt == self._retries:
                        raise RateLimitExceededError(source=_SOURCE_NAME) from exc
                    sleep_s = self._calc_backoff(attempt)
                    logger.warning(
                        "Rate limit (429) attempt %d/%d. Tunggu %.1fs...",
                        attempt, self._retries, sleep_s,
                    )
                    time.sleep(sleep_s)
                else:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"HTTP {status} dari Google Trends: {exc}",
                    ) from exc

            except Exception as exc:
                if attempt == self._retries:
                    raise DataExtractionError(
                        source=_SOURCE_NAME,
                        reason=f"{type(exc).__name__}: {exc}",
                    ) from exc
                logger.warning(
                    "Attempt %d/%d gagal (%s: %s). Retry...",
                    attempt, self._retries, type(exc).__name__, exc,
                )
                time.sleep(self._calc_backoff(attempt))

        raise DataExtractionError(
            source=_SOURCE_NAME,
            reason=f"Semua {self._retries} attempt gagal.",
        )

    def _fetch_with_fallback(self, client: TrendReq, region: str) -> list[RawTrendData]:

        try:
            results = self._try_realtime_searches(client, region)
            if results:
                logger.info("realtime: %d records region='%s'.", len(results), region)
                return results
            logger.debug("realtime: kosong untuk region='%s'.", region)
        except ResponseError as exc:
            if self._extract_status_code(exc) == 429:
                raise
            logger.warning(
                "realtime gagal HTTP %d region='%s'. Lanjut fallback.",
                self._extract_status_code(exc), region,
            )
        except Exception as exc:
            logger.warning(
                "realtime error (%s: %s) region='%s'. Lanjut fallback.",
                type(exc).__name__, exc, region,
            )

        try:
            results = self._try_today_searches_pytrends(client, region)
            if results:
                logger.info("today_searches (pytrends): %d records region='%s'.", len(results), region)
                return results
            logger.debug("today_searches (pytrends): kosong untuk region='%s'.", region)
        except ResponseError as exc:
            if self._extract_status_code(exc) == 429:
                raise
            logger.warning(
                "today_searches (pytrends) gagal HTTP %d region='%s'. Lanjut fallback.",
                self._extract_status_code(exc), region,
            )
        except Exception as exc:
            logger.warning(
                "today_searches (pytrends) error (%s: %s) region='%s'. Lanjut fallback.",
                type(exc).__name__, exc, region,
            )

        try:
            results = self._try_today_searches_raw(client, region)
            if results:
                logger.info("today_searches (raw): %d records region='%s'.", len(results), region)
                return results
            logger.debug("today_searches (raw): kosong untuk region='%s'.", region)
        except ResponseError as exc:
            if self._extract_status_code(exc) == 429:
                raise
            logger.warning(
                "today_searches (raw) gagal HTTP %d region='%s'. Lanjut fallback.",
                self._extract_status_code(exc), region,
            )
        except Exception as exc:
            logger.warning(
                "today_searches (raw) error (%s: %s) region='%s'. Lanjut fallback.",
                type(exc).__name__, exc, region,
            )

        try:
            results = self._try_interest_over_time(client, region)
            if results:
                logger.info("interest_over_time: %d records region='%s'.", len(results), region)
                return results
            logger.debug("interest_over_time: kosong untuk region='%s'.", region)
        except ResponseError as exc:
            if self._extract_status_code(exc) == 429:
                raise
            logger.warning(
                "interest_over_time gagal HTTP %d region='%s'.",
                self._extract_status_code(exc), region,
            )
        except Exception as exc:
            logger.warning(
                "interest_over_time error (%s: %s) region='%s'.",
                type(exc).__name__, exc, region,
            )

        logger.error(
            "Semua endpoint gagal untuk region='%s'. Kembalikan list kosong. "
            "Periksa koneksi internet dan pastikan Google Trends dapat diakses.",
            region,
        )
        return []

    def _try_realtime_searches(self, client: TrendReq, region: str) -> list[RawTrendData]:
        self._polite_sleep()
        df: pd.DataFrame = client.realtime_trending_searches(pn=region, cat="all", count=20)
        if df is None or df.empty:
            return []
        total = len(df)
        records: list[RawTrendData] = []
        for rank, row in enumerate(df.itertuples(index=False)):
            title: str = str(getattr(row, "title", "") or "").strip()
            if not title:
                continue
            records.append(RawTrendData(
                keyword=title,
                region=region,
                raw_value=_score_from_rank(rank, total),
                source=_SOURCE_NAME,
                metadata={"rank": rank, "total_results": total, "endpoint": "realtime_trending_searches"},
            ))
        return records

    def _try_today_searches_pytrends(self, client: TrendReq, region: str) -> list[RawTrendData]:
        self._polite_sleep()
        df: pd.DataFrame = client.today_searches(pn=region)
        if df is None or df.empty:
            return []
        keywords: list[str] = df.dropna().tolist()
        if not keywords:
            return []
        total = len(keywords)
        return [
            RawTrendData(
                keyword=str(kw).strip(),
                region=region,
                raw_value=_score_from_rank(rank, total),
                source=_SOURCE_NAME,
                metadata={"rank": rank, "total_results": total, "endpoint": "today_searches_pytrends"},
            )
            for rank, kw in enumerate(keywords)
            if str(kw).strip()
        ]

    def _try_today_searches_raw(self, client: TrendReq, region: str) -> list[RawTrendData]:
        self._polite_sleep()
        nid_cookie: dict = getattr(client, "cookies", {}) or {}
        session = requests.Session()
        session.headers.update({
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": self._hl,
            "Referer": "https://trends.google.com/",
        })
        if nid_cookie:
            session.cookies.update(nid_cookie)

        for tz_val in (str(self._tz), "0", "-360"):
            try:
                resp = session.get(
                    "https://trends.google.com/trends/api/dailytrends",
                    params={"ns": 15, "geo": region, "tz": tz_val, "hl": self._hl},
                    timeout=(10, 30),
                )
                if resp.status_code == 429:
                    raise ResponseError(
                        "HTTP 429",
                        type("_R", (), {"status_code": 429, "text": resp.text})(),
                    )
                if resp.status_code == 200:
                    text = _strip_xssi(resp.text)
                    data = json.loads(text)
                    days = data.get("default", {}).get("trendingSearchesDays", [])
                    if not days:
                        continue
                    searches = days[0].get("trendingSearches", [])
                    queries = [
                        s.get("title", {}).get("query", "").strip()
                        for s in searches
                        if s.get("title", {}).get("query", "").strip()
                    ]
                    if queries:
                        total = len(queries)
                        return [
                            RawTrendData(
                                keyword=q,
                                region=region,
                                raw_value=_score_from_rank(rank, total),
                                source=_SOURCE_NAME,
                                metadata={
                                    "rank": rank,
                                    "total_results": total,
                                    "endpoint": "today_searches_raw",
                                    "tz_used": tz_val,
                                },
                            )
                            for rank, q in enumerate(queries)
                            if q
                        ]
            except ResponseError:
                raise
            except Exception:
                continue

        return []

    def _try_interest_over_time(self, client: TrendReq, region: str) -> list[RawTrendData]:
        keywords = random.sample(
            _YOUTUBE_SHORTS_SEED_KEYWORDS,
            min(5, len(_YOUTUBE_SHORTS_SEED_KEYWORDS)),
        )
        self._polite_sleep()
        client.build_payload(
            kw_list=keywords, cat=0, timeframe="now 7-d", geo=region, gprop=""
        )
        df: pd.DataFrame = client.interest_over_time()
        if df is None or df.empty:
            return []
        if "isPartial" in df.columns:
            df = df.drop(columns=["isPartial"])
        mean_scores: dict[str, float] = df.mean(axis=0).to_dict()
        sorted_kws = sorted(mean_scores.items(), key=lambda x: x[1], reverse=True)
        total = len(sorted_kws)
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
                },
            )
            for rank, (kw, score) in enumerate(sorted_kws)
            if round(score) > 0
        ]

    def _build_client(self) -> TrendReq:
        return TrendReq(
            hl=self._hl,
            tz=self._tz,
            timeout=(10, 30),
            retries=2,
            backoff_factor=1.0,
            requests_args={"headers": {"User-Agent": random.choice(_USER_AGENTS)}},
        )

    def _polite_sleep(self) -> None:
        time.sleep(random.uniform(_MIN_POLITE_DELAY, _MAX_POLITE_DELAY))

    def _calc_backoff(self, attempt: int) -> float:
        return round(
            self._backoff_factor * (2 ** (attempt - 1))
            + random.uniform(0.0, _MAX_JITTER_SECONDS),
            2,
        )

    @staticmethod
    def _extract_status_code(exc: ResponseError) -> int:
        try:
            return int(exc.response.status_code)
        except (AttributeError, ValueError, TypeError):
            return 0