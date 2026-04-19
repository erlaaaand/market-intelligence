from __future__ import annotations

import json
import random
import re
from typing import Final

# ── Source name ───────────────────────────────────────────────────────────────
SOURCE_NAME: Final[str] = "google_trends"

# ── Endpoint URLs ─────────────────────────────────────────────────────────────
TRENDING_RSS_URL: Final[str] = "https://trends.google.com/trending/rss"
EXPLORE_URL: Final[str] = "https://trends.google.com/trends/api/explore"
MULTILINE_URL: Final[str] = "https://trends.google.com/trends/api/widgetdata/multiline"

# ── Polite-sleep range ────────────────────────────────────────────────────────
MIN_SLEEP: Final[float] = 1.8
MAX_SLEEP: Final[float] = 4.2
JITTER: Final[float] = 2.5

# ── Rotating User-Agents ──────────────────────────────────────────────────────
USER_AGENTS: Final[list[str]] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.6367.118 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.6312.122 Safari/537.36 Edg/123.0.2420.97"
    ),
]

# ── Seed keywords for Tier-3 fallback ────────────────────────────────────────
SEED_KEYWORDS: Final[list[str]] = [
    "AI tools 2025", "viral life hacks", "money making online",
    "fitness motivation", "cooking recipes easy", "travel destinations",
    "productivity tips", "crypto news", "mental health tips",
    "smartphone review", "fashion trends", "gaming highlights",
    "home decor ideas", "relationship advice", "study motivation",
    "electric vehicle", "python tutorial", "remote work tips",
    "investing for beginners", "healthy recipes",
]

# ── ISO → pytrends country-name mapping ──────────────────────────────────────
ISO_TO_PYTRENDS: Final[dict[str, str]] = {
    "AR": "argentina",     "AT": "austria",        "AU": "australia",
    "BD": "bangladesh",    "BE": "belgium",         "BR": "brazil",
    "CA": "canada",        "CH": "switzerland",     "CL": "chile",
    "CO": "colombia",      "CZ": "czech_republic",  "DE": "germany",
    "DK": "denmark",       "EG": "egypt",           "ES": "spain",
    "FI": "finland",       "FR": "france",          "GB": "united_kingdom",
    "GH": "ghana",         "GR": "greece",          "HK": "hong_kong",
    "HU": "hungary",       "ID": "indonesia",       "IL": "israel",
    "IN": "india",         "IT": "italy",           "JP": "japan",
    "KE": "kenya",         "KR": "south_korea",     "MX": "mexico",
    "MY": "malaysia",      "NG": "nigeria",         "NL": "netherlands",
    "NO": "norway",        "NZ": "new_zealand",     "PE": "peru",
    "PH": "philippines",   "PK": "pakistan",        "PL": "poland",
    "PT": "portugal",      "RO": "romania",         "RU": "russia",
    "SA": "saudi_arabia",  "SE": "sweden",          "SG": "singapore",
    "TH": "thailand",      "TR": "turkey",          "TW": "taiwan",
    "UA": "ukraine",       "US": "united_states",   "VE": "venezuela",
    "VN": "vietnam",       "ZA": "south_africa",
}

# ── Pure helper functions ─────────────────────────────────────────────────────

def score_from_rank(rank: int, total: int) -> int:
    if total <= 1:
        return 100
    return max(1, round(100 * (1 - rank / total)))


def strip_xssi(text: str) -> str:
    for prefix in (")]}',\n", ")]}'\n", ")]}',", ")]}'\n\n"):
        if text.startswith(prefix):
            return text[len(prefix):]
    match = re.search(r"[{\[]", text)
    return text[match.start():] if match else text


def json_compact(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"))


def json_loads_xssi(text: str) -> dict[str, object]:
    return json.loads(strip_xssi(text))


def random_ua() -> str:
    return random.choice(USER_AGENTS)