from __future__ import annotations

import sys
from typing import Literal

from rich.columns import Columns
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from src.interfaces.cli_components.theme import REGIONS, console

_Action = Literal["again", "change", "exit"]

# Preset pilihan jumlah topik yang ditampilkan sebagai shortcut
_TOP_N_PRESETS: list[int] = [3, 5, 10, 15, 20, 30]
_MIN_TOP_N: int = 1
_MAX_TOP_N: int = 50


def prompt_top_n(default: int = 10) -> int:
    """
    Tampilkan prompt interaktif untuk memilih jumlah topik yang dianalisis.
    Mendukung input angka preset (1-6) atau ketik langsung nilai custom.
    """
    console.print()
    console.print(
        Panel.fit(
            "[header]✦  PILIH JUMLAH TOPIK[/header]",
            border_style="blue",
            padding=(0, 2),
        )
    )
    console.print()

    # Tampilkan preset sebagai shortcut
    tbl = Table(show_header=False, box=None, padding=(0, 3))
    tbl.add_column(justify="right", style="number", no_wrap=True)
    tbl.add_column(style="topic", no_wrap=True)
    tbl.add_column(style="subheader")

    preset_labels = {
        3:  "Cepat — cocok untuk test",
        5:  "Ringkas — 5 topik terpanas",
        10: "Standar — rekomendasi harian",
        15: "Menengah",
        20: "Lengkap — analisis mendalam",
        30: "Ekstensif — semua tren penting",
    }
    for i, n in enumerate(_TOP_N_PRESETS, start=1):
        label = preset_labels.get(n, "")
        default_marker = "  ← default" if n == default else ""
        tbl.add_row(f"[{i}]", f"{n} topik", f"{label}{default_marker}")

    console.print(tbl)
    console.print()
    console.print(
        f"[hint]Ketik [prompt]nomor[/prompt] preset (1–{len(_TOP_N_PRESETS)}), "
        f"atau langsung ketik [prompt]angka[/prompt] ({_MIN_TOP_N}–{_MAX_TOP_N}), "
        f"atau tekan [prompt]Enter[/prompt] untuk default ([bold]{default}[/bold] topik)[/hint]"
    )
    console.print()

    while True:
        try:
            raw = console.input("[prompt]  > [/prompt]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            console.print("[warning]  Dibatalkan — menggunakan default.[/warning]")
            return default

        # Enter kosong → pakai default
        if not raw:
            console.print(
                f"\n  Menggunakan default: [bold]{default}[/bold] topik\n"
            )
            return default

        if raw.isdigit():
            val = int(raw)

            # Cek apakah ini nomor preset (1-6)
            if 1 <= val <= len(_TOP_N_PRESETS):
                chosen = _TOP_N_PRESETS[val - 1]
                console.print(
                    f"\n  Dipilih: [bold]{chosen}[/bold] topik "
                    f"[subheader](preset #{val})[/subheader]\n"
                )
                return chosen

            # Atau angka langsung dalam range yang valid
            if _MIN_TOP_N <= val <= _MAX_TOP_N:
                console.print(
                    f"\n  Dipilih: [bold]{val}[/bold] topik\n"
                )
                return val

            console.print(
                f"[error]  Angka di luar range.[/error] "
                f"[hint]Masukkan 1–{len(_TOP_N_PRESETS)} untuk preset, "
                f"atau {_MIN_TOP_N}–{_MAX_TOP_N} untuk angka langsung.[/hint]"
            )
            continue

        console.print(
            "[error]  Input tidak valid.[/error] "
            "[hint]Ketik angka atau tekan Enter untuk default.[/hint]"
        )


def prompt_action_menu(region: str) -> _Action:
    from src.interfaces.cli_components.display import print_action_menu
    print_action_menu(region)

    valid: dict[str, _Action] = {"r": "again", "c": "change", "e": "exit"}

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
            "[error]  Input tidak valid.[/error] "
            "[hint]Ketik [/hint][prompt]R[/prompt][hint], "
            "[/hint][prompt]C[/prompt][hint], atau "
            "[/hint][prompt]E[/prompt][hint].[/hint]"
        )


def select_region_interactive() -> str:
    region_list = list(REGIONS.items())

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
        "[hint]Ketik [prompt]nomor[/prompt] (1–{n}) "
        "atau [prompt]kode ISO 2 huruf[/prompt] langsung "
        "(contoh: ID, US, SG)[/hint]".format(n=len(region_list))
    )
    console.print()

    while True:
        try:
            raw = console.input("[prompt]  > [/prompt]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            console.print("[warning]  Dibatalkan oleh user.[/warning]")
            sys.exit(0)

        if not raw:
            console.print("[error]  Input tidak boleh kosong. Coba lagi.[/error]")
            continue

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(region_list):
                code, name = region_list[idx]
                console.print(
                    f"\n  Dipilih: [region]{name}[/region] "
                    f"[subheader]({code})[/subheader]\n"
                )
                return code
            console.print(
                f"[error]  Nomor di luar range.[/error] "
                f"[hint]Masukkan 1–{len(region_list)}.[/hint]"
            )
            continue

        upper = raw.upper()
        if upper in REGIONS:
            console.print(
                f"\n  Dipilih: [region]{REGIONS[upper]}[/region] "
                f"[subheader]({upper})[/subheader]\n"
            )
            return upper

        if len(upper) == 2 and upper.isalpha():
            try:
                confirm = console.input(
                    f"[warning]  '{upper}' tidak ada di daftar preset. "
                    f"Gunakan tetap? [[y/N]]: [/warning]"
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                console.print()
                sys.exit(0)
            if confirm in ("y", "yes"):
                return upper
            continue

        console.print(
            "[error]  Input tidak dikenal.[/error] "
            "[hint]Masukkan nomor atau kode ISO 2 huruf.[/hint]"
        )