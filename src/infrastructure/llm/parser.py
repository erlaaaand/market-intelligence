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


def _normalise_document(
    doc: dict[str, Any],
    region: str,
    analysis_date: str,
    generated_at: str,
) -> dict[str, Any]:
    """Inject / overwrite fields that must be controlled by the pipeline."""
    # Pipeline routing is always set by us, not the LLM
    doc["pipeline_routing"] = {
        "source_agent": "agent_market_intelligence",
        "target_agent": "agent_creative",
        "generated_at": generated_at,
    }
    # Ensure region is always the one we passed in
    doc.setdefault("trend_identity", {})
    doc["trend_identity"]["region"] = region
    return doc


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

    # Normalise each document before validation
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