# src/core/brief_entities.py

"""
Core domain entities for the Content Brief Generator pipeline.

These Pydantic v2 models are the canonical, technology-agnostic data contracts
for the full brief generation pipeline. They must never import from the
infrastructure or application layers.

Design principles:
  - Every field has a concrete, explicit type — no `Any`.
  - All models are frozen (immutable) once constructed.
  - Business invariants are enforced via `@field_validator` at construction time.
  - `@computed_field` properties are serialised by `model_dump()` automatically.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, computed_field, field_validator


# ---------------------------------------------------------------------------
# Enumerations — canonical value sets for categorical fields
# ---------------------------------------------------------------------------


class TrendTier(str, Enum):
    """
    Categorical classification of a trend topic by normalised search intensity.

    Tiers drive the entire content strategy:
        BREAKING  (≥ 80/100): Exceptional interest. Publish immediately.
        EMERGING  (≥ 60/100): Growing interest. Establish early authority.
        DEEP_DIVE (< 60/100): Specialist interest. Long-form authority play.
    """

    BREAKING = "breaking"
    EMERGING = "emerging"
    DEEP_DIVE = "deep_dive"


class SearchIntent(str, Enum):
    """
    SEO search intent classification following Google's four-category taxonomy.

    Used to align content structure and CTA with what the searcher actually wants.
    """

    INFORMATIONAL = "informational"
    NAVIGATIONAL = "navigational"
    COMMERCIAL = "commercial"
    TRANSACTIONAL = "transactional"


class ContentFormatType(str, Enum):
    """Canonical list of supported output content formats."""

    BLOG_POST = "blog_post"
    VIDEO_SCRIPT = "video_script"
    SOCIAL_THREAD = "social_thread"
    NEWSLETTER = "newsletter"
    PODCAST_NOTES = "podcast_notes"


class UrgencyLevel(str, Enum):
    """
    Recommended publication urgency tier.

    IMMEDIATE  : Publish within 2–4 hours of brief generation.
    THIS_WEEK  : Publish within 7 calendar days.
    THIS_MONTH : Publish within 30 calendar days.
    """

    IMMEDIATE = "immediate"
    THIS_WEEK = "this_week"
    THIS_MONTH = "this_month"


# ---------------------------------------------------------------------------
# Nested value objects
# ---------------------------------------------------------------------------


class AudienceProfile(BaseModel):
    """
    A derived target audience persona inferred from the trend tier and keyword.

    All fields are generated from business rules — no manual research required.
    Concrete enough to guide content writing; generic enough to apply universally.
    """

    primary_segment: Annotated[
        str, Field(min_length=1, description="Main target audience archetype")
    ]
    secondary_segment: Annotated[
        str, Field(min_length=1, description="Adjacent or secondary audience")
    ]
    pain_points: Annotated[
        list[str],
        Field(min_length=2, description="Core problems this content addresses"),
    ]
    goals: Annotated[
        list[str],
        Field(min_length=2, description="What the audience wants to achieve"),
    ]
    content_consumption_habits: Annotated[
        list[str],
        Field(min_length=1, description="Where and how this audience discovers content"),
    ]
    demographic_hint: Annotated[
        str,
        Field(min_length=1, description="Broad demographic descriptor for ad targeting"),
    ]

    model_config = {"frozen": True}


class RecommendedFormat(BaseModel):
    """
    Prescribes the optimal content format and platform configuration.

    Format selection balances production speed (critical at BREAKING tier)
    against depth and SEO durability (critical at DEEP_DIVE tier).
    """

    format_type: ContentFormatType
    estimated_length: Annotated[
        str,
        Field(min_length=1, description="Human-readable length estimate (e.g. '1,400–2,000 words')"),
    ]
    primary_platform: Annotated[
        str, Field(min_length=1, description="Primary publication or distribution channel")
    ]
    secondary_platforms: list[str]
    tone: Annotated[
        str,
        Field(min_length=1, description="Recommended editorial tone (e.g. 'authoritative yet approachable')"),
    ]

    model_config = {"frozen": True}


class OutlineSection(BaseModel):
    """
    A single section within a content brief outline.

    Each section maps directly to a structural unit of the final published
    piece: a blog heading, a video chapter, or a newsletter segment.
    """

    section_number: Annotated[int, Field(ge=1, description="1-based section index")]
    title: Annotated[str, Field(min_length=1, description="Section heading text")]
    purpose: Annotated[
        str,
        Field(min_length=1, description="Authorial goal for this section"),
    ]
    key_points: Annotated[
        list[str],
        Field(min_length=1, description="Bullet-point talking points for the writer"),
    ]
    estimated_word_count: Annotated[
        int,
        Field(ge=50, le=2_000, description="Target word count for this section"),
    ]

    model_config = {"frozen": True}


class SeoRecommendations(BaseModel):
    """
    Keyword strategy and on-page metadata guidance for organic search optimisation.
    """

    primary_keyword: Annotated[str, Field(min_length=1)]
    semantic_keywords: Annotated[
        list[str],
        Field(min_length=3, description="LSI and long-tail keyword variants"),
    ]
    title_variants: Annotated[
        list[str],
        Field(min_length=2, description="H1 title alternatives for A/B testing"),
    ]
    meta_description_template: Annotated[
        str,
        Field(
            min_length=50,
            max_length=300,
            description="~160-character meta description template",
        ),
    ]
    target_search_intent: SearchIntent
    estimated_keyword_difficulty: Annotated[
        int,
        Field(
            ge=0,
            le=100,
            description="Estimated SEO difficulty score (0 = easiest, 100 = hardest)",
        ),
    ]

    model_config = {"frozen": True}


class DistributionPlan(BaseModel):
    """
    Multi-channel distribution strategy derived from trend urgency and format tier.
    """

    primary_channel: Annotated[str, Field(min_length=1)]
    secondary_channels: list[str]
    optimal_publish_window: Annotated[
        str,
        Field(min_length=1, description="Best day/time range to publish"),
    ]
    hashtag_suggestions: Annotated[list[str], Field(min_length=1)]
    urgency_level: UrgencyLevel
    cross_posting_notes: Annotated[
        str,
        Field(
            min_length=1,
            description="Platform-specific adaptation guidance for cross-posting",
        ),
    ]

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Primary aggregate root
# ---------------------------------------------------------------------------


class ContentBrief(BaseModel):
    """
    A complete, structured content strategy brief derived from a single TrendTopic.

    This entity is the primary output of the Content Brief Generator pipeline.
    It is fully self-contained: a content writer can execute on this brief
    without consulting any external system.

    Attributes:
        brief_id:             UUID v4 unique identifier.
        topic_name:           Source trend keyword (title-cased via upstream validator).
        region:               ISO 3166-1 alpha-2 country code, uppercase.
        search_volume:        Normalised search interest score (0–100).
        trend_tier:           BREAKING | EMERGING | DEEP_DIVE classification.
        is_growing:           Momentum flag inherited from the upstream TrendTopic.
        suggested_angle:      Raw editorial angle from the upstream pipeline.
        executive_summary:    One-paragraph strategic overview of this brief.
        audience_profile:     Derived target audience persona.
        recommended_format:   Prescribed content format and platform.
        content_outline:      Ordered list of section blueprints.
        seo_recommendations:  Keyword strategy and metadata guidance.
        distribution_plan:    Multi-channel publishing and timing strategy.
        total_estimated_words: Computed sum of all outline section word counts.
        generated_at:         UTC timestamp of brief construction.
        source_trend_file:    Filename of the processed trend file that seeded this brief.
    """

    brief_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID v4 unique identifier for this brief",
    )
    topic_name: Annotated[str, Field(min_length=1)]
    region: Annotated[
        str,
        Field(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Z]{2}$",
            description="ISO 3166-1 alpha-2 country code (uppercase)",
        ),
    ]
    search_volume: Annotated[int, Field(ge=0, le=100)]
    trend_tier: TrendTier
    is_growing: bool
    suggested_angle: Annotated[str, Field(min_length=1)]
    executive_summary: Annotated[str, Field(min_length=20)]
    audience_profile: AudienceProfile
    recommended_format: RecommendedFormat
    content_outline: Annotated[list[OutlineSection], Field(min_length=1)]
    seo_recommendations: SeoRecommendations
    distribution_plan: DistributionPlan
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
    source_trend_file: Annotated[str, Field(min_length=1)]

    @computed_field  # type: ignore[misc]
    @property
    def total_estimated_words(self) -> int:
        """
        Computed aggregate word count across all outline sections.

        Included automatically in model_dump() and JSON serialisation.
        Writers can use this as a quick calibration reference.
        """
        return sum(section.estimated_word_count for section in self.content_outline)

    @field_validator("region", mode="before")
    @classmethod
    def _normalise_region(cls, value: str) -> str:
        return str(value).strip().upper()

    @field_validator("topic_name", "suggested_angle", "executive_summary", mode="before")
    @classmethod
    def _strip_and_validate_strings(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Field must be a string.")
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field must not be blank.")
        return stripped

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Batch aggregate — groups a full pipeline run's output
# ---------------------------------------------------------------------------


class BriefBatch(BaseModel):
    """
    An ordered collection of ContentBrief entities produced in a single pipeline run.

    Persisted as a single JSON file for atomic retrieval of a full run's output.
    The `brief_count` computed field provides a quick cardinality check without
    requiring callers to inspect the nested list.

    Attributes:
        batch_id:          UUID v4 run identifier.
        region:            ISO country code inferred from the source trend file.
        source_trend_file: Filename of the upstream processed trend data.
        generated_at:      UTC timestamp of batch creation.
        briefs:            Ordered list of generated ContentBrief entities.
        brief_count:       Computed count of briefs (included in serialisation).
    """

    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    region: Annotated[
        str,
        Field(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Z]{2}$",
        ),
    ]
    source_trend_file: Annotated[str, Field(min_length=1)]
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
    )
    briefs: list[ContentBrief]

    @computed_field  # type: ignore[misc]
    @property
    def brief_count(self) -> int:
        """Number of briefs in this batch."""
        return len(self.briefs)

    model_config = {"frozen": True}