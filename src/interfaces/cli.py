# src/interfaces/cli.py

"""
Command-line interface for the agent_market_intelligence module.

Parses `--region` from argv, validates it, then delegates to the
injected `TrendAnalyzerUseCase`. All domain exceptions are caught here
and presented as clean, user-friendly error messages before exiting
with a non-zero code.
"""
from __future__ import annotations

import argparse
import logging
import sys

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import TrendTopic
from src.core.exceptions import (
    AgentMarketIntelligenceError,
    DataExtractionError,
    RateLimitExceededError,
    StorageError,
)

logger = logging.getLogger(__name__)

_DIVIDER = "─" * 62


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build and return the CLI argument parser.

    Returns:
        A configured `argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="agent_market_intelligence",
        description=(
            "Fetch, filter, and persist trending market topics "
            "from external data providers (Google Trends, YouTube …)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --region US\n"
            "  python main.py --region ID\n"
            "  python main.py            # uses TARGET_REGION from .env / config\n"
        ),
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="CC",
        help=(
            "ISO 3166-1 alpha-2 country code (e.g. US, ID, GB). "
            "Overrides the TARGET_REGION setting in .env / config.py."
        ),
    )
    return parser


def run_cli(use_case: TrendAnalyzerUseCase, default_region: str) -> None:
    """
    Parse CLI arguments and execute the market-intelligence pipeline.

    Args:
        use_case:       Fully wired `TrendAnalyzerUseCase` instance.
        default_region: Fallback region when `--region` is not supplied.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    region: str = (args.region or default_region).upper().strip()

    # Validate country code format before hitting the network.
    if len(region) != 2 or not region.isalpha():
        parser.error(
            f"Invalid region code '{region}'. "
            "Expected a 2-letter ISO 3166-1 alpha-2 code (e.g. US, ID, GB)."
        )

    logger.info("CLI: starting pipeline for region='%s'.", region)

    try:
        results: list[TrendTopic] = use_case.execute(region=region)
    except RateLimitExceededError as exc:
        _exit_error("Rate limit exceeded", exc.message, code=2)
    except DataExtractionError as exc:
        _exit_error("Data extraction failed", exc.message, code=3)
    except StorageError as exc:
        _exit_error("Storage failure", exc.message, code=4)
    except AgentMarketIntelligenceError as exc:
        _exit_error("Unexpected domain error", exc.message, code=5)

    _print_results(results, region)


# ------------------------------------------------------------------
# Output formatting helpers
# ------------------------------------------------------------------

def _print_results(topics: list[TrendTopic], region: str) -> None:
    """Render the processed `TrendTopic` list as a human-readable table."""
    if not topics:
        print(f"\n  No trending topics found for region '{region}'.\n")
        return

    print(f"\n{_DIVIDER}")
    print(f"  Market Intelligence Report  ·  Region: {region}")
    print(_DIVIDER)

    for idx, topic in enumerate(topics, start=1):
        growth_badge = "↑ Growing" if topic.is_growing else "→ Stable"
        print(
            f"\n  {idx:>2}. {topic.topic_name}\n"
            f"       Volume : {topic.search_volume:>3}/100   {growth_badge}\n"
            f"       Angle  : {topic.suggested_angle}"
        )

    print(f"\n{_DIVIDER}")
    print(f"  ✓ {len(topics)} topic(s) persisted to data/processed/\n")


def _exit_error(label: str, detail: str, code: int = 1) -> None:
    """Print a structured error message to stderr and exit with *code*."""
    logger.error("%s: %s", label, detail)
    print(f"\n  [ERROR] {label}\n  {detail}\n", file=sys.stderr)
    sys.exit(code)