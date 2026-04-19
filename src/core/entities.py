# src/core/entities.py

"""
Core domain entities for the agent_market_intelligence module.

These Pydantic v2 models are the canonical data contracts for the entire
pipeline. They are technology-agnostic and must never import from
infrastructure or application layers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator


class RawTrendData(BaseModel):
    """
    A single raw, unprocessed trend data point as returned by an external
    provider — before any domain filtering or enrichment.

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
    # FIX: use dict[str, Any] — provider metadata values are heterogeneous
    # and restricting to str|int|float|bool caused validation errors when
    # pytrends returned integer rank values alongside boolean flags.
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific supplementary fields (JSON-serialisable)",
    )

    model_config = {"frozen": True}


class TrendTopic(BaseModel):
    """
    A fully processed and validated trend topic, ready for consumption by
    downstream systems (reports, dashboards, LLM prompts, storage).

    Attributes:
        topic_name:      Human-readable trend keyword (title-cased).
        search_volume:   Normalised relative interest score (0–100).
        target_country:  ISO 3166-1 alpha-2 country code.
        suggested_angle: Short editorial angle for content strategy.
        is_growing:      True when the topic shows upward momentum.
        processed_at:    UTC timestamp of entity creation.
    """

    topic_name: Annotated[
        str, Field(min_length=1, description="Trend topic or keyword")
    ]
    search_volume: Annotated[
        int,
        Field(ge=0, le=100, description="Normalised relative search interest (0–100)"),
    ]
    target_country: Annotated[
        str,
        Field(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Z]{2}$",
            description="ISO 3166-1 alpha-2 country code (uppercase)",
        ),
    ]
    suggested_angle: Annotated[
        str,
        Field(min_length=1, description="Short editorial angle for content strategy"),
    ]
    is_growing: Annotated[bool, Field(description="True if topic shows upward trend")]
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp when this entity was processed",
    )

    @field_validator("topic_name", mode="before")
    @classmethod
    def _strip_and_title_case(cls, value: str) -> str:
        """Normalise topic names: strip whitespace and convert to title-case."""
        if not isinstance(value, str):
            raise ValueError("topic_name must be a string")
        return value.strip().title()

    @field_validator("suggested_angle", mode="before")
    @classmethod
    def _strip_angle(cls, value: str) -> str:
        """Strip leading/trailing whitespace from the suggested content angle."""
        if not isinstance(value, str):
            raise ValueError("suggested_angle must be a string")
        return value.strip()

    model_config = {"frozen": True}