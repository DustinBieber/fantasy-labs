"""Top Edges board: props first, then game winners, then commodities. Records edge history."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

from sqlalchemy import func, insert, select

from .. import db
from ..engine.edge import sort_key
from ..timeutil import now_ts
from . import gas_service, lines_service, nfl_service

_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_lock = threading.Lock()
CACHE_SECONDS = 20


def _props_count() -> int:
    r = db.fetch_one(select(func.count()).select_from(db.markets).where(
        db.markets.c.is_prop.is_(True), db.markets.c.status.in_(("active", "open")), db.markets.c.close_ts > now_ts()))
    return int(list(r.values())[0]) if r else 0


def compute(force: bool = False) -> Dict[str, Any]:
    with _lock:
        if not force and _cache["data"] is not None and time.time() - _cache["ts"] < CACHE_SECONDS:
            return _cache["data"]
    errors: List[str] = []
    try:
        nfl = nfl_service.board()
    except Exception as e:  # noqa: BLE001 - surface any failure on the board instead of crashing it
        nfl, errors = {"games": [], "opportunities": []}, errors + [f"NFL: {e}"]
    try:
        lines = lines_service.board()
    except Exception as e:  # noqa: BLE001
        lines, errors = {"best": [], "counts": {}}, errors + [f"Lines/props: {e}"]
    try:
        gas = gas_service.board()
    except Exception as e:  # noqa: BLE001
        gas, errors = {"ladders": [], "opportunities": []}, errors + [f"Gas: {e}"]
    props = [o for o in lines["best"] if o.get("leg_type") == "prop"]
    game_lines = [o for o in lines["best"] if o.get("leg_type") in ("spread", "total")]
    n_props = (lines.get("counts") or {}).get("prop", 0)
    groups = [
        {"key": "props", "title": "Props (NFL)", "items": props,
         "note": (f"{n_props} open NFL prop strikes; {len(props)} have a sportsbook price at that exact line. "
                  "Load alt lines + props per game on the Sports tab.") if n_props or props else
                 f"{_props_count()} open prop markets scanned; none are NFL player ladders with a book price yet."},
        {"key": "games", "title": "Game winners (NFL)", "items": nfl["opportunities"]},
        {"key": "lines", "title": "Spreads & totals (NFL)", "items": game_lines},
        {"key": "commodities", "title": "Commodities: AAA gas ladder", "items": gas["opportunities"]},
    ]
    all_items = sorted(nfl["opportunities"] + props + game_lines + gas["opportunities"], key=sort_key)
    counts = {t: sum(1 for o in all_items if o["tier"] == t) for t in ("S", "G", "W", "P")}
    data = {"groups": groups, "counts": counts, "errors": errors, "computed_ts": now_ts(), "nfl": nfl, "gas": gas,
            "lines": lines}
    with _lock:
        _cache.update(ts=time.time(), data=data)
    return data


def record() -> Dict[str, int]:
    data = compute(force=True)
    ts = now_ts()
    rows = []
    for grp in data["groups"]:
        for o in grp["items"]:
            rows.append({"ts": ts, "ticker": o["ticker"], "side": o["side"], "module": o.get("module"),
                         "fair": o.get("fair"), "maker_price": o.get("maker_price"), "taker_price": o.get("taker_price"),
                         "raw_edge_pts": o.get("raw_edge_pts"), "net_ev_pct": o.get("net_ev_pct"), "tier": o.get("tier"),
                         "fair_source": (o.get("fair_info") or {}).get("source")})
    if rows:
        with db.get_engine().begin() as c:
            c.execute(insert(db.edge_history), rows)
    return {"recorded": len(rows)}
