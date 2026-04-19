from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from urllib3.util.retry import Retry as _UrllibRetry

import httpx

from src.core.entities import RawTrendData
from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.infrastructure.google_trends.constants import (
    SOURCE_NAME,
    TRENDING_RSS_URL,
    ISO_TO_PYTRENDS,
    score_from_rank,
    random_ua,
)

logger = logging.getLogger(__name__)


class _CompatRetry(_UrllibRetry):
    def __init__(self, *args, method_whitelist=None, allowed_methods=None, **kwargs):
        if method_whitelist is not None and allowed_methods is None:
            allowed_methods = method_whitelist
        super().__init__(*args, allowed_methods=allowed_methods, **kwargs)


# Patch pytrends to use our compatible Retry class
import pytrends.request as _pt_req
_pt_req.Retry = _CompatRetry

from pytrends.request import TrendReq  # noqa: E402  (must come after patch)


def fetch_tier1_pytrends(
    region: str,
    hl: str,
    tz: int,
) -> list[RawTrendData]:
    """Tier 1: pytrends trending_searches (most reliable when available)."""
    country_name = ISO_TO_PYTRENDS.get(region)
    if not country_name:
        logger.debug(
            "No pytrends country mapping for region='%s'. Skipping Tier 1.", region
        )
        return []

    pytrends = TrendReq(
        hl=hl,
        tz=tz,
        timeout=(10, 30),
        retries=2,
        backoff_factor=1.0,
        requests_args={"headers": {"User-Agent": random_ua()}},
    )

    import pandas as pd
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
            raw_value=score_from_rank(rank, total),
            source=SOURCE_NAME,
            metadata={
                "rank": rank,
                "total_results": total,
                "endpoint": "pytrends_trending_searches",
                "country_name": country_name,
            },
        )
        for rank, kw in enumerate(keywords)
    ]


def fetch_tier2_rss(
    region: str,
    hl: str,
    client: httpx.Client,
) -> list[RawTrendData]:
    """Tier 2: Google Trending RSS feed."""
    resp = client.get(
        TRENDING_RSS_URL,
        params={"geo": region, "hl": hl},
        timeout=20.0,
        headers={
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Referer": "https://trends.google.com/",
        },
    )

    if resp.status_code == 429:
        raise RateLimitExceededError(source=SOURCE_NAME)
    if resp.status_code >= 400:
        raise DataExtractionError(
            source=SOURCE_NAME,
            reason=f"Trending RSS HTTP {resp.status_code} for region='{region}'",
        )

    root = ET.fromstring(resp.text)
    titles: list[str] = []
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
            raw_value=score_from_rank(rank, total),
            source=SOURCE_NAME,
            metadata={
                "rank": rank,
                "total_results": total,
                "endpoint": "google_trending_rss",
            },
        )
        for rank, t in enumerate(titles)
    ]