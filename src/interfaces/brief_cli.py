# src/interfaces/brief_cli.py

"""
Interactive command-line interface for the Content Brief Generator.

Behaviour:
  • With --file FILENAME  → processes that specific trend file, no prompts.
  • With --region CC      → filters the interactive file picker by region.
  • With no arguments     → drops into a full-colour interactive menu that
                            lists available processed trend files, lets the
                            user pick one, then renders a formatted results
                            table on completion.

ANSI colour codes are used directly — no third-party library required.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from typing import NoReturn

from src.application.content_brief_generator import ContentBriefGeneratorUseCase
from src.core.brief_entities import BriefBatch, ContentBrief, TrendTier
from src.core.exceptions import (
    AgentMarketIntelligenceError,
    BriefGenerationError,
    StorageError,
    TrendFileNotFoundError,
    TrendFileParseError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI escape codes — colour palette
# ---------------------------------------------------------------------------
_R = "\033[0m"        # reset
_B = "\033[1m"        # bold
_D = "\033[2m"        # dim
_RED = "\033[31m"
_GRN = "\033[32m"
_YLW = "\033[33m"
_CYN = "\033[36m"
_WHT = "\033[37m"
_BG_MAG = "\033[45m"  # background magenta (header bar)

# Console width used for decorative separators.
_W: int = 68

# ---------------------------------------------------------------------------
# Tier → visual badge mapping
# ---------------------------------------------------------------------------
_TIER_BADGE: dict[TrendTier, str] = {
    TrendTier.BREAKING:  f"{_B}{_RED}◆ BREAKING {_R}",
    TrendTier.EMERGING:  f"{_B}{_YLW}▲ EMERGING {_R}",
    TrendTier.DEEP_DIVE: f"{_B}{_CYN}● DEEP DIVE{_R}",
}
_TIER_CLR: dict[TrendTier, str] = {
    TrendTier.BREAKING:  _RED,
    TrendTier.EMERGING:  _YLW,
    TrendTier.DEEP_DIVE: _CYN,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_brief_cli(
    use_case: ContentBriefGeneratorUseCase,
    available_files_fn: Callable[[str | None], list[str]],
) -> None:
    """
    Parse CLI arguments and execute the content brief pipeline.

    Drops into the interactive file picker when no --file argument is supplied.

    Args:
        use_case:           Fully assembled ContentBriefGeneratorUseCase.
        available_files_fn: Bound method from TrendReaderPort.list_available_files.
                            Signature: (region: str | None) -> list[str]
    """
    args = _build_arg_parser().parse_args()
    region: str | None = args.region.upper().strip() if args.region else None

    _print_header()

    # ── Resolve source file ───────────────────────────────────────────
    resolved_file: str
    if args.file is not None:
        resolved_file = args.file
        _sep()
        _info(f"  Source  : {_B}{resolved_file}{_R}")
    else:
        resolved_file = _interactive_pick(available_files_fn, region)

    _sep()
    _info(f"\n  {_CYN}Running pipeline…{_R}\n")

    # ── Execute ───────────────────────────────────────────────────────
    try:
        batch: BriefBatch = use_case.execute(
            source_filename=resolved_file,
            region=region,
        )
    except TrendFileNotFoundError as exc:
        _die("Trend file not found", exc.message, code=2)
    except TrendFileParseError as exc:
        _die("Trend file parse error", exc.message, code=3)
    except BriefGenerationError as exc:
        _die("Brief generation failed", exc.message, code=4)
    except StorageError as exc:
        _die("Storage failure", exc.message, code=5)
    except AgentMarketIntelligenceError as exc:
        _die("Pipeline error", exc.message, code=6)

    _print_results(batch)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content_brief_generator",
        description=(
            "Generate structured JSON content briefs from processed trend data.\n"
            "Run without arguments to enter interactive mode."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main_brief.py\n"
            "  python main_brief.py --region US\n"
            "  python main_brief.py --file processed_trends_ID_20240601T120000Z.json\n"
        ),
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        metavar="FILENAME",
        help=(
            "Bare filename of a processed trend file to convert into briefs. "
            "Skips the interactive picker."
        ),
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="CC",
        help="ISO 3166-1 alpha-2 country code used to filter available files.",
    )
    return parser


# ---------------------------------------------------------------------------
# Interactive file picker
# ---------------------------------------------------------------------------


def _interactive_pick(
    available_files_fn: Callable[[str | None], list[str]],
    region: str | None,
) -> str:
    """
    Show a numbered menu of available processed trend files and return the choice.

    Args:
        available_files_fn: Returns available filenames (may raise TrendFileNotFoundError).
        region:             Optional ISO region filter.

    Returns:
        Bare filename chosen by the user.
    """
    hint = f" for region {_B}{region}{_R}" if region else ""
    print(f"\n  {_CYN}Scanning available trend files{hint}…{_R}\n")

    try:
        files = available_files_fn(region)
    except TrendFileNotFoundError as exc:
        _die("No trend files found", exc.message, code=2)

    print(f"  {_B}Available files{_R}  ({len(files)} found, newest first)\n")
    for idx, name in enumerate(files, start=1):
        print(f"    {_D}{idx:>2}.{_R}  {name}")

    print()

    while True:
        try:
            raw = input(
                f"  {_B}{_CYN}▶  Select a file [1–{len(files)}]: {_R}"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n  {_D}Aborted.{_R}\n")
            sys.exit(0)

        if raw.isdigit() and 1 <= int(raw) <= len(files):
            chosen = files[int(raw) - 1]
            print(f"\n  Selected: {_B}{chosen}{_R}")
            return chosen

        print(f"  {_YLW}  Please enter a number between 1 and {len(files)}.{_R}")


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def _print_header() -> None:
    """Print the branded application banner."""
    print()
    print(
        f"  {_B}{_BG_MAG}{_WHT}  Content Brief Generator  {_R}"
        f"  {_D}agent_market_intelligence · brief pipeline{_R}"
    )
    print(f"  {_D}Transforms processed trend data into structured JSON content briefs.{_R}")


def _sep() -> None:
    print(f"  {_D}{'─' * (_W - 4)}{_R}")


def _info(msg: str) -> None:
    print(msg)


def _print_results(batch: BriefBatch) -> None:
    """Render a formatted summary table of the generated BriefBatch."""
    print()
    _sep()
    print(
        f"\n  {_B}Results{_R}"
        f"   Region: {_B}{batch.region}{_R}"
        f"   Source: {_D}{batch.source_trend_file}{_R}"
    )
    _sep()

    if not batch.briefs:
        print(
            f"\n  {_YLW}  No briefs generated —"
            f" source file contained no topics.{_R}\n"
        )
        return

    for idx, brief in enumerate(batch.briefs, start=1):
        badge = _TIER_BADGE.get(brief.trend_tier, brief.trend_tier.value)
        clr = _TIER_CLR.get(brief.trend_tier, _R)
        grow = f"{_GRN}↑ Growing{_R}" if brief.is_growing else f"{_D}→ Stable{_R}"
        fmt = brief.recommended_format.format_type.value.replace("_", " ").title()
        intent = brief.seo_recommendations.target_search_intent.value.title()
        urgency = brief.distribution_plan.urgency_level.value.replace("_", " ").title()

        print(f"\n  {_B}{idx:>2}. {brief.topic_name}{_R}")
        print(f"       Tier    : {badge}   Volume : {clr}{brief.search_volume}/100{_R}   {grow}")
        print(f"       Format  : {fmt}   ~{brief.total_estimated_words:,} words")
        print(f"       Intent  : {intent}   Urgency : {urgency}")
        print(f"       Diff.   : {brief.seo_recommendations.estimated_keyword_difficulty}/100 (SEO difficulty)")
        print(f"       ID      : {_D}{brief.brief_id}{_R}")

    print()
    _sep()
    print(
        f"\n  {_GRN}✓{_R} {_B}{batch.brief_count}{_R} brief(s) saved to "
        f"{_CYN}data/briefs/{_R}\n"
    )


# ---------------------------------------------------------------------------
# Error exit helper (NoReturn ensures type-checkers treat it as a dead end)
# ---------------------------------------------------------------------------


def _die(label: str, detail: str, code: int = 1) -> NoReturn:
    """Print a structured error to stderr and exit with the given code."""
    logger.error("%s: %s", label, detail)
    print(f"\n  {_B}{_RED}[ERROR]{_R} {label}", file=sys.stderr)
    print(f"  {_D}{detail}{_R}\n", file=sys.stderr)
    sys.exit(code)