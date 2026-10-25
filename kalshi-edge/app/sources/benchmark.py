"""S&P 500 benchmark: daily closes of the index, no key needed. FRED's public CSV (series SP500, published by
the St. Louis Fed from S&P Dow Jones Indices) first, Yahoo's chart feed for SPY if FRED fails. Stooq was the
original source but started requiring an API key in 2026. Price return only: the ~1.3%/yr dividend is left
out, which is immaterial over weeks and slightly favors the S&P side over years."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import List, Tuple

from .http import FetchError, get

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/SPY"


def parse_fred(text: str) -> List[Tuple[str, float]]:
    """FRED CSV: a date column (observation_date, or DATE in older files) and the value; '.' or blank = holiday."""
    rows = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 2 or not r[0][:4].isdigit():
            continue
        try:
            rows.append((r[0][:10], float(r[1])))
        except ValueError:
            continue
    return rows


def parse_yahoo(body) -> List[Tuple[str, float]]:
    res = ((body or {}).get("chart") or {}).get("result") or []
    if not res:
        return []
    ts = res[0].get("timestamp") or []
    closes = (((res[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    return [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), float(c))
            for t, c in zip(ts, closes) if c is not None]


def fetch_spy() -> Tuple[List[Tuple[str, float]], int]:
    """Daily S&P 500 closes as [(YYYY-MM-DD, close)], oldest first, plus the pull id."""
    errors = []
    try:
        text, pull_id, _ = get("benchmark", FRED_URL, params={"id": "SP500"}, expect="text")
        rows = parse_fred(text)
        if rows:
            return rows, pull_id
        errors.append("FRED: no rows")
    except FetchError as e:
        errors.append(f"FRED: {e}")
    try:
        body, pull_id, _ = get("benchmark", YAHOO_URL, params={"range": "2y", "interval": "1d"},
                               headers={"User-Agent": "Mozilla/5.0"})
        rows = parse_yahoo(body)
        if rows:
            return rows, pull_id
        errors.append("Yahoo: no rows")
    except FetchError as e:
        errors.append(f"Yahoo: {e}")
    raise ValueError("No S&P 500 prices: " + "; ".join(errors))
