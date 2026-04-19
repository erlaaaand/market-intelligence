# tests/test_google_trends.py

"""
Unit tests untuk GoogleTrendsAdapter.
Semua network call di-mock — tidak ada HTTP request sungguhan.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pytrends.exceptions import ResponseError

from src.core.exceptions import DataExtractionError, RateLimitExceededError
from src.infrastructure.google_trends_api import (
    GoogleTrendsAdapter,
    _ISO_TO_PYTRENDS_NAME,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(retries: int = 2, backoff: float = 0.01) -> GoogleTrendsAdapter:
    return GoogleTrendsAdapter(hl="en-US", tz=360, retries=retries, backoff_factor=backoff)


def _response_error(status: int) -> ResponseError:
    mock_resp = MagicMock()
    mock_resp.status_code = status
    return ResponseError(f"HTTP {status}", mock_resp)


# ---------------------------------------------------------------------------
# ISO → country name mapping
# ---------------------------------------------------------------------------

def test_iso_mapping_contains_common_regions() -> None:
    for iso in ("US", "ID", "GB", "AU", "SG", "IN", "JP"):
        assert iso in _ISO_TO_PYTRENDS_NAME, f"{iso} hilang dari mapping"


def test_iso_mapping_values_are_snake_case() -> None:
    for iso, name in _ISO_TO_PYTRENDS_NAME.items():
        assert name == name.lower(), f"{iso} → '{name}' bukan lowercase"
        assert " " not in name, f"{iso} → '{name}' mengandung spasi"


# ---------------------------------------------------------------------------
# Happy path — trending_searches (primary)
# ---------------------------------------------------------------------------

def test_fetch_trends_uses_trending_searches_as_primary() -> None:
    adapter = _make_adapter()
    mock_df = pd.DataFrame({"0": ["AI Boom", "Budget Phone", "Liga 1"]})
    mock_df.columns = [0]

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.return_value = mock_df
        mock_build.return_value = mock_client

        results = adapter.fetch_trends("ID")

    assert len(results) == 3
    assert results[0].keyword == "Ai Boom"  # title-cased oleh TrendTopic, tapi di sini raw
    # Karena kita test di level RawTrendData, keyword tidak di-title-case
    assert results[0].raw_value == 100
    assert results[0].region == "ID"
    assert results[0].source == "google_trends"
    assert results[0].metadata["endpoint"] == "trending_searches"
    assert results[0].metadata["country_name"] == "indonesia"


def test_fetch_trends_volume_decreases_with_rank() -> None:
    adapter = _make_adapter()
    keywords = [f"Topic {i}" for i in range(5)]
    mock_df = pd.DataFrame({0: keywords})

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.return_value = mock_df
        mock_build.return_value = mock_client

        results = adapter.fetch_trends("US")

    volumes = [r.raw_value for r in results]
    assert volumes == sorted(volumes, reverse=True), "Volume harus menurun sesuai rank"
    assert volumes[0] == 100
    assert volumes[-1] >= 1


def test_fetch_trends_normalises_region_to_uppercase() -> None:
    adapter = _make_adapter()
    mock_df = pd.DataFrame({0: ["Topic A"]})

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.return_value = mock_df
        mock_build.return_value = mock_client

        results = adapter.fetch_trends("us")  # lowercase input

    assert results[0].region == "US"


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------

def test_falls_back_to_realtime_when_trending_empty() -> None:
    adapter = _make_adapter()
    empty_df = pd.DataFrame({0: []})
    realtime_df = pd.DataFrame(
        {"title": ["Realtime Story"], "entityNames": [["Entity A"]]}
    )

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.return_value = empty_df
        mock_client.realtime_trending_searches.return_value = realtime_df
        mock_build.return_value = mock_client

        results = adapter.fetch_trends("US")

    assert len(results) == 1
    assert results[0].keyword == "Realtime Story"
    assert results[0].metadata["endpoint"] == "realtime_trending_searches"


def test_falls_back_to_today_raw_when_trending_and_realtime_fail() -> None:
    adapter = _make_adapter()

    mock_today_json = {
        "default": {
            "trendingSearchesDays": [
                {
                    "trendingSearches": [
                        {"title": {"query": "Daily Topic A", "exploreLink": ""}},
                        {"title": {"query": "Daily Topic B", "exploreLink": ""}},
                    ]
                }
            ]
        }
    }

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
        patch(
            "src.infrastructure.google_trends_api.requests",
            autospec=True,
        ) as mock_requests,
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.side_effect = ResponseError(
            "404", MagicMock(status_code=404)
        )
        mock_client.realtime_trending_searches.side_effect = ResponseError(
            "404", MagicMock(status_code=404)
        )
        mock_build.return_value = mock_client

        # Mock requests.get untuk today_searches_raw
        import json
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = ")]}',\n" + json.dumps(mock_today_json)
        mock_requests.get.return_value = mock_resp

        results = adapter.fetch_trends("US")

    assert len(results) == 2
    assert results[0].keyword == "Daily Topic A"
    assert results[0].metadata["endpoint"] == "today_searches_raw"


def test_returns_empty_list_when_all_endpoints_fail() -> None:
    """Pipeline tidak crash jika semua endpoint gagal — kembalikan []."""
    adapter = _make_adapter(retries=1)

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
        patch(
            "src.infrastructure.google_trends_api.requests",
            autospec=True,
        ) as mock_requests,
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.side_effect = Exception("Network error")
        mock_client.realtime_trending_searches.side_effect = Exception("Network error")
        mock_build.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_requests.get.return_value = mock_resp

        results = adapter.fetch_trends("US")

    assert results == []


# ---------------------------------------------------------------------------
# Rate limit & error handling
# ---------------------------------------------------------------------------

def test_raises_rate_limit_after_all_retries() -> None:
    adapter = _make_adapter(retries=2)

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
        patch("time.sleep"),
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.side_effect = _response_error(429)
        mock_build.return_value = mock_client

        with pytest.raises(RateLimitExceededError):
            adapter.fetch_trends("US")


def test_raises_data_extraction_on_fatal_http_error() -> None:
    adapter = _make_adapter(retries=1)

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
    ):
        mock_client = MagicMock()
        mock_client.trending_searches.side_effect = _response_error(500)
        mock_build.return_value = mock_client

        with pytest.raises(DataExtractionError, match="HTTP 500"):
            adapter.fetch_trends("US")


def test_unknown_region_falls_back_gracefully() -> None:
    """Region tidak ada di mapping → KeyError di trending_searches → coba fallback."""
    adapter = _make_adapter(retries=1)

    realtime_df = pd.DataFrame(
        {"title": ["Fallback Topic"], "entityNames": [[]]}
    )

    with (
        patch.object(adapter, "_build_client") as mock_build,
        patch.object(adapter, "_polite_sleep"),
    ):
        mock_client = MagicMock()
        mock_client.realtime_trending_searches.return_value = realtime_df
        mock_build.return_value = mock_client

        # "XX" tidak ada di mapping → KeyError → lanjut ke realtime
        results = adapter.fetch_trends("XX")

    assert len(results) == 1
    assert results[0].keyword == "Fallback Topic"


# ---------------------------------------------------------------------------
# Helper method tests
# ---------------------------------------------------------------------------

def test_calc_backoff_increases_with_attempt() -> None:
    adapter = GoogleTrendsAdapter(backoff_factor=2.0)
    delays = [adapter._calc_backoff(i) for i in range(1, 4)]
    # Base: 2, 4, 8 — dengan jitter selalu >= base
    assert delays[0] >= 2.0
    assert delays[1] >= 4.0
    assert delays[2] >= 8.0


def test_extract_status_code_safe_on_none_response() -> None:
    exc = ResponseError("error", None)
    assert GoogleTrendsAdapter._extract_status_code(exc) == 0


def test_iso_to_pytrends_name_covers_us_and_id() -> None:
    assert _ISO_TO_PYTRENDS_NAME["US"] == "united_states"
    assert _ISO_TO_PYTRENDS_NAME["ID"] == "indonesia"