"""Kalshi public market data (no authentication, read only).

Parsing accepts both the newer fixed-point fields (yes_bid_dollars, volume_fp) and the older
integer-cent fields (yes_bid, volume), so a field rename on Kalshi's side does not zero our prices.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .. import config
from ..timeutil import parse_iso
from .http import get

SOURCE = "kalshi"


def _url(path: str) -> str:
    return f"{config.KALSHI_API_BASE}{path}"


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price(m: Dict[str, Any], name: str) -> Optional[float]:
    """Dollar price 0-1 from `<name>_dollars` (string) or `<name>` (integer cents)."""
    d = _num(m.get(f"{name}_dollars"))
    if d is None:
        c = _num(m.get(name))
        d = c / 100.0 if c is not None else None
    return round(d, 4) if d is not None else None


def _qty(m: Dict[str, Any], name: str) -> Optional[float]:
    v = _num(m.get(f"{name}_fp"))
    return v if v is not None else _num(m.get(name))


def series_from_event_ticker(event_ticker: str) -> str:
    return (event_ticker or "").split("-", 1)[0]


def parse_market(m: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    yes_bid, yes_ask = _price(m, "yes_bid"), _price(m, "yes_ask")
    no_bid, no_ask = _price(m, "no_bid"), _price(m, "no_ask")
    last = _price(m, "last_price")
    # Kalshi reports an empty side as 0 (bid) or 100 (ask).
    yes_bid = yes_bid if yes_bid and yes_bid > 0 else None
    no_bid = no_bid if no_bid and no_bid > 0 else None
    yes_ask = yes_ask if yes_ask and 0 < yes_ask < 1 else None
    no_ask = no_ask if no_ask and 0 < no_ask < 1 else None
    last = last if last and last > 0 else None
    ranges = parse_price_ranges(m.get("price_ranges"))
    tick = _num(m.get("tick_size"))
    tick = tick / 100.0 if tick and tick >= 1 else tick
    if ranges:
        tick = min(r[2] for r in ranges)
    tick = tick or 0.01
    liq = _num(m.get("liquidity_dollars"))
    if liq is None and _num(m.get("liquidity")) is not None:
        liq = _num(m.get("liquidity")) / 100.0
    settle = _num(m.get("settlement_value_dollars"))
    if settle is None:
        settle = _num(m.get("yes_settlement_value_dollars"))
    if settle is None and _num(m.get("settlement_value")) is not None:
        settle = _num(m.get("settlement_value")) / 100.0
    result = (m.get("result") or m.get("market_result") or "").strip().lower() or None
    ev_ticker = m.get("event_ticker") or (event or {}).get("event_ticker") or ""
    return {
        "ticker": m.get("ticker"),
        "event_ticker": ev_ticker,
        "series_ticker": (event or {}).get("series_ticker") or series_from_event_ticker(ev_ticker),
        "title": m.get("title"),
        "subtitle": m.get("subtitle"),
        "yes_sub_title": m.get("yes_sub_title"),
        "no_sub_title": m.get("no_sub_title"),
        "event_title": (event or {}).get("title"),
        "strike_type": m.get("strike_type"),
        "floor_strike": _num(m.get("floor_strike")),
        "cap_strike": _num(m.get("cap_strike")),
        "open_ts": parse_iso(m.get("open_time")),
        "close_ts": parse_iso(m.get("close_time")),
        "expiration_ts": parse_iso(m.get("expected_expiration_time") or m.get("expiration_time")),
        "status": m.get("status"),
        "result": result,
        "settlement_value": settle,
        "expiration_value": (str(m.get("expiration_value")) if m.get("expiration_value") not in (None, "") else None),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "last_price": last,
        "volume": _qty(m, "volume"),
        "volume_24h": _qty(m, "volume_24h"),
        "open_interest": _qty(m, "open_interest"),
        "liquidity": liq,
        "tick_size": tick,
        "price_ranges": json.dumps(ranges) if ranges else None,
        "rules_primary": m.get("rules_primary"),
    }


def parse_price_ranges(raw: Any) -> List[List[float]]:
    """Kalshi's valid price grid: [{start, end, step}, ...] in fixed-point dollars -> [[start, end, step]]."""
    out: List[List[float]] = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        a, b, st = _num(r.get("start")), _num(r.get("end")), _num(r.get("step"))
        if a is not None and b is not None and st and st > 0:
            out.append([a, b, st])
    return sorted(out)


