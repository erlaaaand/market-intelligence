from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from src.core.entities import (
    CreativeDocument,
    CreativeDocumentBatch,
    PipelineRouting,
)
from src.core.exceptions import LLMAnalysisError

logger = logging.getLogger(__name__)

# ── Enum normalisation maps ───────────────────────────────────────────────────
# Handles any casing the LLM might produce: "trending", "TRENDING", "Trending"

_LIFECYCLE_NORM: dict[str, str] = {
    v.lower(): v for v in ("Emerging", "Trending", "Peak", "Stagnant", "Declining")
}

_ENTITY_TYPE_NORM: dict[str, str] = {
    v.lower(): v
    for v in ("Team", "Person", "Location", "Organization", "Event", "Other")
}


# ── Text helpers ──────────────────────────────────────────────────────────────


def strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> blocks produced by reasoning models."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def extract_json_object(text: str) -> str:
    """Extract the outermost JSON object from a string."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start: end + 1]


def _safe_str(value: Any, default: str) -> str:
    """Return *value* if it is a non-empty string, otherwise return *default*."""
    return value if (isinstance(value, str) and value.strip()) else default


# ── Document coercion ─────────────────────────────────────────────────────────


def _coerce_document(
    doc: dict[str, Any],
    region: str,
    analysis_date: str,
    generated_at: str,
) -> dict[str, Any]:
    """
    Inject pipeline-controlled fields, normalise enum cases, clamp numeric
    ranges, and substitute safe defaults for any missing/null required strings.

    This makes validation resilient to common LLM formatting inconsistencies
    (wrong enum case, out-of-range numbers, null values, missing keys) without
    altering the JSON schema consumed by downstream agents.
    """

    # ── 1. Pipeline routing (always overwritten by the pipeline) ──────
    doc["pipeline_routing"] = {
        "source_agent": "agent_market_intelligence",
        "target_agent": "agent_creative",
        "generated_at": generated_at,
    }

    # ── 2. trend_identity ─────────────────────────────────────────────
    ti: dict[str, Any] = doc.get("trend_identity") or {}
    if not isinstance(ti, dict):
        ti = {}

    ti["region"] = region  # always use the pipeline-supplied region code
    ti["topic"] = _safe_str(ti.get("topic"), "Unknown Topic")
    ti["category"] = _safe_str(ti.get("category"), "General")

    metrics: dict[str, Any] = ti.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}

    # Normalise lifecycle_stage to exact Title-case enum value
    raw_stage = str(metrics.get("lifecycle_stage") or "").strip()
    metrics["lifecycle_stage"] = _LIFECYCLE_NORM.get(raw_stage.lower(), "Trending")

    # Clamp momentum_score to [0.0, 100.0]
    try:
        metrics["momentum_score"] = max(
            0.0, min(100.0, float(metrics.get("momentum_score", 50.0)))  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        metrics["momentum_score"] = 50.0

    ti["metrics"] = metrics
    doc["trend_identity"] = ti

    # ── 3. document_id ────────────────────────────────────────────────
    if not _safe_str(doc.get("document_id"), ""):
        doc["document_id"] = CreativeDocument.make_document_id(
            region, ti["topic"], analysis_date
        )

    # ── 4. contextual_intelligence ────────────────────────────────────
    ci: dict[str, Any] = doc.get("contextual_intelligence") or {}
    if not isinstance(ci, dict):
        ci = {}

    ci["event_summary"] = _safe_str(
        ci.get("event_summary"),
        "Topik ini sedang ramai diperbincangkan di media sosial Indonesia.",
    )

    # Normalise entity types to exact Title-case enum value
    raw_entities = ci.get("key_entities")
    if isinstance(raw_entities, list):
        clean_entities: list[dict[str, Any]] = []
        for ent in raw_entities:
            if not isinstance(ent, dict):
                continue
            raw_type = str(ent.get("type") or "").strip()
            ent["type"] = _ENTITY_TYPE_NORM.get(raw_type.lower(), "Other")
            ent["name"] = _safe_str(ent.get("name"), "Unknown")
            clean_entities.append(ent)
        ci["key_entities"] = clean_entities
    else:
        ci["key_entities"] = []

    sa: dict[str, Any] = ci.get("sentiment_analysis") or {}
    if not isinstance(sa, dict):
        sa = {}
    sa["primary_emotion"] = _safe_str(sa.get("primary_emotion"), "Neutral")
    sa["tone"] = _safe_str(sa.get("tone"), "Informative")
    ci["sentiment_analysis"] = sa

    if not isinstance(ci.get("verified_facts"), list):
        ci["verified_facts"] = []

    doc["contextual_intelligence"] = ci

    # ── 5. creative_brief ─────────────────────────────────────────────
    cb: dict[str, Any] = doc.get("creative_brief") or {}
    if not isinstance(cb, dict):
        cb = {}

    cb["target_audience"] = _safe_str(
        cb.get("target_audience"),
        "Pengguna media sosial Indonesia usia 18-35 tahun.",
    )

    vp: dict[str, Any] = cb.get("video_parameters") or {}
    if not isinstance(vp, dict):
        vp = {}
    vp["platform"] = _safe_str(vp.get("platform"), "YouTube Shorts / TikTok")
    vp["pacing"] = _safe_str(vp.get("pacing"), "Medium-paced")
    vp["language"] = _safe_str(vp.get("language"), "Indonesian")

    # Clamp target_duration_seconds to [5, 600]
    try:
        vp["target_duration_seconds"] = max(
            5, min(600, int(vp.get("target_duration_seconds", 60)))  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        vp["target_duration_seconds"] = 60

    cb["video_parameters"] = vp

    if not isinstance(cb.get("recommended_angles"), list):
        cb["recommended_angles"] = []

    doc["creative_brief"] = cb

    # ── 6. distribution_assets ────────────────────────────────────────
    da: dict[str, Any] = doc.get("distribution_assets") or {}
    if not isinstance(da, dict):
        da = {}

    if not isinstance(da.get("primary_keywords"), list):
        da["primary_keywords"] = []
    if not isinstance(da.get("recommended_hashtags"), list):
        da["recommended_hashtags"] = []

    doc["distribution_assets"] = da

    return doc


def _normalise_document(
    doc: dict[str, Any],
    region: str,
    analysis_date: str,
    generated_at: str,
) -> dict[str, Any]:
    """Inject / overwrite fields that must be controlled by the pipeline."""
    return _coerce_document(doc, region, analysis_date, generated_at)


# ── Public entry point ────────────────────────────────────────────────────────


def parse_and_validate(
    content: str,
    model: str,
    region: str,
    analysis_date: str,
) -> CreativeDocumentBatch:
    """
    Parse raw LLM output into a CreativeDocumentBatch.
    Attempts full-batch validation first; falls back to per-document recovery.
    """
    cleaned = strip_thinking_tags(content)
    json_str = extract_json_object(cleaned)

    try:
        raw_obj: dict[str, Any] = json.loads(json_str)
    except json.JSONDecodeError:
        raise LLMAnalysisError(
            model=model,
            reason=(
                "LLM returned output that could not be parsed as JSON. "
                f"First 300 chars: {json_str[:300]}"
            ),
        ) from None

    generated_at = datetime.now(tz=timezone.utc).isoformat()
    raw_docs: list[Any] = raw_obj.get("documents", [])

    # Coerce + normalise each document before Pydantic validation
    normalised = [
        _normalise_document(doc, region, analysis_date, generated_at)
        for doc in raw_docs
        if isinstance(doc, dict)
    ]

    # Attempt full-batch validation
    batch_payload = {
        "region": region,
        "date": analysis_date,
        "documents": normalised,
    }
    try:
        return CreativeDocumentBatch.model_validate(batch_payload)
    except ValidationError as full_err:
        logger.warning(
            "Full CreativeDocumentBatch validation failed (%d error(s)). "
            "Attempting per-document recovery.",
            full_err.error_count(),
        )

    # Per-document recovery
    valid_docs: list[CreativeDocument] = []
    for doc in normalised:
        try:
            valid_docs.append(CreativeDocument.model_validate(doc))
        except ValidationError as doc_err:
            topic = (doc.get("trend_identity") or {}).get("topic", "<unknown>")
            logger.warning(
                "Dropping invalid document (topic=%r): %d error(s).",
                topic,
                doc_err.error_count(),
            )

    if not valid_docs:
        raise LLMAnalysisError(
            model=model,
            reason=(
                "LLM output failed validation and no individual documents "
                "could be recovered."
            ),
        )

    logger.info("Partial recovery: %d valid document(s) salvaged.", len(valid_docs))
    return CreativeDocumentBatch(
        region=region,
        date=analysis_date,
        documents=valid_docs,
    )