from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Final, Literal

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import MarketAnalysisReport, TrendTopic
from src.core.exceptions import (
    AgentMarketIntelligenceError,
    DataExtractionError,
    LLMAnalysisError,
    RateLimitExceededError,
    StorageError,
)

logger = logging.getLogger(__name__)

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
        "lifecycle_emerging": "bold green",
        "lifecycle_trending": "bold bright_green",
        "lifecycle_peak": "bold red",
        "lifecycle_stagnant": "yellow",
        "lifecycle_declining": "dim red",
        "angle": "magenta",
        "success": "bold green",
        "error": "bold red",
        "warning": "yellow",
        "hint": "dim white",
        "divider": "dim blue",
        "number": "bold bright_blue",
        "prompt": "bold cyan",
        "menu_key": "bold bright_green",
        "menu_label": "white",
        "menu_exit": "bold red",
    }
)

console = Console(theme=_THEME, highlight=False)

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

_Action = Literal["again", "change", "exit"]

_LIFECYCLE_STYLES: Final[dict[str, str]] = {
    "Emerging": "lifecycle_emerging",
    "Trending": "lifecycle_trending",
    "Peak": "lifecycle_peak",
    "Stagnant": "lifecycle_stagnant",
    "Declining": "lifecycle_declining",
}


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
            "  python main.py --region US   # skip selector, analyse US\n"
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

    if args.region:
        region = args.region.upper().strip()
        if len(region) != 2 or not region.isalpha():
            parser.error(
                f"Invalid region code '{region}'. Expected a 2-letter ISO code."
            )
    else:
        region = _select_region_interactive()

    while True:
        logger.info("CLI: starting pipeline for region='%s'.", region)

        report = _run_pipeline(use_case, region)
        _print_results(report, region)

        action = _prompt_action_menu(region)

        if action == "exit":
            _print_goodbye()
            sys.exit(0)

        if action == "change":
            region = _select_region_interactive()


