# src/core/entities.py

"""
Core domain entities for the agent_market_intelligence module.

These Pydantic models represent the canonical data structures used
throughout the application layer and are technology-agnostic.
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class RawTrendData(BaseModel):
    """
    Represents a single raw, unprocessed trend data point as returned
    by an external provider before any domain filtering or enrichment.

    Attributes:
        keyword:        The raw search keyword or trend phrase.
        region:         The ISO 3166-1 alpha-2 country code (e.g. "US", "ID").
        raw_value:      The relative search interest score (0–100 for Google Trends).
        source:         The name of the data provider (e.g. "google_trends").
        fetched_at:     UTC timestamp when this data point was retrieved.
        metadata:       Optional dict for provider-specific extra fields.
    """

    keyword: Annotated[str, Field(min_length=1, description="Raw search keyword")]
    region: Annotated[
        str,
        Field(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Z]{2}$",
            description="ISO 3166-1 alpha-2 country code, uppercase",
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
    metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Provider-specific supplementary fields",
    )

    model_config = {"frozen": True}


class TrendTopic(BaseModel):
    """
    Represents a fully processed and validated trend topic, ready for
    consumption by downstream systems (reports, dashboards, LLM prompts).

    Attributes:
        topic_name:       The human-readable trend topic or keyword.
        search_volume:    Normalised relative interest score (0–100).
        target_country:   ISO 3166-1 alpha-2 country code.
        suggested_angle:  A short editorial angle for content creation.
        is_growing:       True if the topic shows upward momentum.
        processed_at:     UTC timestamp when the entity was created.
    """

    topic_name: Annotated[str, Field(min_length=1, description="Trend topic or keyword")]
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
            description="ISO 3166-1 alpha-2 country code, uppercase",
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
    def strip_and_title_case(cls, value: str) -> str:
        """Normalise topic names to stripped title-case."""
        return value.strip().title()

    @field_validator("suggested_angle", mode="before")
    @classmethod
    def strip_angle(cls, value: str) -> str:
        """Strip leading/trailing whitespace from the suggested angle."""
        return value.strip()

    model_config = {"frozen": True}