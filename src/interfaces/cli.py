from __future__ import annotations

"""
CLI interface — powered by `rich` for polished, production-grade terminal UX.

Key improvements over the previous ANSI/threading implementation:
  - `rich.console` handles color/no-color detection automatically (CI, pipes, etc.)
  - `rich.progress` spinner replaces the hand-rolled threading spinner
  - `rich.table` produces a professional results table
  - `rich.panel` / `rich.text` give clean error formatting
  - Zero manual ANSI escape codes — everything is theme-consistent
"""

import argparse
import logging
import sys
from typing import Final

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import TrendTopic
from src.core.exceptions import (
    AgentMarketIntelligenceError,
    DataExtractionError,
    RateLimitExceededError,
    StorageError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Console setup
# ---------------------------------------------------------------------------

_THEME = Theme(
    {
        "header": "bold bright_cyan",
        "subheader": "dim white",
        "region": "bold yellow",
        "rank": "bold cyan",
        "topic": "bold white",
        "volume_high": "bold red",
        "volume_mid": "bold yellow",
        "volume_low": "cyan",
        "growing": "bold green",
        "stable": "yellow",
        "angle": "magenta",
        "success": "bold green",
        "error": "bold red",
        "warning": "yellow",
        "hint": "dim white",
        "divider": "dim blue",
        "number": "bold bright_blue",
        "prompt": "bold cyan",
    }
)

console = Console(theme=_THEME, highlight=False)

# ---------------------------------------------------------------------------
# Region registry
# ---------------------------------------------------------------------------

_REGIONS: Final[dict[str, str]] = {
    "US": "United States",
    "ID": "Indonesia",
    "GB": "United Kingdom",
    "IN": "India",
    "AU": "Australia",
    "CA": "Canada",
    "SG": "Singapore",
    "MY": "Malaysia",
    "PH": "Philippines",
    "DE": "Germany",
    "FR": "France",
    "JP": "Japan",
    "KR": "South Korea",
    "BR": "Brazil",
    "MX": "Mexico",
    "TH": "Thailand",
    "VN": "Vietnam",
    "SA": "Saudi Arabia",
    "ZA": "South Africa",
    "IT": "Italy",
}

# ---------------------------------------------------------------------------
# Region selector
# ---------------------------------------------------------------------------


def _select_region_interactive() -> str:
    """Display an interactive region picker and return the chosen ISO code."""
    region_list = list(_REGIONS.items())

    console.print()
    console.print(
        Panel.fit(
            "[header]✦  SELECT TARGET REGION[/header]",
            border_style="blue",
            padding=(0, 2),
        )
    )
    console.print()

    # Build a 3-column table of regions
    tbl = Table(
        show_header=False,
        box=None,
        padding=(0, 2),
        expand=False,
    )
    tbl.add_column(justify="right", style="number", no_wrap=True)
    tbl.add_column(style="region", no_wrap=True)
    tbl.add_column(style="subheader")

    for i, (code, name) in enumerate(region_list, start=1):
        tbl.add_row(f"[{i}]", code, name)

    # Split into 3 visual columns
    rows = region_list
    col_size = (len(rows) + 2) // 3
    cols_data: list[Table] = []
    for c in range(3):
        sub = Table(show_header=False, box=None, padding=(0, 2))
        sub.add_column(justify="right", style="number", no_wrap=True)
        sub.add_column(style="region", no_wrap=True)
        sub.add_column(style="subheader")
        for i in range(c * col_size, min((c + 1) * col_size, len(rows))):
            sub.add_row(f"[{i + 1}]", rows[i][0], rows[i][1])
        cols_data.append(sub)

    console.print(Columns(cols_data))
    console.print()
    console.print(
        "[hint]Enter a [prompt]number[/prompt] (1–{n}) "
        "or a [prompt]2-letter ISO code[/prompt] directly (e.g. ID, US, SG)[/hint]".format(
            n=len(region_list)
        )
    )
    console.print()

    while True:
        try:
            raw = console.input("[prompt]  > [/prompt]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            console.print("[warning]  Cancelled by user.[/warning]")
            sys.exit(0)

        if not raw:
            console.print("[error]  Input cannot be empty. Try again.[/error]")
            continue

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(region_list):
                code, name = region_list[idx]
                console.print(
                    f"\n  Region selected: [region]{name}[/region] "
                    f"[subheader]({code})[/subheader]\n"
                )
                return code
            console.print(f"[error]  Number out of range. Enter 1–{len(region_list)}.[/error]")
            continue

        upper = raw.upper()
        if upper in _REGIONS:
            console.print(
                f"\n  Region selected: [region]{_REGIONS[upper]}[/region] "
                f"[subheader]({upper})[/subheader]\n"
            )
            return upper

        if len(upper) == 2 and upper.isalpha():
            confirm = console.input(
                f"[warning]  '{upper}' is not in the preset list. Use it anyway? [[y/N]]: [/warning]"
            ).strip().lower()
            if confirm in ("y", "yes"):
                return upper
            continue

        console.print("[error]  Unrecognised input. Enter a number or a 2-letter ISO code.[/error]")


# ---------------------------------------------------------------------------
# CLI runner
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_market_intelligence",
        description=(
            "Fetch, filter, and persist trending market topics "
            "for YouTube Shorts content research."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py               # interactive region selector\n"
            "  python main.py --region US   # skip selector, use US\n"
            "  python main.py --region ID\n"
        ),
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="CC",
        help=(
            "ISO 3166-1 alpha-2 code (e.g. US, ID). "
            "If omitted, the interactive selector is shown."
        ),
    )
    return parser


def run_cli(use_case: TrendAnalyzerUseCase, default_region: str) -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    _print_header()

    # Resolve region
    if args.region:
        region = args.region.upper().strip()
        if len(region) != 2 or not region.isalpha():
            parser.error(f"Invalid region code '{region}'. Expected 2-letter ISO code.")
    else:
        region = _select_region_interactive()

    logger.info("CLI: starting pipeline for region='%s'.", region)

    # Run pipeline with a rich spinner
    results: list[TrendTopic] = []
    region_label = _REGIONS.get(region, "Custom Region")

    try:
        with Progress(
            SpinnerColumn(spinner_name="dots", style="cyan"),
            TextColumn(
                f"[white]Fetching trends for [/white][region]{region_label}[/region] "
                f"[subheader]({region})[/subheader][white] …[/white]"
            ),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("", total=None)
            results = use_case.execute(region=region)
            progress.stop_task(task)

    except RateLimitExceededError as exc:
        _exit_error(
            title="Rate Limit Exceeded",
            detail=exc.message,
            hint="Wait a few minutes and try again, or use a VPN/proxy.",
            code=2,
        )
    except DataExtractionError as exc:
        _exit_error(
            title="Data Extraction Failed",
            detail=exc.message,
            hint="Check your internet connection and verify the region code is supported.",
            code=3,
        )
    except StorageError as exc:
        _exit_error(
            title="Storage Failure",
            detail=exc.message,
            hint="Ensure data/ is writable and you have sufficient disk space.",
            code=4,
        )
    except AgentMarketIntelligenceError as exc:
        _exit_error(title="Unexpected Error", detail=exc.message, code=5)
    except KeyboardInterrupt:
        console.print()
        console.print("[warning]  Interrupted by user.[/warning]")
        sys.exit(0)

    _print_results(results, region)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _print_header() -> None:
    title = Text()
    title.append("✦  Market Intelligence", style="header")
    title.append("   by agent_market_intelligence", style="subheader")

    subtitle = Text("YouTube Shorts Trend Analyzer", style="subheader")

    console.print()
    console.print(
        Panel(
            f"{title}\n{subtitle}",
            border_style="blue",
            padding=(0, 2),
        )
    )


# ---------------------------------------------------------------------------
# Results table
# ---------------------------------------------------------------------------


def _print_results(topics: list[TrendTopic], region: str) -> None:
    region_name = _REGIONS.get(region, region)

    if not topics:
        console.print()
        console.print(
            Panel(
                f"[warning]⚠  No trends found for region '{region}'.[/warning]\n"
                "[hint]Try a different region or check your internet connection.[/hint]",
                border_style="yellow",
                title="[warning]No Results[/warning]",
                padding=(0, 2),
            )
        )
        console.print()
        return

    # Build results table
    tbl = Table(
        title=(
            f"[header]Trend Report[/header]  "
            f"[region]{region_name} ({region})[/region]  "
            f"[subheader]· {len(topics)} topics found[/subheader]"
        ),
        box=box.ROUNDED,
        border_style="blue",
        header_style="bold cyan",
        show_lines=True,
        expand=True,
        padding=(0, 1),
    )

    tbl.add_column("#", style="rank", justify="right", width=3, no_wrap=True)
    tbl.add_column("Topic", style="topic", min_width=20)
    tbl.add_column("Volume", justify="center", width=12)
    tbl.add_column("Status", justify="center", width=11)
    tbl.add_column("Content Angle", style="angle", min_width=30)

    for idx, topic in enumerate(topics, start=1):
        volume_bar = _volume_bar(topic.search_volume)
        volume_style = (
            "volume_high" if topic.search_volume >= 80
            else "volume_mid" if topic.search_volume >= 60
            else "volume_low"
        )
        volume_cell = Text()
        volume_cell.append(f"{topic.search_volume:>3}/100", style=volume_style)
        volume_cell.append(f"\n{volume_bar}")

        status = (
            Text("↑ Growing", style="growing")
            if topic.is_growing
            else Text("→ Stable", style="stable")
        )

        tbl.add_row(
            str(idx),
            topic.topic_name,
            volume_cell,
            status,
            topic.suggested_angle,
        )

    console.print()
    console.print(tbl)
    console.print()
    console.print(
        f"  [success]✓  {len(topics)} topic(s) saved to data/processed/[/success]"
    )
    console.print()


def _volume_bar(volume: int, width: int = 10) -> str:
    """Return a simple unicode progress bar string."""
    filled = round(volume / 100 * width)
    empty = width - filled
    return "█" * filled + "░" * empty


# ---------------------------------------------------------------------------
# Error display
# ---------------------------------------------------------------------------


def _exit_error(
    title: str,
    detail: str,
    hint: str = "",
    code: int = 1,
) -> None:
    logger.error("%s: %s", title, detail)
    body = f"[error]{detail}[/error]"
    if hint:
        body += f"\n\n[hint]💡 {hint}[/hint]"
    console.print()
    console.print(
        Panel(
            body,
            title=f"[error]✗  {title}[/error]",
            border_style="red",
            padding=(0, 2),
        )
    )
    console.print()
    sys.exit(code)