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
    prompt_top_n,
    select_region_interactive,
)
from src.interfaces.cli_components.theme import REGIONS, console

logger = logging.getLogger(__name__)

# Batas wajar untuk jumlah topik
_MIN_TOP_N: int = 1
_MAX_TOP_N: int = 50


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
            "  python main.py                        # interactive (region + jumlah topik)\n"
            "  python main.py --region US            # skip selector, 10 topik default\n"
            "  python main.py --region ID --top-n 5  # 5 topik untuk Indonesia\n"
            "  python main.py --region ID --top-n 20 # 20 topik untuk Indonesia\n"
        ),
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="CC",
        help=(
            "ISO 3166-1 alpha-2 code (e.g. US, ID). "
            "Jika tidak diisi, interactive selector akan muncul."
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        metavar="N",
        dest="top_n",
        help=(
            f"Jumlah topik yang dianalisis ({_MIN_TOP_N}–{_MAX_TOP_N}). "
            "Jika tidak diisi, program akan menanya secara interaktif."
        ),
    )
    return parser


def run_cli(use_case: TrendAnalyzerUseCase, default_region: str, default_top_n: int) -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    print_header()

    # ── Tentukan region ───────────────────────────────────────────────
    if args.region:
        region = args.region.upper().strip()
        if len(region) != 2 or not region.isalpha():
            parser.error(
                f"Region code '{region}' tidak valid. Gunakan kode ISO 2 huruf."
            )
    else:
        region = select_region_interactive()

    # ── Tentukan top_n ────────────────────────────────────────────────
    if args.top_n is not None:
        top_n = args.top_n
        if not (_MIN_TOP_N <= top_n <= _MAX_TOP_N):
            parser.error(
                f"--top-n harus antara {_MIN_TOP_N} dan {_MAX_TOP_N}, got {top_n}."
            )
    else:
        top_n = prompt_top_n(default=default_top_n)

    while True:
        logger.info(
            "CLI: memulai pipeline  region='%s'  top_n=%d.", region, top_n
        )

        report = _run_pipeline(use_case, region, top_n)
        print_results(report, region)

        action = prompt_action_menu(region)

        if action == "exit":
            print_goodbye()
            sys.exit(0)

        if action == "change":
            region = select_region_interactive()
            top_n = prompt_top_n(default=top_n)  # tanya lagi, default = nilai sebelumnya

        # action == "again": loop dengan region & top_n yang sama


def _run_pipeline(
    use_case: TrendAnalyzerUseCase,
    region: str,
    top_n: int,
) -> MarketAnalysisReport | None:
    region_label = REGIONS.get(region, "Custom Region")

    try:
        with Progress(
            SpinnerColumn(spinner_name="dots", style="cyan"),
            TextColumn(
                f"[white]Fetching [bold]{top_n}[/bold] trends for [/white]"
                f"[region]{region_label}[/region] "
                f"[subheader]({region})[/subheader][white] …[/white]"
            ),
            TimeElapsedColumn(),
            HardwareMonitorColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("", total=None)
            report = use_case.execute(region=region, top_n=top_n)
            progress.stop_task(task)

        return report

    except RateLimitExceededError as exc:
        print_error(
            title="Rate Limit Exceeded",
            detail=exc.message,
            hint="Tunggu beberapa menit dan coba lagi, atau ganti region.",
        )
    except DataExtractionError as exc:
        print_error(
            title="Data Extraction Failed",
            detail=exc.message,
            hint=(
                "Periksa koneksi internet dan pastikan region didukung "
                "oleh provider yang aktif."
            ),
        )
    except LLMAnalysisError as exc:
        print_error(
            title="LLM Analysis Failed",
            detail=exc.message,
            hint=(
                "Pastikan Ollama berjalan dan model tersedia. "
                "Set LLM_PROVIDER=mock di .env untuk mode offline."
            ),
        )
    except StorageError as exc:
        print_error(
            title="Storage Failure",
            detail=exc.message,
            hint="Pastikan direktori data/ bisa ditulis dan disk tidak penuh.",
        )
    except AgentMarketIntelligenceError as exc:
        print_error(title="Unexpected Error", detail=exc.message)
    except KeyboardInterrupt:
        console.print()
        console.print("[warning]  Interrupted — kembali ke menu.[/warning]")

    return None