from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from src.core.entities import (
    MarketAnalysisReport,
    ReportMetadata,
    TrendTopic,
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


def parse_and_validate(
    content: str,
    model: str,
    region: str,
    analysis_date: str,
) -> MarketAnalysisReport:
    """
    Parse raw LLM output and validate it against MarketAnalysisReport.
    Falls back to per-item recovery when full validation fails.
    """
    import json

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

    # Normalise metadata fields
    raw_obj.setdefault("metadata", {})
    raw_obj["metadata"]["region"] = region
    raw_obj["metadata"]["date"] = analysis_date
    raw_obj["metadata"].setdefault(
        "processed_at",
        datetime.now(tz=timezone.utc).isoformat(),
    )

    # Attempt full validation first
    try:
        return MarketAnalysisReport.model_validate(raw_obj)
    except ValidationError as full_err:
        logger.warning(
            "Full MarketAnalysisReport validation failed (%d error(s)). "
            "Attempting per-item recovery.",
            full_err.error_count(),
        )

    # Per-item recovery
    valid_trends: list[TrendTopic] = []
    for item in raw_obj.get("market_trends", []):
        if not isinstance(item, dict):
            continue
        try:
            valid_trends.append(TrendTopic.model_validate(item))
        except ValidationError as item_err:
            logger.warning(
                "Dropping invalid trend item (topic=%r): %d error(s).",
                item.get("topic", "<unknown>"),
                item_err.error_count(),
            )

    if not valid_trends:
        raise LLMAnalysisError(
            model=model,
            reason=(
                "LLM output failed validation and no individual trends "
                "could be recovered."
            ),
        )

    logger.info("Partial recovery: %d valid trend(s) salvaged.", len(valid_trends))
    return MarketAnalysisReport(
        metadata=ReportMetadata(region=region, date=analysis_date),
        market_trends=valid_trends,
    )