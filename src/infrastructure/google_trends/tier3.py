from __future__ import annotations

import logging
import random

import httpx

from src.core.entities import RawTrendData
from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.infrastructure.google_trends.constants import (
    SOURCE_NAME,
    EXPLORE_URL,
    MULTILINE_URL,
    SEED_KEYWORDS,
    json_compact,
    json_loads_xssi,
)

logger = logging.getLogger(__name__)


def fetch_tier3_interest_over_time(
    region: str,
    hl: str,
    tz: int,
    prior_keywords: list[str],
    client: httpx.Client,
    polite_sleep_fn,
) -> list[RawTrendData]:
    """Tier 3: interest_over_time via undocumented Explore + Multiline APIs."""
    keywords = prior_keywords[:5] if prior_keywords else random.sample(
        SEED_KEYWORDS, min(5, len(SEED_KEYWORDS))
    )

    explore_req_obj = {
        "comparisonItem": [
            {"keyword": kw, "geo": region, "time": "now 7-d"}
            for kw in keywords
        ],
        "category": 0,
        "property": "",
    }

    polite_sleep_fn()
    resp1 = client.get(
        EXPLORE_URL,
        params={
            "hl": hl,
            "tz": str(tz),
            "req": json_compact(explore_req_obj),
        },
        timeout=20.0,
    )
    if resp1.status_code == 429:
        raise RateLimitExceededError(source=SOURCE_NAME)
    if resp1.status_code >= 400:
        raise DataExtractionError(
            source=SOURCE_NAME,
            reason=f"explore HTTP {resp1.status_code}",
        )

    explore_data = json_loads_xssi(resp1.text)
    widgets: list[dict[str, object]] = explore_data.get("widgets", [])
    iot_widget = next(
        (w for w in widgets if w.get("id") == "TIMESERIES"), None
    )
    if not iot_widget:
        return []

    token = str(iot_widget.get("token", ""))
    req_payload: dict[str, object] = iot_widget.get("request", {})

    polite_sleep_fn()
    resp2 = client.get(
        MULTILINE_URL,
        params={
            "hl": hl,
            "tz": str(tz),
            "req": json_compact(req_payload),
            "token": token,
            "csv": "1",
        },
        timeout=20.0,
    )
    if resp2.status_code == 429:
        raise RateLimitExceededError(source=SOURCE_NAME)
    if resp2.status_code >= 400:
        raise DataExtractionError(
            source=SOURCE_NAME,
            reason=f"multiline HTTP {resp2.status_code}",
        )

    ts_data = json_loads_xssi(resp2.text)
    timeline: list[dict[str, object]] = (
        ts_data.get("default", {}).get("timelineData", [])
    )

    sums: dict[str, float] = {kw: 0.0 for kw in keywords}
    counts: dict[str, int] = {kw: 0 for kw in keywords}
    for point in timeline:
        values: list[int] = point.get("value", [])
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
    keywords_source = "prior_trending" if prior_keywords else "seed_list"

    return [
        RawTrendData(
            keyword=kw,
            region=region,
            raw_value=max(1, min(100, round(score))),
            source=SOURCE_NAME,
            metadata={
                "rank": rank,
                "total_results": total,
                "mean_score_7d": round(score, 2),
                "endpoint": "interest_over_time",
                "keywords_source": keywords_source,
            },
        )
        for rank, (kw, score) in enumerate(scored)
        if round(score) > 0
    ]