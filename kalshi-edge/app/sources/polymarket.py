"""Polymarket public Data API (read only): trader leaderboard, closed positions, open positions.

Used only to grade bettors from their public, on-chain record and to see what proven bettors hold now.
Nothing here trades, and the app never routes you to Polymarket.

Polymarket is migrating the Data API from v1 (camelCase, offset paging) to v2 (snake_case, cursor paging).
Each call tries v1 first and falls back to v2; every row is normalized to one shape.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import config
from ..timeutil import parse_iso
from .http import FetchError, get

SOURCE = "polymarket"


def _url(path: str) -> str:
    return f"{config.POLYMARKET_DATA_API}{path}"


def _pick(d: Dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return None


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _rows(body: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Rows + next cursor from either a bare list (v1) or an object (v2)."""
    if isinstance(body, list):
        return body, None
    if isinstance(body, dict):
        for k in ("data", "positions", "leaderboard", "results", "items"):
            if isinstance(body.get(k), list):
                return body[k], body.get("next_cursor") or body.get("cursor") or body.get("nextCursor")
    return [], None


def normalize_position(p: Dict[str, Any]) -> Dict[str, Any]:
    end = _pick(p, "endDate", "end_date")
    ts = _pick(p, "timestamp", "closed_at", "closedAt", "updated_at")
    return {
        "wallet": _pick(p, "proxyWallet", "proxy_wallet", "wallet"),
        "condition_id": _pick(p, "conditionId", "condition_id"),
        "title": _pick(p, "title") or "",
        "slug": _pick(p, "slug") or "",
        "event_slug": _pick(p, "eventSlug", "event_slug") or "",
        "outcome": _pick(p, "outcome") or "",
        "avg_price": _f(_pick(p, "avgPrice", "avg_price")),
        "size": _f(_pick(p, "size", "currentSize", "current_size")),
        "total_bought": _f(_pick(p, "totalBought", "total_bought")),
        "realized_pnl": _f(_pick(p, "realizedPnl", "realized_pnl")),
        "cash_pnl": _f(_pick(p, "cashPnl", "cash_pnl")),
        "cur_price": _f(_pick(p, "curPrice", "currPrice", "currentPrice", "current_price")),
        "current_value": _f(_pick(p, "currentValue", "current_value")),
        "end_ts": parse_iso(end) if isinstance(end, str) else (int(end) if isinstance(end, (int, float)) else None),
        "ts": int(ts) if isinstance(ts, (int, float)) else (parse_iso(ts) if isinstance(ts, str) else None),
    }


def normalize_leader(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "wallet": _pick(r, "proxyWallet", "proxy_wallet", "wallet", "address"),
        "name": _pick(r, "userName", "user_name", "name", "pseudonym"),
        "pnl": _f(_pick(r, "pnl", "PnL")),
        "volume": _f(_pick(r, "vol", "volume")),
        "rank": _pick(r, "rank"),
    }


def _paged(v1_path: str, v1_params: Dict[str, Any], v2_path: str, v2_params: Dict[str, Any],
           page_size: int, max_rows: int) -> List[Dict[str, Any]]:
    """Raw rows across pages, up to max_rows: v1 offset paging, falling back to v2 cursor paging."""
    out: List[Dict[str, Any]] = []
    try:
        while len(out) < max_rows:
            body, _, _ = get(SOURCE, _url(v1_path), dict(v1_params, limit=page_size, offset=len(out)))
            rows, _ = _rows(body)
            out.extend(rows)
            if len(rows) < page_size:
                break
        return out[:max_rows]
    except FetchError as e:
        if e.status not in (400, 404, 410) or out:
            raise
    cursor = None
    while len(out) < max_rows:
        params = dict(v2_params, limit=page_size)
        if cursor:
            params["cursor"] = cursor
        body, _, _ = get(SOURCE, _url(v2_path), params)
        rows, cursor = _rows(body)
        out.extend(rows)
        if not cursor or not rows:
            break
    return out[:max_rows]


def leaderboard(category: str = "SPORTS", window: str = "ALL", limit: int = 50) -> List[Dict[str, Any]]:
    rows = _paged("/v1/leaderboard", {"category": category, "timePeriod": window, "orderBy": "PNL"},
                  "/v2/leaderboard", {"category": category, "time_period": window, "sort_by": "PNL"},
                  min(50, limit), limit)
    out = [normalize_leader(r) for r in rows]
    return [r for r in out if r["wallet"]][:limit]


def closed_positions(wallet: str, max_pages: int = 20) -> List[Dict[str, Any]]:
    rows = _paged("/closed-positions", {"user": wallet, "sortBy": "TIMESTAMP", "sortDirection": "DESC"},
                  "/v2/positions", {"user": wallet, "status": "CLOSED"}, 50, 50 * max_pages)
    return [normalize_position(r) for r in rows]


def open_positions(wallet: str, min_size: float = 1.0, max_pages: int = 5) -> List[Dict[str, Any]]:
    rows = _paged("/positions", {"user": wallet, "sizeThreshold": min_size},
                  "/v2/positions", {"user": wallet, "status": "OPEN"}, 100, 100 * max_pages)
    return [normalize_position(r) for r in rows]
