# tests/test_trend_analyzer.py

"""
Unit tests for TrendAnalyzerUseCase.

Uses mocked ports — zero network calls, zero file-system side-effects.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import RawTrendData, TrendTopic
from src.core.exceptions import DataExtractionError, StorageError
from src.core.ports import StoragePort, TrendProviderPort


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_raw(keyword: str, volume: int, region: str = "US") -> RawTrendData:
    return RawTrendData(
        keyword=keyword,
        region=region,
        raw_value=volume,
        source="test_source",
    )


@pytest.fixture()
def mock_provider() -> MagicMock:
    return MagicMock(spec=TrendProviderPort)


@pytest.fixture()
def mock_storage() -> MagicMock:
    return MagicMock(spec=StoragePort)


@pytest.fixture()
def use_case(mock_provider: MagicMock, mock_storage: MagicMock) -> TrendAnalyzerUseCase:
    return TrendAnalyzerUseCase(
        trend_provider=mock_provider,
        storage=mock_storage,
    )


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------

def test_constructor_rejects_invalid_top_n(
    mock_provider: MagicMock, mock_storage: MagicMock
) -> None:
    with pytest.raises(ValueError, match="top_n"):
        TrendAnalyzerUseCase(mock_provider, mock_storage, top_n=0)


def test_constructor_rejects_invalid_growth_threshold(
    mock_provider: MagicMock, mock_storage: MagicMock
) -> None:
    with pytest.raises(ValueError, match="growth_threshold"):
        TrendAnalyzerUseCase(mock_provider, mock_storage, growth_threshold=101)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_execute_returns_top_5_sorted_by_volume(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    raw = [
        _make_raw("Keyword A", 30),
        _make_raw("Keyword B", 90),
        _make_raw("Keyword C", 50),
        _make_raw("Keyword D", 80),
        _make_raw("Keyword E", 70),
        _make_raw("Keyword F", 10),  # should be excluded (top_n=5)
    ]
    mock_provider.fetch_trends.return_value = raw

    results = use_case.execute("US")

    assert len(results) == 5
    assert results[0].search_volume == 90
    assert results[1].search_volume == 80
    assert results[4].search_volume == 30
    # Keyword F (vol=10) must not appear
    assert all(t.topic_name != "Keyword F" for t in results)


def test_execute_calls_storage_twice(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("AI Tools", 85)]

    use_case.execute("US")

    mock_storage.save_raw.assert_called_once()
    mock_storage.save_processed.assert_called_once()


def test_execute_region_normalised_to_uppercase(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("Test Keyword", 50, region="ID")]

    results = use_case.execute("id")   # lowercase input

    mock_provider.fetch_trends.assert_called_once_with("ID")
    assert results[0].target_country == "ID"


def test_execute_is_growing_flag(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    raw = [
        _make_raw("Growing Topic", 60),   # exactly at threshold → growing
        _make_raw("Stable Topic", 59),    # just below threshold → stable
        _make_raw("Hot Topic", 100),      # well above → growing
    ]
    mock_provider.fetch_trends.return_value = raw

    results = {t.topic_name: t for t in use_case.execute("US")}

    assert results["Growing Topic"].is_growing is True
    assert results["Stable Topic"].is_growing is False
    assert results["Hot Topic"].is_growing is True


def test_execute_suggested_angle_tiers(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    raw = [
        _make_raw("Big Story", 80),
        _make_raw("Mid Story", 70),
        _make_raw("Niche Story", 40),
    ]
    mock_provider.fetch_trends.return_value = raw

    results = {t.topic_name: t for t in use_case.execute("US")}

    assert "Breaking" in results["Big Story"].suggested_angle
    assert "Emerging" in results["Mid Story"].suggested_angle
    assert "Deep-dive" in results["Niche Story"].suggested_angle


def test_execute_returns_empty_list_when_no_raw_data(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = []

    results = use_case.execute("US")

    assert results == []
    # save_raw still called (with empty payload); save_processed called with []
    mock_storage.save_raw.assert_called_once()
    mock_storage.save_processed.assert_called_once_with([], pytest.approx([], abs=0))


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

def test_execute_propagates_data_extraction_error(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    mock_provider.fetch_trends.side_effect = DataExtractionError(
        source="google_trends", reason="timeout"
    )

    with pytest.raises(DataExtractionError):
        use_case.execute("US")

    # Storage must NOT be touched if extraction fails
    mock_storage.save_raw.assert_not_called()
    mock_storage.save_processed.assert_not_called()


def test_execute_propagates_storage_error(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("Topic", 55)]
    mock_storage.save_raw.side_effect = StorageError(
        path="/data/raw", reason="disk full"
    )

    with pytest.raises(StorageError):
        use_case.execute("US")
        