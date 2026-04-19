# tests/test_google_trends.py

"""
Unit tests for GoogleTrendsAdapter.

All network calls are mocked — no real HTTP requests are made.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.infrastructure.google_trends_api import GoogleTrendsAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def adapter() -> GoogleTrendsAdapter:
    return GoogleTrendsAdapter(hl="en-US", tz=360, retries=2, backoff_factor=0.01)


# ---------------------------------------------------------------------------
# today_searches happy path
# ---------------------------------------------------------------------------

def test_fetch_trends_returns_records_from_today_searches(
    adapter: GoogleTrendsAdapter,
) -> None:
    titles = pd.Series(["AI Boom", "Budget Phone", "Travel Tips"])

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch("time.sleep"),
    ):
        mock_client = MagicMock()
        mock_client.today_searches.return_value = titles
        mock_build.return_value = mock_client

        results = adapter.fetch_trends("US")

    assert len(results) == 3
    assert results[0].keyword == "AI Boom"
    assert results[0].raw_value == 100       # top-ranked
    assert results[0].region == "US"
    assert results[0].source == "google_trends"
    assert results[-1].raw_value >= 1        # minimum synthetic volume


def test_fetch_trends_uses_realtime_fallback_when_today_empty(
    adapter: GoogleTrendsAdapter,
) -> None:
    realtime_df = pd.DataFrame(
        {"title": ["Realtime Topic A", "Realtime Topic B"], "entityNames": [[], []]}
    )

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch("time.sleep"),
    ):
        mock_client = MagicMock()
        mock_client.today_searches.return_value = pd.Series([], dtype=str)
        mock_client.realtime_trending_searches.return_value = realtime_df
        mock_build.return_value = mock_client

        results = adapter.fetch_trends("US")

    assert len(results) == 2
    assert results[0].keyword == "Realtime Topic A"
    assert results[0].metadata["endpoint"] == "realtime_trending_searches"


def test_fetch_trends_normalises_region_to_uppercase(
    adapter: GoogleTrendsAdapter,
) -> None:
    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch("time.sleep"),
    ):
        mock_client = MagicMock()
        mock_client.today_searches.return_value = pd.Series(["Topic"])
        mock_build.return_value = mock_client

        results = adapter.fetch_trends("id")   # lowercase

    assert results[0].region == "ID"


# ---------------------------------------------------------------------------
# Rate-limit handling
# ---------------------------------------------------------------------------

def test_fetch_trends_raises_rate_limit_after_all_retries(
    adapter: GoogleTrendsAdapter,
) -> None:
    from pytrends.exceptions import ResponseError

    fake_response = MagicMock()
    fake_response.status_code = 429

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch("time.sleep"),
    ):
        mock_client = MagicMock()
        mock_client.today_searches.side_effect = ResponseError(
            "429", fake_response
        )
        mock_build.return_value = mock_client

        with pytest.raises(RateLimitExceededError):
            adapter.fetch_trends("US")


def test_fetch_trends_raises_data_extraction_on_non_429(
    adapter: GoogleTrendsAdapter,
) -> None:
    from pytrends.exceptions import ResponseError

    fake_response = MagicMock()
    fake_response.status_code = 500

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch("time.sleep"),
    ):
        mock_client = MagicMock()
        mock_client.today_searches.side_effect = ResponseError(
            "500 internal error", fake_response
        )
        mock_build.return_value = mock_client

        with pytest.raises(DataExtractionError, match="HTTP 500"):
            adapter.fetch_trends("US")


def test_fetch_trends_raises_data_extraction_on_unexpected_exception(
    adapter: GoogleTrendsAdapter,
) -> None:
    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch("time.sleep"),
    ):
        mock_client = MagicMock()
        mock_client.today_searches.side_effect = ConnectionError("Network unreachable")
        mock_build.return_value = mock_client

        with pytest.raises(DataExtractionError, match="ConnectionError"):
            adapter.fetch_trends("US")


# ---------------------------------------------------------------------------
# Backoff calculation
# ---------------------------------------------------------------------------

def test_backoff_with_jitter_increases_with_attempt() -> None:
    adapter_local = GoogleTrendsAdapter(backoff_factor=2.0)
    delay_1 = adapter_local._backoff_with_jitter(1)
    delay_2 = adapter_local._backoff_with_jitter(2)
    delay_3 = adapter_local._backoff_with_jitter(3)
    # Base (without jitter): 2, 4, 8 — with jitter always >= base
    assert delay_1 >= 2.0
    assert delay_2 >= 4.0
    assert delay_3 >= 8.0


def test_extract_status_code_returns_zero_on_bad_response() -> None:
    from pytrends.exceptions import ResponseError

    exc = ResponseError("error", None)
    result = GoogleTrendsAdapter._extract_status_code(exc)
    assert result == 0
    