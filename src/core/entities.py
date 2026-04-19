from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator


class LifecycleStage(str, Enum):
    EMERGING = "Emerging"
    TRENDING = "Trending"
    PEAK = "Peak"
    STAGNANT = "Stagnant"
    DECLINING = "Declining"


class RawTrendData(BaseModel):
    keyword: Annotated[str, Field(min_length=1)]
    region: Annotated[
        str,
        Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"),
    ]
    raw_value: Annotated[int, Field(ge=0, le=100)]
    source: Annotated[str, Field(min_length=1)]
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class TrendMetrics(BaseModel):
    momentum_score: Annotated[float, Field(ge=0.0, le=100.0)]
    volatility_index: Annotated[float, Field(ge=0.0, le=100.0)]

    model_config = {"frozen": True}


class TrendAnalysisDetail(BaseModel):
    lifecycle_stage: LifecycleStage
    key_drivers: Annotated[list[str], Field(min_length=1)]
    potential_impact: Annotated[str, Field(min_length=1)]

    model_config = {"frozen": True}


class Anomaly(BaseModel):
    type: Annotated[str, Field(min_length=1)]
    description: Annotated[str, Field(min_length=1)]

    model_config = {"frozen": True}


class TrendTopic(BaseModel):
    trend_id: Annotated[str, Field(min_length=1)]
    topic: Annotated[str, Field(min_length=1)]
    metrics: TrendMetrics
    analysis: TrendAnalysisDetail
    anomalies_detected: list[Anomaly] = Field(default_factory=list)

    @field_validator("topic", mode="before")
    @classmethod
    def _strip_topic(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("topic must be a string")
        stripped = value.strip()
        if not stripped:
            raise ValueError("topic must not be empty or whitespace")
        return stripped

    @classmethod
    def make_trend_id(cls, region: str, topic: str, analysis_date: str) -> str:
        namespace = uuid.NAMESPACE_DNS
        name = f"{region.upper()}:{analysis_date}:{topic.strip().lower()}"
        return str(uuid.uuid5(namespace, name))

    model_config = {"frozen": True}


class ReportMetadata(BaseModel):
    region: Annotated[
        str,
        Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"),
    ]
    date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    @field_validator("region", mode="before")
    @classmethod
    def _upper_region(cls, v: str) -> str:
        return str(v).strip().upper()

    model_config = {"frozen": True}


class MarketAnalysisReport(BaseModel):
    metadata: ReportMetadata
    market_trends: list[TrendTopic] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_trend_ids(self) -> "MarketAnalysisReport":
        import logging
        seen: set[str] = set()
        for t in self.market_trends:
            if t.trend_id in seen:
                logging.getLogger(__name__).warning(
                    "Duplicate trend_id detected: '%s'.", t.trend_id
                )
            seen.add(t.trend_id)
        return self

    model_config = {"frozen": True}