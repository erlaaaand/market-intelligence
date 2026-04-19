# src/interfaces/cli.py

"""
Command-line interface for the agent_market_intelligence module.

Accepts a `--region` flag to override the default target country
and delegates execution to the injected `TrendAnalyzerUseCase`.
"""

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
            "from external providers (Google Trends, YouTube, …)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --region US\n"
            "  python main.py --region ID\n"
            "  python main.py  # uses TARGET_REGION from config / .env\n"
        ),
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="CC",
        help=(
            "ISO 3166-1 alpha-2 country code (e.g. US, ID, GB). "
            "Overrides the TARGET_REGION setting from config / .env."
        ),
    )
    return parser


def run_cli(use_case: TrendAnalyzerUseCase, default_region: str) -> None:
    """
    Parse CLI arguments and execute the market-intelligence pipeline.

    Args:
        use_case:       Fully initialised `TrendAnalyzerUseCase` instance.
        default_region: Fallback region when `--region` is not supplied.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    region: str = (args.region or default_region).upper().strip()

    if len(region) != 2 or not region.isalpha():
        parser.error(
            f"Invalid region code '{region}'. "
            "Expected a 2-letter ISO 3166-1 alpha-2 code (e.g. US, ID, GB)."
        )

    logger.info("CLI: executing pipeline for region='%s'.", region)

    try:
        results: list[TrendTopic] = use_case.execute(region=region)
    except RateLimitExceededError as exc:
        logger.error("Rate limit exceeded: %s", exc.message)
        print(f"\n[ERROR] {exc.message}", file=sys.stderr)
        sys.exit(1)
    except DataExtractionError as exc:
        logger.error("Data extraction failed: %s", exc.message)
        print(f"\n[ERROR] {exc.message}", file=sys.stderr)
        sys.exit(1)
    except StorageError as exc:
        logger.error("Storage failure: %s", exc.message)
        print(f"\n[ERROR] {exc.message}", file=sys.stderr)
        sys.exit(1)
    except AgentMarketIntelligenceError as exc:
        logger.error("Unexpected domain error: %s", exc.message)
        print(f"\n[ERROR] {exc.message}", file=sys.stderr)
        sys.exit(1)

    _print_results(results, region)


# ------------------------------------------------------------------
# Output formatting
# ------------------------------------------------------------------

def _print_results(topics: list[TrendTopic], region: str) -> None:
    """Render the processed trend topics as a human-readable summary."""
    if not topics:
        print(f"\nNo trending topics found for region '{region}'.")
        return

    divider = "─" * 60
    print(f"\n{divider}")
    print(f"  Market Intelligence Report  ·  Region: {region}")
    print(divider)

    for idx, topic in enumerate(topics, start=1):
        growth_label = "↑ Growing" if topic.is_growing else "→ Stable"
        print(
            f"\n  {idx}. {topic.topic_name}\n"
            f"     Volume : {topic.search_volume}/100   {growth_label}\n"
            f"     Angle  : {topic.suggested_angle}"
        )

    print(f"\n{divider}")
    print(f"  {len(topics)} topic(s) saved to data/processed/\n")