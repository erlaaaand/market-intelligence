# tests/test_trend_analyzer.py

"""
Unit tests for TrendAnalyzerUseCase.

Uses mocked ports — zero network calls, zero filesystem side-effects.

Key behaviour under test:
  execute()
    ├── calls trend_provider.fetch_trends(region)
    ├── calls storage.save_raw(payload, filename)         ← always called
    ├── calls storage.save_processed(topics, filename)    ← ONLY when topics non-empty
    └── returns list[TrendTopic] sorted by volume desc, top_n entries
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import RawTrendData, TrendTopic
from src.core.exceptions import DataExtractionError, StorageError
from src.core.ports import StoragePort, TrendProviderPort


# ---------------------------------------------------------------------------
# Fixtures & helpers
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
def use_case(
    mock_provider: MagicMock, mock_storage: MagicMock
) -> TrendAnalyzerUseCase:
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


def test_constructor_rejects_growth_threshold_above_100(
    mock_provider: MagicMock, mock_storage: MagicMock
) -> None:
    with pytest.raises(ValueError, match="growth_threshold"):
        TrendAnalyzerUseCase(mock_provider, mock_storage, growth_threshold=101)


def test_constructor_rejects_growth_threshold_below_0(
    mock_provider: MagicMock, mock_storage: MagicMock
) -> None:
    with pytest.raises(ValueError, match="growth_threshold"):
        TrendAnalyzerUseCase(mock_provider, mock_storage, growth_threshold=-1)


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
        _make_raw("Keyword F", 10),  # excluded — beyond top_n=5
    ]
    mock_provider.fetch_trends.return_value = raw

    results = use_case.execute("US")

    assert len(results) == 5
    assert results[0].search_volume == 90
    assert results[1].search_volume == 80
    assert results[4].search_volume == 30
    assert all(t.topic_name != "Keyword F" for t in results)


def test_execute_calls_save_raw_and_save_processed(
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
    mock_provider.fetch_trends.return_value = [_make_raw("Test", 50, region="ID")]

    results = use_case.execute("id")  # lowercase input

    mock_provider.fetch_trends.assert_called_once_with("ID")
    assert results[0].target_country == "ID"


def test_execute_is_growing_flag_at_threshold_boundary(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    """Default growth_threshold=60: exactly at threshold → growing; one below → stable."""
    raw = [
        _make_raw("Exactly At Threshold", 60),
        _make_raw("Just Below Threshold", 59),
        _make_raw("Well Above", 100),
    ]
    mock_provider.fetch_trends.return_value = raw

    results = {t.topic_name: t for t in use_case.execute("US")}

    assert results["Exactly At Threshold"].is_growing is True
    assert results["Just Below Threshold"].is_growing is False
    assert results["Well Above"].is_growing is True


def test_execute_suggested_angle_three_tiers(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    """Volume ≥80 → Breaking; ≥60 → Emerging; <60 → Deep-dive."""
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


def test_execute_topic_names_are_title_cased(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    """TrendTopic.topic_name must be title-cased (validator on entity)."""
    mock_provider.fetch_trends.return_value = [_make_raw("artificial intelligence", 75)]

    results = use_case.execute("US")

    assert results[0].topic_name == "Artificial Intelligence"


def test_execute_custom_top_n(
    mock_provider: MagicMock, mock_storage: MagicMock
) -> None:
    use_case = TrendAnalyzerUseCase(mock_provider, mock_storage, top_n=3)
    raw = [_make_raw(f"Keyword {i}", 100 - i * 10) for i in range(6)]
    mock_provider.fetch_trends.return_value = raw

    results = use_case.execute("US")

    assert len(results) == 3


# ---------------------------------------------------------------------------
# Empty data — critical: save_processed must NOT be called
# ---------------------------------------------------------------------------

def test_execute_returns_empty_list_when_no_raw_data(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    """
    When fetch returns no data:
      - execute() returns []
      - save_raw IS still called (records an empty payload for auditability)
      - save_processed is NOT called (nothing to persist)
    """
    mock_provider.fetch_trends.return_value = []

    results = use_case.execute("US")

    assert results == []
    mock_storage.save_raw.assert_called_once()
    # BUG WAS HERE: previous test incorrectly asserted save_processed was called.
    # The code has `if processed: ... save_processed(...)` — so it is skipped.
    mock_storage.save_processed.assert_not_called()


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

def test_execute_propagates_data_extraction_error(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    """If extraction fails, storage must never be touched."""
    mock_provider.fetch_trends.side_effect = DataExtractionError(
        source="google_trends", reason="timeout"
    )

    with pytest.raises(DataExtractionError):
        use_case.execute("US")

    mock_storage.save_raw.assert_not_called()
    mock_storage.save_processed.assert_not_called()


def test_execute_propagates_storage_error_on_save_raw(
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


def test_execute_propagates_storage_error_on_save_processed(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("Topic", 75)]
    mock_storage.save_processed.side_effect = StorageError(
        path="/data/processed", reason="permission denied"
    )

    with pytest.raises(StorageError):
        use_case.execute("US")