from __future__ import annotations

from typing import Final

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
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

console = Console(theme=THEME, highlight=False)

REGIONS: Final[dict[str, str]] = {
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

LIFECYCLE_STYLES: Final[dict[str, str]] = {
    "Emerging": "lifecycle_emerging",
    "Trending": "lifecycle_trending",
    "Peak": "lifecycle_peak",
    "Stagnant": "lifecycle_stagnant",
    "Declining": "lifecycle_declining",
}