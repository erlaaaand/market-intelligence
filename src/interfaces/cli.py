from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import itertools

from src.application.trend_analyzer import TrendAnalyzerUseCase
from src.core.entities import TrendTopic
from src.core.exceptions import (
    AgentMarketIntelligenceError,
    DataExtractionError,
    RateLimitExceededError,
    StorageError,
)

logger = logging.getLogger(__name__)

_REGIONS: dict[str, str] = {
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
}

_DIVIDER_OUTER = "═" * 64
_DIVIDER_INNER = "─" * 64


class _Ansi:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GRAY    = "\033[90m"
    BG_BLUE = "\033[44m"

    @staticmethod
    def supports() -> bool:
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    @classmethod
    def c(cls, text: str, *codes: str) -> str:
        if not cls.supports():
            return text
        return "".join(codes) + text + cls.RESET


C = _Ansi()


class _Spinner:
    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str) -> None:
        self._message = message
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        for frame in itertools.cycle(self._FRAMES):
            if self._stop_event.is_set():
                break
            sys.stdout.write(
                f"\r  {C.c(frame, C.CYAN, C.BOLD)}  {C.c(self._message, C.WHITE)}   "
            )
            sys.stdout.flush()
            time.sleep(0.08)

    def __enter__(self) -> "_Spinner":
        if C.supports():
            self._thread.start()
        else:
            print(f"  ... {self._message}")
        return self

    def __exit__(self, *_) -> None:
        self._stop_event.set()
        if C.supports() and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if C.supports():
            sys.stdout.write("\r" + " " * 72 + "\r")
            sys.stdout.flush()


def _select_region_interactive() -> str:
    region_list = list(_REGIONS.items())

    print()
    print(C.c(_DIVIDER_OUTER, C.BLUE))
    print(C.c("  ✦  PILIH REGION TARGET", C.BOLD, C.WHITE))
    print(C.c(_DIVIDER_INNER, C.GRAY))
    print()

    cols = 3
    for i, (code, name) in enumerate(region_list):
        num_badge = C.c(f"  [{i + 1:>2}]", C.CYAN, C.BOLD)
        code_str  = C.c(f" {code}", C.YELLOW, C.BOLD)
        name_str  = C.c(f" {name}", C.WHITE)
        end       = "\n" if (i + 1) % cols == 0 or (i + 1) == len(region_list) else ""
        print(f"{num_badge}{code_str} {name_str:<22}", end=end)

    print()
    print(C.c(_DIVIDER_INNER, C.GRAY))
    print(
        C.c("  Ketik ", C.GRAY)
        + C.c("nomor", C.CYAN, C.BOLD)
        + C.c(" (1-", C.GRAY)
        + C.c(str(len(region_list)), C.CYAN)
        + C.c(") atau ", C.GRAY)
        + C.c("kode ISO", C.YELLOW, C.BOLD)
        + C.c(" langsung (contoh: ID, US, SG)", C.GRAY)
    )
    print()

    while True:
        try:
            raw = input(C.c("  > ", C.CYAN, C.BOLD)).strip()
        except (KeyboardInterrupt, EOFError):
            print()
            print(C.c("\n  Dibatalkan oleh user.", C.YELLOW))
            sys.exit(0)

        if not raw:
            print(C.c("  Input tidak boleh kosong. Coba lagi.", C.RED))
            continue

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(region_list):
                code, name = region_list[idx]
                print(
                    C.c(f"\n  Region dipilih: ", C.GRAY)
                    + C.c(f"{name} ({code})", C.GREEN, C.BOLD)
                    + "\n"
                )
                return code
            else:
                print(C.c(f"  Nomor tidak valid. Masukkan 1-{len(region_list)}.", C.RED))
                continue

        upper = raw.upper()
        if upper in _REGIONS:
            name = _REGIONS[upper]
            print(
                C.c(f"\n  Region dipilih: ", C.GRAY)
                + C.c(f"{name} ({upper})", C.GREEN, C.BOLD)
                + "\n"
            )
            return upper

        if len(upper) == 2 and upper.isalpha():
            confirm = input(
                C.c(f"  Kode '{upper}' tidak ada di daftar. Tetap gunakan? [y/N]: ", C.YELLOW)
            ).strip().lower()
            if confirm in ("y", "yes"):
                return upper
            continue

        print(C.c("  Input tidak dikenali. Masukkan nomor atau kode ISO 2 huruf.", C.RED))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_market_intelligence",
        description="Fetch, filter, and persist trending market topics for YouTube Shorts research.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py               # interactive region selector\n"
            "  python main.py --region US   # skip selector, langsung US\n"
            "  python main.py --region ID\n"
        ),
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        metavar="CC",
        help="ISO 3166-1 alpha-2 code (e.g. US, ID). Jika tidak disertakan, akan muncul selector interaktif.",
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
                f"Invalid region code '{region}'. Expected 2-letter ISO code."
            )
    else:
        region = _select_region_interactive()

    logger.info("CLI: starting pipeline for region='%s'.", region)

    results: list[TrendTopic] = []
    try:
        spinner_msg = (
            f"Mengambil trend untuk "
            + C.c(region, C.CYAN, C.BOLD)
            + C.c(f" ({_REGIONS.get(region, 'Custom Region')})", C.GRAY)
            + C.c(" ...", C.WHITE)
        )
        with _Spinner(spinner_msg):
            results = use_case.execute(region=region)

    except RateLimitExceededError as exc:
        _clear_line()
        _exit_error(
            "Rate Limit Exceeded", exc.message,
            hint="Tunggu beberapa menit lalu coba lagi, atau gunakan VPN.",
            code=2,
        )
    except DataExtractionError as exc:
        _clear_line()
        _exit_error(
            "Data Extraction Failed", exc.message,
            hint="Cek koneksi internet dan pastikan region code didukung.",
            code=3,
        )
    except StorageError as exc:
        _clear_line()
        _exit_error(
            "Storage Failure", exc.message,
            hint="Pastikan folder data/ bisa ditulis dan disk masih punya ruang.",
            code=4,
        )
    except AgentMarketIntelligenceError as exc:
        _clear_line()
        _exit_error("Unexpected Error", exc.message, code=5)

    _print_results(results, region)


