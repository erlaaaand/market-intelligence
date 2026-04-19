from __future__ import annotations

import logging
import random
from typing import Final

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
from src.core.ports import LLMPort

logger = logging.getLogger(__name__)

# ── Static data pools ─────────────────────────────────────────────────────────

_MOCK_DRIVERS: Final[list[list[str]]] = [
    ["Social media virality", "Influencer amplification"],
    ["Breaking news cycle", "Public curiosity spike"],
    ["Seasonal demand pattern", "Consumer habit shift"],
    ["Viral challenge / meme spread", "Platform algorithm boost"],
    ["Economic uncertainty", "Cost-of-living search behaviour"],
    ["Technology product launch", "Media coverage surge"],
    ["Political or regulatory event", "Investor sentiment shift"],
    ["Sports or entertainment event", "Celebrity association"],
]

_MOCK_IMPACTS: Final[list[str]] = [
    "High short-term content opportunity; saturation likely within 7-14 days.",
    "Sustained audience interest expected; brand awareness campaigns recommended.",
    "Niche but loyal audience segment; long-form content will outperform shorts.",
    "Broad mainstream appeal; high competition from established publishers.",
    "Regional spike with limited global transfer; localise content strategy.",
    "Evergreen potential if paired with actionable how-to or educational framing.",
    "Controversy-adjacent; engagement high but reputational risk must be managed.",
]

_MOCK_ANOMALY_POOL: Final[list[dict[str, str]]] = [
    {
        "type": "Sudden Volume Spike",
        "description": (
            "Search volume increased >3x within a 24-hour window, "
            "suggesting a single triggering event."
        ),
    },
    {
        "type": "Geographic Concentration",
        "description": (
            "Interest is disproportionately concentrated in one "
            "sub-region rather than distributed nationally."
        ),
    },
    {
        "type": "Keyword Co-occurrence Anomaly",
        "description": (
            "Topic frequently co-occurs with semantically unrelated "
            "keywords, indicating possible misspelling or double meaning."
        ),
    },
    {
        "type": "Cyclical Pattern Deviation",
        "description": (
            "Trend appears outside its typical seasonal window, "
            "indicating exogenous demand rather than habitual search."
        ),
    },
]


# ── Adapter ───────────────────────────────────────────────────────────────────

class MockLLMAdapter(LLMPort):
    def __init__(self, inject_anomaly_probability: float = 0.3) -> None:
        if not 0.0 <= inject_anomaly_probability <= 1.0:
            raise ValueError("inject_anomaly_probability must be in [0, 1]")
        self._anomaly_prob = inject_anomaly_probability

    def analyze_trends(
        self,
        raw_data: list[RawTrendData],
        region: str,
        analysis_date: str,
    ) -> MarketAnalysisReport:
        logger.info(
            "MockLLMAdapter.analyze_trends  region='%s'  records=%d",
            region,
            len(raw_data),
        )
        trends: list[TrendTopic] = [
            self._build_mock_trend(record, i, region, analysis_date)
            for i, record in enumerate(raw_data)
        ]
        return MarketAnalysisReport(
            metadata=ReportMetadata(region=region, date=analysis_date),
            market_trends=trends,
        )

    def _build_mock_trend(
        self,
        record: RawTrendData,
        index: int,
        region: str,
        analysis_date: str,
    ) -> TrendTopic:
        v = record.raw_value
        momentum = min(100.0, round(v * 0.9 + (index % 5) * 2.0, 2))
        volatility = min(100.0, round(100.0 - v * 0.6 + (index % 3) * 5.0, 2))

        drivers = _MOCK_DRIVERS[index % len(_MOCK_DRIVERS)]
        impact = _MOCK_IMPACTS[index % len(_MOCK_IMPACTS)]

        anomalies: list[Anomaly] = []
        rng = random.Random(f"{region}:{record.keyword}:{analysis_date}")
        if rng.random() < self._anomaly_prob:
            anomaly_dict = _MOCK_ANOMALY_POOL[index % len(_MOCK_ANOMALY_POOL)]
            anomalies.append(Anomaly(**anomaly_dict))

        return TrendTopic(
            trend_id=TrendTopic.make_trend_id(region, record.keyword, analysis_date),
            topic=record.keyword,
            metrics=TrendMetrics(
                momentum_score=momentum,
                volatility_index=volatility,
            ),
            analysis=TrendAnalysisDetail(
                lifecycle_stage=_lifecycle_from_value(v),
                key_drivers=list(drivers),
                potential_impact=impact,
            ),
            anomalies_detected=anomalies,
        )


def _lifecycle_from_value(raw_value: int) -> LifecycleStage:
    if raw_value >= 80:
        return LifecycleStage.PEAK
    if raw_value >= 60:
        return LifecycleStage.TRENDING
    if raw_value >= 40:
        return LifecycleStage.EMERGING
    if raw_value >= 20:
        return LifecycleStage.STAGNANT
    return LifecycleStage.DECLINING