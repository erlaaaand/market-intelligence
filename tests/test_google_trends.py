# tests/test_google_trends.py

"""
Unit tests for GoogleTrendsAdapter.

All network calls are mocked — no real HTTP requests are made.

Adapter fallback chain under test:
  Tier 1 → _try_pytrends_trending   (pytrends TrendReq.trending_searches)
  Tier 2 → _try_trending_rss        (Google Trending RSS feed)
  Tier 3 → _try_interest_over_time  (/explore + /widgetdata/multiline)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.infrastructure.google_trends_api import (
    GoogleTrendsAdapter,
    _ISO_TO_PYTRENDS,   # correct constant name (no _NAME suffix)
    _score_from_rank,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(retries: int = 2, backoff: float = 0.01) -> GoogleTrendsAdapter:
    return GoogleTrendsAdapter(hl="en-US", tz=360, retries=retries, backoff_factor=backoff)


# ---------------------------------------------------------------------------
# ISO → country name mapping
# ---------------------------------------------------------------------------

def test_iso_mapping_contains_common_regions() -> None:
    for iso in ("US", "ID", "GB", "AU", "SG", "IN", "JP"):
        assert iso in _ISO_TO_PYTRENDS, f"{iso} missing from mapping"


def test_iso_mapping_values_are_snake_case() -> None:
    for iso, name in _ISO_TO_PYTRENDS.items():
        assert name == name.lower(), f"{iso} → '{name}' is not lowercase"
        assert " " not in name, f"{iso} → '{name}' contains a space"


def test_iso_mapping_covers_us_and_id() -> None:
    assert _ISO_TO_PYTRENDS["US"] == "united_states"
    assert _ISO_TO_PYTRENDS["ID"] == "indonesia"


# ---------------------------------------------------------------------------
# _score_from_rank helper
# ---------------------------------------------------------------------------

def test_score_from_rank_top_is_100() -> None:
    assert _score_from_rank(0, 10) == 100


def test_score_from_rank_single_item_is_100() -> None:
    assert _score_from_rank(0, 1) == 100


def test_score_from_rank_decreases_with_rank() -> None:
    scores = [_score_from_rank(r, 10) for r in range(10)]
    assert scores == sorted(scores, reverse=True)
    assert min(scores) >= 1


# ---------------------------------------------------------------------------
# Tier 1: pytrends.trending_searches (primary)
# Patch TrendReq at the module level where it is imported.
# ---------------------------------------------------------------------------

@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_tier1_happy_path(mock_sleep: MagicMock, MockTrendReq: MagicMock) -> None:
    """Tier 1 returns data → adapter returns it immediately."""
    adapter = _make_adapter()
    mock_df = pd.DataFrame({0: ["AI Boom", "Budget Phone", "Liga 1"]})

    instance = MockTrendReq.return_value
    instance.trending_searches.return_value = mock_df

    results = adapter.fetch_trends("ID")

    assert len(results) == 3
    # RawTrendData does NOT title-case — that only happens in TrendTopic
    assert results[0].keyword == "AI Boom"
    assert results[0].raw_value == 100
    assert results[0].region == "ID"
    assert results[0].source == "google_trends"
    assert results[0].metadata["endpoint"] == "pytrends_trending_searches"
    assert results[0].metadata["country_name"] == "indonesia"


@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_tier1_volume_decreases_with_rank(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    adapter = _make_adapter()
    keywords = [f"Topic {i}" for i in range(5)]
    mock_df = pd.DataFrame({0: keywords})

    MockTrendReq.return_value.trending_searches.return_value = mock_df
    results = adapter.fetch_trends("US")

    volumes = [r.raw_value for r in results]
    assert volumes == sorted(volumes, reverse=True)
    assert volumes[0] == 100
    assert all(v >= 1 for v in volumes)


@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_tier1_normalises_region_to_uppercase(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    adapter = _make_adapter()
    MockTrendReq.return_value.trending_searches.return_value = pd.DataFrame({0: ["X"]})

    results = adapter.fetch_trends("us")  # lowercase input

    assert results[0].region == "US"


@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_tier1_unknown_region_skips_to_tier2(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    """Region not in mapping → Tier 1 skipped → falls back to Tier 2."""
    adapter = _make_adapter()

    with patch.object(adapter, "_try_trending_rss", return_value=[]) as mock_rss:
        with patch.object(adapter, "_try_interest_over_time", return_value=[]):
            adapter.fetch_trends("XX")  # "XX" not in _ISO_TO_PYTRENDS

    # TrendReq should NOT be called for an unknown region
    MockTrendReq.assert_not_called()
    mock_rss.assert_called_once_with("XX")


# ---------------------------------------------------------------------------
# Tier 2: Google Trending RSS fallback
# ---------------------------------------------------------------------------

@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_tier2_used_when_tier1_returns_empty(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    """Tier 1 returns empty DataFrame → Tier 2 RSS is tried."""
    adapter = _make_adapter()
    MockTrendReq.return_value.trending_searches.return_value = pd.DataFrame({0: []})

    rss_records = [
        MagicMock(keyword="RSS Topic 1", raw_value=100, region="US",
                  source="google_trends", metadata={}),
    ]
    with patch.object(adapter, "_try_trending_rss", return_value=rss_records) as mock_rss:
        results = adapter.fetch_trends("US")

    mock_rss.assert_called_once_with("US")
    assert results == rss_records


@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_tier2_used_when_tier1_raises(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    """Tier 1 raises an exception → Tier 2 is tried next."""
    adapter = _make_adapter(retries=1)
    MockTrendReq.return_value.trending_searches.side_effect = Exception("network error")

    rss_records = [MagicMock()]
    with patch.object(adapter, "_try_trending_rss", return_value=rss_records):
        results = adapter.fetch_trends("US")

    assert results == rss_records


# ---------------------------------------------------------------------------
# Tier 3: interest_over_time fallback
# ---------------------------------------------------------------------------

@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_tier3_used_when_tier1_and_tier2_fail(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    """Both Tier 1 and Tier 2 return empty → Tier 3 is tried."""
    adapter = _make_adapter(retries=1)
    MockTrendReq.return_value.trending_searches.return_value = pd.DataFrame({0: []})

    iot_records = [MagicMock(keyword="IOT Topic", raw_value=55)]
    with patch.object(adapter, "_try_trending_rss", return_value=[]):
        with patch.object(adapter, "_try_interest_over_time", return_value=iot_records) as mock_iot:
            results = adapter.fetch_trends("US")

    mock_iot.assert_called_once()
    assert results == iot_records


@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_all_tiers_fail_returns_empty_list(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    """Pipeline must not crash when all tiers fail — return []."""
    adapter = _make_adapter(retries=1)
    MockTrendReq.return_value.trending_searches.side_effect = Exception("err")

    with patch.object(adapter, "_try_trending_rss", side_effect=Exception("err")):
        with patch.object(adapter, "_try_interest_over_time", side_effect=Exception("err")):
            results = adapter.fetch_trends("US")

    assert results == []


# ---------------------------------------------------------------------------
# Rate limit handling
# ---------------------------------------------------------------------------

@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_rate_limit_propagated_after_all_retries(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    """RateLimitExceededError must bubble up after all retries exhausted."""
    adapter = _make_adapter(retries=2)

    with patch.object(
        adapter, "_fetch_with_fallback", side_effect=RateLimitExceededError("google_trends")
    ):
        with pytest.raises(RateLimitExceededError):
            adapter.fetch_trends("US")


@patch("src.infrastructure.google_trends_api.TrendReq", autospec=True)
@patch("src.infrastructure.google_trends_api.time.sleep")
def test_data_extraction_error_not_retried(
    mock_sleep: MagicMock, MockTrendReq: MagicMock
) -> None:
    """DataExtractionError is a hard failure — must not be swallowed or retried."""
    adapter = _make_adapter(retries=3)

    with patch.object(
        adapter,
        "_fetch_with_fallback",
        side_effect=DataExtractionError("google_trends", "malformed response"),
    ) as mock_fetch:
        with pytest.raises(DataExtractionError):
            adapter.fetch_trends("US")

    # Should be called only once — DataExtractionError is not retried
    mock_fetch.assert_called_once()


# ---------------------------------------------------------------------------
# Backoff helper
# ---------------------------------------------------------------------------

def test_calc_backoff_increases_with_attempt() -> None:
    adapter = GoogleTrendsAdapter(backoff_factor=2.0)
    delays = [adapter._calc_backoff(i) for i in range(1, 4)]
    # Base: 2, 4, 8 — jitter always adds, so each must be >= base
    assert delays[0] >= 2.0
    assert delays[1] >= 4.0
    assert delays[2] >= 8.0


def test_calc_backoff_includes_jitter() -> None:
    """Two calls for the same attempt should almost never return identical values."""
    adapter = GoogleTrendsAdapter(backoff_factor=2.0)
    results = {adapter._calc_backoff(1) for _ in range(20)}
    # With random jitter, at least 2 different values in 20 trials
    assert len(results) > 1