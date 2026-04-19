from __future__ import annotations

from rich import box
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from src.core.entities import MarketAnalysisReport
from src.interfaces.cli_components.theme import (
    LIFECYCLE_STYLES,
    REGIONS,
    console,
)


def print_header() -> None:
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


def print_goodbye() -> None:
    console.print()
    console.print(
        Panel.fit(
            "[header]✦  Session ended. Happy creating![/header]",
            border_style="blue",
            padding=(0, 2),
        )
    )
    console.print()


def print_error(title: str, detail: str, hint: str = "") -> None:
    import logging
    logging.getLogger(__name__).error("%s: %s", title, detail)
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


def print_results(report: MarketAnalysisReport | None, region: str) -> None:
    region_name = REGIONS.get(region, region)

    # FIX: `report.market_trends` → `report.documents`
    if report is None or not report.documents:
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

    # FIX: `report.market_trends` → `report.documents`
    topics = report.documents

    tbl = Table(
        title=(
            f"[header]Trend Report[/header]  "
            f"[region]{region_name} ({region})[/region]  "
            # FIX: `report.metadata.date` → `report.date`
            f"[subheader]· {len(topics)} topic(s) found · {report.date}[/subheader]"
        ),
        box=box.ROUNDED,
        border_style="blue",
        header_style="bold cyan",
        show_lines=True,
        expand=True,
        padding=(0, 1),
    )

    tbl.add_column("#",           style="rank",  justify="right", width=3,  no_wrap=True)
    tbl.add_column("Topic",       style="topic",                  min_width=20)
    tbl.add_column("Momentum",                   justify="center", width=12)
    tbl.add_column("Lifecycle",                  justify="center", width=12)
    tbl.add_column("Key Drivers", style="angle",                  min_width=30)

    for idx, topic in enumerate(topics, start=1):
        # FIX: `topic.metrics.momentum_score` → `topic.trend_identity.metrics.momentum_score`
        momentum = topic.trend_identity.metrics.momentum_score
        momentum_style = (
            "volume_high" if momentum >= 80
            else "volume_mid" if momentum >= 60
            else "volume_low"
        )
        momentum_cell = Text()
        momentum_cell.append(f"{momentum:>5.1f}/100", style=momentum_style)
        momentum_cell.append(f"\n{_volume_bar(int(momentum))}")

        # FIX: `topic.analysis.lifecycle_stage.value` → `topic.trend_identity.metrics.lifecycle_stage.value`
        lifecycle_val = topic.trend_identity.metrics.lifecycle_stage.value
        lifecycle_style = LIFECYCLE_STYLES.get(lifecycle_val, "subheader")

        # FIX: `topic.analysis.key_drivers[:3]` → `topic.creative_brief.recommended_angles[:3]`
        drivers_text = "\n".join(f"• {d}" for d in topic.creative_brief.recommended_angles[:3])

        tbl.add_row(
            str(idx),
            # FIX: `topic.topic` → `topic.trend_identity.topic`
            topic.trend_identity.topic,
            momentum_cell,
            Text(lifecycle_val, style=lifecycle_style),
            drivers_text,
        )

    console.print()
    console.print(tbl)
    console.print()
    console.print(
        f"  [success]✓  {len(topics)} topic(s) saved to "
        # FIX: `report.metadata.date` → `report.date`
        f"data/processed/{region}/{report.date}/[/success]"
    )
    console.print()


def print_action_menu(region: str) -> None:
    region_name = REGIONS.get(region, region)
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


def _volume_bar(volume: int, width: int = 10) -> str:
    filled = round(volume / 100 * width)
    return "█" * filled + "░" * (width - filled)