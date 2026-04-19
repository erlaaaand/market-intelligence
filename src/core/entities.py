from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class LifecycleStage(str, Enum):
    EMERGING = "Emerging"
    TRENDING = "Trending"
    PEAK = "Peak"
    STAGNANT = "Stagnant"
    DECLINING = "Declining"


class EntityType(str, Enum):
    TEAM = "Team"
    PERSON = "Person"
    LOCATION = "Location"
    ORGANIZATION = "Organization"
    EVENT = "Event"
    OTHER = "Other"


# ── Raw ingestion ─────────────────────────────────────────────────────────────

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


# ── Trend identity & metrics ──────────────────────────────────────────────────

class TrendIdentityMetrics(BaseModel):
    momentum_score: Annotated[float, Field(ge=0.0, le=100.0)]
    lifecycle_stage: LifecycleStage

    model_config = {"frozen": True}


class TrendIdentity(BaseModel):
    topic: Annotated[str, Field(min_length=1)]
    category: Annotated[str, Field(min_length=1)]
    region: Annotated[
        str,
        Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"),
    ]
    metrics: TrendIdentityMetrics

    @field_validator("topic", mode="before")
    @classmethod
    def _strip_topic(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("topic must be a string")
        stripped = v.strip()
        if not stripped:
            raise ValueError("topic must not be empty or whitespace")
        return stripped

    @field_validator("region", mode="before")
    @classmethod
    def _upper_region(cls, v: str) -> str:
        return str(v).strip().upper()

    model_config = {"frozen": True}


# ── Contextual intelligence ───────────────────────────────────────────────────

class KeyEntity(BaseModel):
    name: Annotated[str, Field(min_length=1)]
    type: EntityType

    model_config = {"frozen": True}


class SentimentAnalysis(BaseModel):
    primary_emotion: Annotated[str, Field(min_length=1)]
    tone: Annotated[str, Field(min_length=1)]

    model_config = {"frozen": True}


class ContextualIntelligence(BaseModel):
    event_summary: Annotated[str, Field(min_length=1)]
    key_entities: list[KeyEntity] = Field(default_factory=list)
    sentiment_analysis: SentimentAnalysis
    verified_facts: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


# ── Creative brief ────────────────────────────────────────────────────────────

class VideoParameters(BaseModel):
    platform: str = "YouTube Shorts / TikTok"
    target_duration_seconds: Annotated[int, Field(ge=5, le=600)] = 60
    pacing: Annotated[str, Field(min_length=1)]
    language: Annotated[str, Field(min_length=1)]

    model_config = {"frozen": True}


class CreativeBrief(BaseModel):
    target_audience: Annotated[str, Field(min_length=1)]
    video_parameters: VideoParameters
    recommended_angles: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


# ── Distribution assets ───────────────────────────────────────────────────────

class DistributionAssets(BaseModel):
    primary_keywords: list[str] = Field(default_factory=list)
    recommended_hashtags: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


# ── Pipeline routing ──────────────────────────────────────────────────────────

class PipelineRouting(BaseModel):
    source_agent: str = "agent_market_intelligence"
    target_agent: str = "agent_creative"
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    model_config = {"frozen": True}


# ── Top-level document ────────────────────────────────────────────────────────

class CreativeDocument(BaseModel):
    """
    The single output unit produced per trending topic.
    Maps 1-to-1 with the pipeline JSON schema routed to agent_creative.
    """
    document_id: Annotated[str, Field(min_length=1)]
    pipeline_routing: PipelineRouting
    trend_identity: TrendIdentity
    contextual_intelligence: ContextualIntelligence
    creative_brief: CreativeBrief
    distribution_assets: DistributionAssets

    @classmethod
    def make_document_id(cls, region: str, topic: str, analysis_date: str) -> str:
        namespace = uuid.NAMESPACE_DNS
        name = f"{region.upper()}:{analysis_date}:{topic.strip().lower()}"
        short = str(uuid.uuid5(namespace, name)).replace("-", "")[:12]
        date_compact = analysis_date.replace("-", "")
        return f"trend_{short}_{date_compact}"

    model_config = {"frozen": True}


# ── Batch report (list of documents for one pipeline run) ─────────────────────

class CreativeDocumentBatch(BaseModel):
    region: Annotated[
        str,
        Field(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"),
    ]
    date: Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    documents: list[CreativeDocument] = Field(default_factory=list)

    @field_validator("region", mode="before")
    @classmethod
    def _upper_region(cls, v: str) -> str:
        return str(v).strip().upper()

    @model_validator(mode="after")
    def _unique_document_ids(self) -> "CreativeDocumentBatch":
        import logging
        seen: set[str] = set()
        for doc in self.documents:
            if doc.document_id in seen:
                logging.getLogger(__name__).warning(
                    "Duplicate document_id detected: '%s'.", doc.document_id
                )
            seen.add(doc.document_id)
        return self

    model_config = {"frozen": True}


# ── Legacy aliases (kept for any internal tooling that imports the old names) ──
# These point to the closest equivalent in the new schema.

class TrendMetrics(BaseModel):
    """Legacy alias — prefer TrendIdentityMetrics for new code."""
    momentum_score: Annotated[float, Field(ge=0.0, le=100.0)]
    volatility_index: Annotated[float, Field(ge=0.0, le=100.0)]
    model_config = {"frozen": True}


class ReportMetadata(BaseModel):
    """Legacy alias — prefer CreativeDocumentBatch fields for new code."""
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


# MarketAnalysisReport is fully replaced by CreativeDocumentBatch.
# The alias below prevents ImportError in any file that hasn't been updated yet.
MarketAnalysisReport = CreativeDocumentBatch