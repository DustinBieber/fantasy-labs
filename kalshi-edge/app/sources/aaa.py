"""AAA National Average (gasprices.aaa.com), the only resolution source for Kalshi gas markets.

The page is HTML; we extract today's national regular average and the 'as of' date. If the page
shape changes or the site blocks us, the pull fails visibly and the value can be entered by hand.
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from .. import config
from .http import get

SOURCE = "aaa"
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "text/html,application/xhtml+xml",
}


def fetch_page() -> Tuple[str, int]:
    text, pull_id, _ = get(SOURCE, config.AAA_URL, expect="text", headers=_BROWSER_HEADERS, retries=2)
    return text, pull_id


def parse_national_average(page: str) -> Dict[str, Any]:
    """Return {'value': float, 'date': 'YYYY-MM-DD' or None, 'table': {...}} or raise ValueError."""
    text = htmllib.unescape(page)
    flat = re.sub(r"\s+", " ", text)
    value: Optional[float] = None
    m = re.search(r"Today[’']?s AAA National Average.{0,300}?\$\s*([0-9]\.[0-9]{2,3})", flat, re.I)
    if m:
        value = float(m.group(1))
    table: Dict[str, float] = {}
    for label in ("Current Avg.", "Yesterday Avg.", "Week Ago Avg.", "Month Ago Avg.", "Year Ago Avg."):
        tm = re.search(re.escape(label) + r"\s*</t[dh]>\s*<td[^>]*>\s*\$?\s*([0-9]\.[0-9]{2,3})", flat, re.I)
        if tm:
            table[label] = float(tm.group(1))
    if value is None and "Current Avg." in table:
        value = table["Current Avg."]
    if value is None:
        raise ValueError("Could not find the AAA national average on the page")
    as_of = None
    dm = re.search(r"Price as of\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})", flat, re.I)
    if dm:
        raw = dm.group(1)
        fmt = "%m/%d/%y" if len(raw.split("/")[-1]) == 2 else "%m/%d/%Y"
        try:
            as_of = datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            as_of = None
    return {"value": value, "date": as_of, "table": table}
