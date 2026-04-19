from __future__ import annotations

import sys
from typing import Literal

from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table

from src.interfaces.cli_components.theme import REGIONS, console

_Action = Literal["again", "change", "exit"]


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
            "[error]  Invalid input.[/error] "
            "[hint]Enter [/hint][prompt]R[/prompt][hint], "
            "[/hint][prompt]C[/prompt][hint], or "
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
        if upper in REGIONS:
            console.print(
                f"\n  Selected: [region]{REGIONS[upper]}[/region] "
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