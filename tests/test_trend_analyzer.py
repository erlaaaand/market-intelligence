from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import (
    Anomaly,
    LifecycleStage,
    MarketAnalysisReport,
    RawTrendData,
    ReportMetadata,
    TrendAnalysisDetail,
    TrendMetrics,
    TrendTopic,
)
from src.core.exceptions import DataExtractionError, LLMAnalysisError, StorageError
from src.core.ports import LLMPort, StoragePort, TrendProviderPort


def _make_raw(keyword: str, volume: int, region: str = "US") -> RawTrendData:
    return RawTrendData(
        keyword=keyword,
        region=region,
        raw_value=volume,
        source="test_source",
    )


def _make_trend_topic(topic: str, momentum: float = 75.0) -> TrendTopic:
    return TrendTopic(
        trend_id=TrendTopic.make_trend_id("US", topic, "2026-01-01"),
        topic=topic,
        metrics=TrendMetrics(momentum_score=momentum, volatility_index=30.0),
        analysis=TrendAnalysisDetail(
            lifecycle_stage=LifecycleStage.TRENDING,
            key_drivers=["Test driver"],
            potential_impact="Test impact",
        ),
        anomalies_detected=[],
    )


def _make_report(region: str, topics: list[TrendTopic]) -> MarketAnalysisReport:
    return MarketAnalysisReport(
        metadata=ReportMetadata(region=region, date="2026-01-01"),
        market_trends=topics,
    )


@pytest.fixture()
def mock_provider() -> MagicMock:
    return MagicMock(spec=TrendProviderPort)


@pytest.fixture()
def mock_storage() -> MagicMock:
    return MagicMock(spec=StoragePort)


@pytest.fixture()
def mock_llm() -> MagicMock:
    return MagicMock(spec=LLMPort)


@pytest.fixture()
def use_case(
    mock_provider: MagicMock, mock_storage: MagicMock, mock_llm: MagicMock
) -> TrendAnalyzerUseCase:
    return TrendAnalyzerUseCase(
        trend_provider=mock_provider,
        storage=mock_storage,
        llm=mock_llm,
        top_n=5,
    )


def test_constructor_rejects_invalid_top_n(
    mock_provider: MagicMock, mock_storage: MagicMock, mock_llm: MagicMock
) -> None:
    with pytest.raises(ValueError, match="top_n"):
        TrendAnalyzerUseCase(mock_provider, mock_storage, mock_llm, top_n=0)


def test_execute_returns_market_analysis_report(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    raw = [_make_raw("Keyword A", 90), _make_raw("Keyword B", 50)]
    mock_provider.fetch_trends.return_value = raw
    expected_report = _make_report("US", [_make_trend_topic("Keyword A")])
    mock_llm.analyze_trends.return_value = expected_report

    result = use_case.execute("US")

    assert isinstance(result, MarketAnalysisReport)
    assert result is expected_report


def test_execute_calls_save_raw_always(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("AI Tools", 85)]
    mock_llm.analyze_trends.return_value = _make_report(
        "US", [_make_trend_topic("AI Tools")]
    )

    use_case.execute("US")

    mock_storage.save_raw.assert_called_once()


def test_execute_calls_save_processed_when_trends_exist(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("AI Tools", 85)]
    mock_llm.analyze_trends.return_value = _make_report(
        "US", [_make_trend_topic("AI Tools")]
    )

    use_case.execute("US")

    mock_storage.save_processed.assert_called_once()


def test_execute_region_normalised_to_uppercase(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("Test", 50, region="ID")]
    mock_llm.analyze_trends.return_value = _make_report(
        "ID", [_make_trend_topic("Test")]
    )

    use_case.execute("id")

    mock_provider.fetch_trends.assert_called_once_with("ID")


def test_execute_returns_empty_report_when_no_raw_data(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = []

    result = use_case.execute("US")

    assert isinstance(result, MarketAnalysisReport)
    assert result.market_trends == []
    mock_storage.save_raw.assert_called_once()
    mock_llm.analyze_trends.assert_not_called()
    mock_storage.save_processed.assert_not_called()


def test_execute_save_processed_not_called_when_no_trends(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("Topic", 50)]
    mock_llm.analyze_trends.return_value = _make_report("US", [])

    use_case.execute("US")

    mock_storage.save_processed.assert_not_called()


def test_execute_forwards_top_n_records_to_llm(
    mock_provider: MagicMock, mock_storage: MagicMock, mock_llm: MagicMock
) -> None:
    uc = TrendAnalyzerUseCase(mock_provider, mock_storage, mock_llm, top_n=3)
    raw = [_make_raw(f"Keyword {i}", 100 - i * 10) for i in range(6)]
    mock_provider.fetch_trends.return_value = raw
    mock_llm.analyze_trends.return_value = _make_report("US", [])

    uc.execute("US")

    call_args = mock_llm.analyze_trends.call_args
    forwarded = call_args.kwargs.get("raw_data") or call_args.args[0]
    assert len(forwarded) == 3


def test_execute_forwards_top_n_by_raw_value_descending(
    mock_provider: MagicMock, mock_storage: MagicMock, mock_llm: MagicMock
) -> None:
    uc = TrendAnalyzerUseCase(mock_provider, mock_storage, mock_llm, top_n=2)
    raw = [
        _make_raw("Low", 10),
        _make_raw("High", 90),
        _make_raw("Mid", 50),
    ]
    mock_provider.fetch_trends.return_value = raw
    mock_llm.analyze_trends.return_value = _make_report("US", [])

    uc.execute("US")

    call_args = mock_llm.analyze_trends.call_args
    forwarded = call_args.kwargs.get("raw_data") or call_args.args[0]
    keywords = [r.keyword for r in forwarded]
    assert "High" in keywords
    assert "Mid" in keywords
    assert "Low" not in keywords


def test_execute_propagates_data_extraction_error(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.side_effect = DataExtractionError(
        source="google_trends", reason="timeout"
    )

    with pytest.raises(DataExtractionError):
        use_case.execute("US")

    mock_storage.save_raw.assert_not_called()
    mock_storage.save_processed.assert_not_called()


def test_execute_propagates_llm_analysis_error(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("Topic", 75)]
    mock_llm.analyze_trends.side_effect = LLMAnalysisError(
        model="qwen3:30b", reason="model not found"
    )

    with pytest.raises(LLMAnalysisError):
        use_case.execute("US")

    mock_storage.save_raw.assert_called_once()
    mock_storage.save_processed.assert_not_called()


def test_execute_propagates_storage_error_on_save_raw(
    use_case: TrendAnalyzerUseCase,
    mock_provider: MagicMock,
    mock_storage: MagicMock,
    mock_llm: MagicMock,
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
    mock_llm: MagicMock,
) -> None:
    mock_provider.fetch_trends.return_value = [_make_raw("Topic", 75)]
    mock_llm.analyze_trends.return_value = _make_report(
        "US", [_make_trend_topic("Topic")]
    )
    mock_storage.save_processed.side_effect = StorageError(
        path="/data/processed", reason="permission denied"
    )

    with pytest.raises(StorageError):
        use_case.execute("US")