def _run_pipeline(
    use_case: TrendAnalyzerUseCase, region: str
) -> MarketAnalysisReport | None:
    region_label = _REGIONS.get(region, "Custom Region")

    try:
        with Progress(
            SpinnerColumn(spinner_name="dots", style="cyan"),
            TextColumn(
                f"[white]Fetching trends for [/white]"
                f"[region]{region_label}[/region] "
                f"[subheader]({region})[/subheader][white] …[/white]"
            ),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("", total=None)
            report = use_case.execute(region=region)
            progress.stop_task(task)

        return report

    except RateLimitExceededError as exc:
        _print_error(
            title="Rate Limit Exceeded",
            detail=exc.message,
            hint="Wait a few minutes and try again, or switch to a different region.",
        )
    except DataExtractionError as exc:
        _print_error(
            title="Data Extraction Failed",
            detail=exc.message,
            hint=(
                "Check your internet connection and verify the region code "
                "is supported by the active provider."
            ),
        )
    except LLMAnalysisError as exc:
        _print_error(
            title="LLM Analysis Failed",
            detail=exc.message,
            hint=(
                "Ensure Ollama is running and the model is available. "
                "Set LLM_PROVIDER=mock in .env to use offline mode."
            ),
        )
    except StorageError as exc:
        _print_error(
            title="Storage Failure",
            detail=exc.message,
            hint="Ensure data/ is writable and you have sufficient disk space.",
        )
    except AgentMarketIntelligenceError as exc:
        _print_error(title="Unexpected Error", detail=exc.message)
    except KeyboardInterrupt:
        console.print()
        console.print("[warning]  Interrupted — returning to menu.[/warning]")

    return None


def _prompt_action_menu(region: str) -> _Action:
    region_name = _REGIONS.get(region, region)

    console.print(Rule(style="dim blue"))
    console.print()
    console.print("  [subheader]What would you like to do next?[/subheader]")
    console.print()
    console.print(
        f"  [menu_key][ R ][/menu_key]  [menu_label]Run again[/menu_label]"
        f"  [hint]— fetch trends for {region_name} ({region}) again[/hint]"
    )
    console.print(
        "  [menu_key][ C ][/menu_key]  [menu_label]Change region[/menu_label]"
        "  [hint]— select a different country[/hint]"
    )
    console.print(
        "  [menu_exit][ E ][/menu_exit]  [menu_label]Exit[/menu_label]"
        "  [hint]— quit the program[/hint]"
    )
    console.print()

    valid: dict[str, _Action] = {
        "r": "again",
        "c": "change",
        "e": "exit",
    }

    while True:
        try:
            raw = console.input("[prompt]  > [/prompt]").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return "exit"

        if raw in valid:
            console.print()
            return valid[raw]

        console.print(
            "[error]  Invalid input.[/error] "
            "[hint]Enter [/hint][prompt]R[/prompt][hint], "
            "[/hint][prompt]C[/prompt][hint], or "
            "[/hint][prompt]E[/prompt][hint].[/hint]"
        )


def _select_region_interactive() -> str:
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

    col_size = (len(region_list) + 2) // 3
    cols_data: list[Table] = []
    for c in range(3):
        sub = Table(show_header=False, box=None, padding=(0, 2))
        sub.add_column(justify="right", style="number", no_wrap=True)
        sub.add_column(style="region", no_wrap=True)
        sub.add_column(style="subheader")
        for i in range(c * col_size, min((c + 1) * col_size, len(region_list))):
            code, name = region_list[i]
            sub.add_row(f"[{i + 1}]", code, name)
        cols_data.append(sub)

    console.print(Columns(cols_data))
    console.print()
    console.print(
        "[hint]Enter a [prompt]number[/prompt] (1–{n}) "
        "or a [prompt]2-letter ISO code[/prompt] directly "
        "(e.g. ID, US, SG)[/hint]".format(n=len(region_list))
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
                    f"\n  Selected: [region]{name}[/region] "
                    f"[subheader]({code})[/subheader]\n"
                )
                return code
            console.print(
                f"[error]  Number out of range.[/error] "
                f"[hint]Enter 1–{len(region_list)}.[/hint]"
            )
            continue

        upper = raw.upper()
        if upper in _REGIONS:
            console.print(
                f"\n  Selected: [region]{_REGIONS[upper]}[/region] "
                f"[subheader]({upper})[/subheader]\n"
            )
            return upper

        if len(upper) == 2 and upper.isalpha():
            try:
                confirm = console.input(
                    f"[warning]  '{upper}' is not in the preset list. "
                    f"Use it anyway? [[y/N]]: [/warning]"
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                console.print()
                sys.exit(0)
            if confirm in ("y", "yes"):
                return upper
            continue

        console.print(
            "[error]  Unrecognised input.[/error] "
            "[hint]Enter a number or a 2-letter ISO code.[/hint]"
        )


def _print_results(report: MarketAnalysisReport | None, region: str) -> None:
    region_name = _REGIONS.get(region, region)

    if report is None or not report.market_trends:
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

    topics = report.market_trends

    tbl = Table(
        title=(
            f"[header]Trend Report[/header]  "
            f"[region]{region_name} ({region})[/region]  "
            f"[subheader]· {len(topics)} topic(s) found · {report.metadata.date}[/subheader]"
        ),
        box=box.ROUNDED,
        border_style="blue",
        header_style="bold cyan",
        show_lines=True,
        expand=True,
        padding=(0, 1),
    )

    tbl.add_column("#",             style="rank",  justify="right", width=3,  no_wrap=True)
    tbl.add_column("Topic",         style="topic",                  min_width=20)
    tbl.add_column("Momentum",                     justify="center", width=12)
    tbl.add_column("Lifecycle",                    justify="center", width=12)
    tbl.add_column("Key Drivers",   style="angle",                  min_width=30)

    for idx, topic in enumerate(topics, start=1):
        momentum = topic.metrics.momentum_score
        momentum_style = (
            "volume_high" if momentum >= 80
            else "volume_mid" if momentum >= 60
            else "volume_low"
        )
        momentum_cell = Text()
        momentum_cell.append(f"{momentum:>5.1f}/100", style=momentum_style)
        momentum_cell.append(f"\n{_volume_bar(int(momentum))}")

        lifecycle_val = topic.analysis.lifecycle_stage.value
        lifecycle_style = _LIFECYCLE_STYLES.get(lifecycle_val, "subheader")
        lifecycle_cell = Text(lifecycle_val, style=lifecycle_style)

        drivers_text = "\n".join(f"• {d}" for d in topic.analysis.key_drivers[:3])

        tbl.add_row(
            str(idx),
            topic.topic,
            momentum_cell,
            lifecycle_cell,
            drivers_text,
        )

    console.print()
    console.print(tbl)
    console.print()
    console.print(
        f"  [success]✓  {len(topics)} topic(s) saved to "
        f"data/processed/{region}/{report.metadata.date}/[/success]"
    )
    console.print()


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


def _print_goodbye() -> None:
    console.print()
    console.print(
        Panel.fit(
            "[header]✦  Session ended. Happy creating![/header]",
            border_style="blue",
            padding=(0, 2),
        )
    )
    console.print()


def _print_error(title: str, detail: str, hint: str = "") -> None:
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


def _volume_bar(volume: int, width: int = 10) -> str:
    filled = round(volume / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _today_label() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")