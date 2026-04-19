# src/core/entities.py

"""
Core domain entities for agent_market_intelligence.

Layer rules
───────────
• This module is the innermost layer — zero imports from application or
  infrastructure layers.
• All models are immutable (``frozen = True``) so they can safely be passed
  between threads and cached.

Entity hierarchy
────────────────
RawTrendData          ← raw provider output, unchanged from v1
  │
  └─► (fed into LLM)
          │
          ▼
MarketAnalysisReport  ← top-level LLM output
  ├── ReportMetadata
  └── list[TrendTopic]
        ├── TrendMetrics
        ├── TrendAnalysisDetail
        └── list[Anomaly]
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class LifecycleStage(str, Enum):
    """Standardised lifecycle stage labels returned by the LLM."""

    EMERGING = "Emerging"
    TRENDING = "Trending"
    PEAK = "Peak"
    STAGNANT = "Stagnant"
    DECLINING = "Declining"


# ---------------------------------------------------------------------------
# Raw provider data (unchanged from v1)
# ---------------------------------------------------------------------------

class RawTrendData(BaseModel):
    """
    A single raw, unprocessed trend data point as returned by an external
    provider — before any domain filtering or LLM enrichment.

    Attributes:
        keyword:    The raw search keyword or trend phrase.
        region:     ISO 3166-1 alpha-2 country code, always uppercase (e.g. "US").
        raw_value:  Relative search interest score, normalised to 0–100.
        source:     Name of the data provider (e.g. "google_trends").
        fetched_at: UTC timestamp of retrieval.
        metadata:   Optional provider-specific supplementary fields.
    """

    keyword: Annotated[str, Field(min_length=1, description="Raw search keyword")]
    region: Annotated[
        str,
        Field(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Z]{2}$",
            description="ISO 3166-1 alpha-2 country code (uppercase)",
        ),
    ]
    raw_value: Annotated[
        int,
        Field(ge=0, le=100, description="Relative search interest score (0–100)"),
    ]
    source: Annotated[str, Field(min_length=1, description="Name of the data provider")]
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp of data retrieval",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific supplementary fields (JSON-serialisable)",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Deep analytics sub-models
# ---------------------------------------------------------------------------

class TrendMetrics(BaseModel):
    """
    Quantitative signals extracted by the LLM for a single trend.

    Attributes:
        momentum_score:   Rate of growth / acceleration (0–100).
        volatility_index: Instability / unpredictability of the trend (0–100).
    """

    momentum_score: Annotated[
        float,
        Field(ge=0.0, le=100.0, description="Growth momentum score (0–100)"),
    ]
    volatility_index: Annotated[
        float,
        Field(ge=0.0, le=100.0, description="Trend volatility index (0–100)"),
    ]

    model_config = {"frozen": True}


class TrendAnalysisDetail(BaseModel):
    """
    Qualitative insights produced by the LLM for a single trend.

    Attributes:
        lifecycle_stage:  Where the trend sits in its adoption curve.
        key_drivers:      Identified causal factors (at least one required).
        potential_impact: Free-text synthesis of downstream effects.
    """

    lifecycle_stage: LifecycleStage = Field(
        description="Current position in the trend lifecycle"
    )
    key_drivers: Annotated[
        list[str],
        Field(min_length=1, description="Primary factors driving the trend"),
    ]
    potential_impact: Annotated[
        str,
        Field(min_length=1, description="In-depth assessment of potential impact"),
    ]

    model_config = {"frozen": True}


class Anomaly(BaseModel):
    """
    A single anomalous signal detected within the trend data.

    Attributes:
        type:        Short label (e.g. "Sudden Spike", "Volume Mismatch").
        description: Human-readable explanation of the anomaly.
    """

    type: Annotated[str, Field(min_length=1, description="Anomaly category label")]
    description: Annotated[
        str, Field(min_length=1, description="Explanation of the anomaly")
    ]

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Primary analytics entity (replaces old TrendTopic)
# ---------------------------------------------------------------------------

class TrendTopic(BaseModel):
    """
    A fully enriched, LLM-validated trend topic.

    This replaces the v1 ``TrendTopic`` (which used simple Python heuristics).
    Every field is populated by the ``LLMPort.analyze_trends()`` call.

    Attributes:
        trend_id:           Stable unique identifier for this topic.
        topic:              Human-readable trend keyword / phrase.
        metrics:            Quantitative LLM-derived scores.
        analysis:           Qualitative LLM-derived insights.
        anomalies_detected: Zero or more anomalies flagged by the LLM.
    """

    trend_id: Annotated[
        str, Field(min_length=1, description="Stable unique identifier")
    ]
    topic: Annotated[
        str, Field(min_length=1, description="Trend keyword or phrase")
    ]
    metrics: TrendMetrics
    analysis: TrendAnalysisDetail
    anomalies_detected: list[Anomaly] = Field(
        default_factory=list,
        description="Anomalies flagged by the LLM (may be empty)",
    )

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
        """
        Generate a stable UUID-5 based trend identifier.

        The ID is deterministic for the same (region, topic, date) triple,
        so re-running the pipeline on the same day produces the same IDs.
        """
        namespace = uuid.NAMESPACE_DNS
        name = f"{region.upper()}:{analysis_date}:{topic.strip().lower()}"
        return str(uuid.uuid5(namespace, name))

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Report-level wrapper
# ---------------------------------------------------------------------------

class ReportMetadata(BaseModel):
    """
    Top-level provenance metadata attached to every ``MarketAnalysisReport``.

    Attributes:
        region:       ISO 3166-1 alpha-2 code for the analysed region.
        date:         Calendar date of the analysis (UTC, YYYY-MM-DD).
        processed_at: Full ISO-8601 UTC timestamp of when the report was built.
    """

    region: Annotated[
        str,
        Field(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Z]{2}$",
            description="ISO 3166-1 alpha-2 region code (uppercase)",
        ),
    ]
    date: Annotated[
        str,
        Field(
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="Analysis date (YYYY-MM-DD, UTC)",
        ),
    ]
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp of report generation",
    )

    @field_validator("region", mode="before")
    @classmethod
    def _upper_region(cls, v: str) -> str:
        return str(v).strip().upper()

    model_config = {"frozen": True}


class MarketAnalysisReport(BaseModel):
    """
    The complete LLM-generated market analysis report for one region + date.

    This is the primary output of ``TrendAnalyzerUseCase.execute()`` and the
    primary input to ``StoragePort.save_processed()``.

    Attributes:
        metadata:      Provenance and temporal context.
        market_trends: Ordered list of enriched trend topics (may be empty
                       if the LLM found no actionable trends).
    """

    metadata: ReportMetadata
    market_trends: list[TrendTopic] = Field(
        default_factory=list,
        description="LLM-enriched trend topics for this report",
    )

    @model_validator(mode="after")
    def _unique_trend_ids(self) -> "MarketAnalysisReport":
        """Warn (not fail) if the LLM produced duplicate trend_id values."""
        import logging
        seen: set[str] = set()
        for t in self.market_trends:
            if t.trend_id in seen:
                logging.getLogger(__name__).warning(
                    "Duplicate trend_id detected in LLM output: '%s'. "
                    "Downstream consumers should de-duplicate.",
                    t.trend_id,
                )
            seen.add(t.trend_id)
        return self

    model_config = {"frozen": True}