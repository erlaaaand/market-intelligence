from __future__ import annotations

import argparse
import logging
import sys

from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import MarketAnalysisReport
from src.core.exceptions import (
    AgentMarketIntelligenceError,
    DataExtractionError,
    LLMAnalysisError,
    RateLimitExceededError,
    StorageError,
)
from src.interfaces.cli_components.display import (
    print_error,
    print_goodbye,
    print_header,
    print_results,
)
from src.interfaces.cli_components.hardware_monitor import HardwareMonitorColumn
from src.interfaces.cli_components.prompts import (
    prompt_action_menu,
    select_region_interactive,
)
from src.interfaces.cli_components.theme import REGIONS, console

logger = logging.getLogger(__name__)


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

    print_header()

    if args.region:
        region = args.region.upper().strip()
        if len(region) != 2 or not region.isalpha():
            parser.error(
                f"Invalid region code '{region}'. Expected a 2-letter ISO code."
            )
    else:
        region = select_region_interactive()

    while True:
        logger.info("CLI: starting pipeline for region='%s'.", region)

        report = _run_pipeline(use_case, region)
        print_results(report, region)

        action = prompt_action_menu(region)

        if action == "exit":
            print_goodbye()
            sys.exit(0)

        if action == "change":
            region = select_region_interactive()


def _run_pipeline(
    use_case: TrendAnalyzerUseCase, region: str
) -> MarketAnalysisReport | None:
    region_label = REGIONS.get(region, "Custom Region")

    try:
        with Progress(
            SpinnerColumn(spinner_name="dots", style="cyan"),
            TextColumn(
                f"[white]Fetching trends for [/white]"
                f"[region]{region_label}[/region] "
                f"[subheader]({region})[/subheader][white] …[/white]"
            ),
            TimeElapsedColumn(),
            HardwareMonitorColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("", total=None)
            report = use_case.execute(region=region)
            progress.stop_task(task)

        return report

    except RateLimitExceededError as exc:
        print_error(
            title="Rate Limit Exceeded",
            detail=exc.message,
            hint="Wait a few minutes and try again, or switch to a different region.",
        )
    except DataExtractionError as exc:
        print_error(
            title="Data Extraction Failed",
            detail=exc.message,
            hint=(
                "Check your internet connection and verify the region code "
                "is supported by the active provider."
            ),
        )
    except LLMAnalysisError as exc:
        print_error(
            title="LLM Analysis Failed",
            detail=exc.message,
            hint=(
                "Ensure Ollama is running and the model is available. "
                "Set LLM_PROVIDER=mock in .env to use offline mode."
            ),
        )
    except StorageError as exc:
        print_error(
            title="Storage Failure",
            detail=exc.message,
            hint="Ensure data/ is writable and you have sufficient disk space.",
        )
    except AgentMarketIntelligenceError as exc:
        print_error(title="Unexpected Error", detail=exc.message)
    except KeyboardInterrupt:
        console.print()
        console.print("[warning]  Interrupted — returning to menu.[/warning]")

    return None