def parse_event(e: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_ticker": e.get("event_ticker"),
        "series_ticker": e.get("series_ticker") or series_from_event_ticker(e.get("event_ticker", "")),
        "title": e.get("title"),
        "sub_title": e.get("sub_title"),
        "kalshi_category": e.get("category"),
        "strike_date": parse_iso(e.get("strike_date")),
        "mutually_exclusive": bool(e.get("mutually_exclusive")),
        "fee_type_override": e.get("fee_type_override") or None,
        "fee_multiplier_override": _num(e.get("fee_multiplier_override")),
    }


def iter_open_events(page_limit: int = 200, max_pages: int = 200) -> Iterator[Tuple[List[Dict[str, Any]], int]]:
    """Yield (events, pull_id) pages of open events with nested markets."""
    cursor = None
    for _ in range(max_pages):
        params: Dict[str, Any] = {"status": "open", "with_nested_markets": "true", "limit": page_limit}
        if cursor:
            params["cursor"] = cursor
        body, pull_id, _ = get(SOURCE, _url("/events"), params)
        yield body.get("events") or [], pull_id
        cursor = body.get("cursor")
        if not cursor:
            break


def get_markets(params: Dict[str, Any], max_pages: int = 50) -> Tuple[List[Dict[str, Any]], List[int]]:
    out: List[Dict[str, Any]] = []
    pulls: List[int] = []
    cursor = None
    for _ in range(max_pages):
        p = dict(params, limit=params.get("limit", 1000))
        if cursor:
            p["cursor"] = cursor
        body, pull_id, _ = get(SOURCE, _url("/markets"), p)
        pulls.append(pull_id)
        out.extend(body.get("markets") or [])
        cursor = body.get("cursor")
        if not cursor:
            break
    return out, pulls


def get_historical_markets(params: Dict[str, Any], max_pages: int = 50) -> Tuple[List[Dict[str, Any]], List[int]]:
    """Markets settled before Kalshi's historical cutoff are only served here."""
    out: List[Dict[str, Any]] = []
    pulls: List[int] = []
    cursor = None
    for _ in range(max_pages):
        p = dict(params, limit=params.get("limit", 1000))
        if cursor:
            p["cursor"] = cursor
        body, pull_id, _ = get(SOURCE, _url("/historical/markets"), p)
        pulls.append(pull_id)
        out.extend(body.get("markets") or [])
        cursor = body.get("cursor")
        if not cursor:
            break
    return out, pulls


def get_historical_cutoff() -> Optional[int]:
    """Epoch seconds before which settled markets (and their candles) live under /historical."""
    body, _, _ = get(SOURCE, _url("/historical/cutoff"))
    v = body.get("market_settled_ts")
    if v is None:
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        return int(float(v))
    return parse_iso(str(v))


def get_series(series_ticker: str) -> Tuple[Dict[str, Any], int]:
    body, pull_id, _ = get(SOURCE, _url(f"/series/{series_ticker}"))
    return body.get("series") or {}, pull_id


def get_candlesticks(series_ticker: str, ticker: str, start_ts: int, end_ts: int,
                     period_minutes: int = 60) -> Tuple[List[Dict[str, Any]], int]:
    body, pull_id, _ = get(
        SOURCE, _url(f"/series/{series_ticker}/markets/{ticker}/candlesticks"),
        {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_minutes},
    )
    return body.get("candlesticks") or [], pull_id


def get_historical_candlesticks(ticker: str, start_ts: int, end_ts: int,
                                period_minutes: int = 60) -> Tuple[List[Dict[str, Any]], int]:
    body, pull_id, _ = get(
        SOURCE, _url(f"/historical/markets/{ticker}/candlesticks"),
        {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_minutes},
    )
    return body.get("candlesticks") or [], pull_id


def candle_mid(c: Dict[str, Any]) -> Optional[float]:
    """Mid of the closing YES bid/ask in a candlestick.

    Live candles use close_dollars ("0.5600"); historical candles use close in dollars ("0.5600");
    very old payloads used integer cents (56). Values above 1 are treated as cents."""
    def close_of(obj):
        if not isinstance(obj, dict):
            return None
        v = _num(obj.get("close_dollars"))
        if v is None:
            v = _num(obj.get("close"))
            if v is not None and v > 1:
                v = v / 100.0
        return v
    bid, ask = close_of(c.get("yes_bid")), close_of(c.get("yes_ask"))
    if bid is not None and ask is not None and 0 < ask <= 1 and ask >= bid:
        if bid <= 0:
            return None
        return round((bid + ask) / 2.0, 4)
    price = close_of(c.get("price"))
    return price if price and 0 < price < 1 else None
