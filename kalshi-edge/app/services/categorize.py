"""Map Kalshi categories to the app's boards."""
from __future__ import annotations

import re
from typing import Optional, Tuple

CATEGORIES = ["Props", "Sports", "Economics", "Commodities and Energy", "Weather", "Politics and Policy",
              "Culture", "Companies", "Other"]

_MAP = {
    "sports": "Sports",
    "economics": "Economics",
    "financials": "Economics",
    "financial": "Economics",
    "climate and weather": "Weather",
    "weather": "Weather",
    "climate": "Weather",
    "politics": "Politics and Policy",
    "elections": "Politics and Policy",
    "world": "Politics and Policy",
    "policy": "Politics and Policy",
    "entertainment": "Culture",
    "culture": "Culture",
    "social": "Culture",
    "mentions": "Culture",
    "companies": "Companies",
    "science and technology": "Companies",
    "tech": "Companies",
    "commodities": "Commodities and Energy",
    "energy": "Commodities and Energy",
}

_COMMODITY = re.compile(
    r"(?i)(gas price|gasoline|\bAAA\b|\boil\b|\bWTI\b|brent|crude|natural gas|\bgold\b|\bsilver\b|copper|"
    r"platinum|palladium|commodit|electricity price|\bcorn\b|\bwheat\b|soybean)")
_PROP_TITLE = re.compile(
    r"(?i)(passing yards|rushing yards|receiving yards|receptions|touchdown|anytime td|points scored by|"
    r"rebounds|assists|strikeouts|home runs?|shots on goal|player)")


def categorize(kalshi_category: Optional[str], series_ticker: str, title: str, props_regex: str) -> Tuple[str, bool]:
    """Return (category, is_prop)."""
    base = _MAP.get((kalshi_category or "").strip().lower(), "Other")
    text = f"{series_ticker} {title or ''}"
    if base != "Sports" and _COMMODITY.search(text):
        return "Commodities and Energy", False
    if base == "Sports":
        st = (series_ticker or "").upper().removeprefix("KX")
        is_prop = bool(re.search(props_regex, st)) or bool(_PROP_TITLE.search(title or ""))
        return ("Props" if is_prop else "Sports"), is_prop
    return base, False