def _print_header() -> None:
    print()
    print(C.c(_DIVIDER_OUTER, C.BLUE))
    print(C.c("  ✦  Market Intelligence", C.BOLD, C.WHITE) + "  " + C.c("by agent_market_intelligence", C.GRAY))
    print(C.c("     YouTube Shorts Trend Analyzer", C.GRAY))
    print(C.c(_DIVIDER_OUTER, C.BLUE))


def _clear_line() -> None:
    if C.supports():
        sys.stdout.write("\r" + " " * 72 + "\r")
        sys.stdout.flush()


def _print_results(topics: list[TrendTopic], region: str) -> None:
    region_name = _REGIONS.get(region, region)

    if not topics:
        print()
        print(C.c("  ⚠  Tidak ada trend yang ditemukan", C.YELLOW, C.BOLD) + C.c(f" untuk region '{region}'.", C.YELLOW))
        print(C.c("     Coba region lain atau periksa koneksi internet.", C.GRAY))
        print()
        return

    print(C.c(_DIVIDER_OUTER, C.BLUE))
    print(
        C.c("  ✦  TREND REPORT", C.BOLD, C.WHITE)
        + "  "
        + C.c(f"{region_name} ({region})", C.CYAN, C.BOLD)
        + "  "
        + C.c(f"· {len(topics)} topik ditemukan", C.GREEN)
    )
    print(C.c(_DIVIDER_OUTER, C.BLUE))

    for idx, topic in enumerate(topics, start=1):
        _print_topic_card(idx, topic)

    print(C.c(_DIVIDER_OUTER, C.BLUE))
    print(C.c(f"  ✓  {len(topics)} topik disimpan ke data/processed/", C.GREEN, C.BOLD))
    print(C.c(_DIVIDER_OUTER, C.BLUE))
    print()


def _print_topic_card(idx: int, topic: TrendTopic) -> None:
    growth_icon = (
        C.c("↑ Growing", C.GREEN, C.BOLD) if topic.is_growing
        else C.c("→ Stable", C.YELLOW)
    )
    volume_bar  = _volume_bar(topic.search_volume)
    volume_num  = C.c(f"{topic.search_volume:>3}/100", C.WHITE, C.BOLD)
    rank_badge  = C.c(f"  {idx:>2}.", C.CYAN, C.BOLD)
    name        = C.c(topic.topic_name, C.WHITE, C.BOLD)
    angle_label = C.c("       Angle  :", C.GRAY)
    angle_text  = C.c(topic.suggested_angle, C.MAGENTA)
    divider     = C.c("  " + _DIVIDER_INNER, C.GRAY)

    print()
    print(f"{rank_badge}  {name}")
    print(f"       Volume : {volume_num}  {volume_bar}  {growth_icon}")
    print(f"{angle_label} {angle_text}")
    print(divider)


def _volume_bar(volume: int, width: int = 20) -> str:
    filled = round(volume / 100 * width)
    empty  = width - filled
    color  = C.RED if volume >= 80 else (C.YELLOW if volume >= 60 else C.CYAN)
    return "[" + C.c("█" * filled, color) + C.c("░" * empty, C.GRAY) + "]"


def _exit_error(label: str, detail: str, hint: str = "", code: int = 1) -> None:
    logger.error("%s: %s", label, detail)
    print()
    print(C.c(f"  ✗  {label}", C.RED, C.BOLD))
    print(C.c(f"     {detail}", C.WHITE))
    if hint:
        print(C.c(f"     💡 {hint}", C.GRAY))
    print()
    sys.exit(code)