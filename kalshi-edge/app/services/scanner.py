"""Module 1: market scanner.

full_scan       every open event + nested markets; snapshot a market only when it changed.
active_snapshot every minute for the active set (watched, NFL, gas, top volume, open paper trades).
refresh_series  fee_type / fee_multiplier for series we evaluate.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import and_, insert, select, update

from .. import db
from ..settings_store import get_settings
from ..sources import kalshi
from ..timeutil import now_ts
from .categorize import categorize

log = logging.getLogger("kalshi_edge.scanner")

_SNAP_FIELDS = ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "volume", "open_interest", "status")
_OPEN_STATUSES = ("active", "open", "initialized")


def _changed(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> bool:
    if old is None:
        return True
    return any(old.get(f) != new.get(f) for f in _SNAP_FIELDS)


def _snapshot_row(m: Dict[str, Any], ts: int, pull_id: Optional[int], kind: str) -> Dict[str, Any]:
    return {"ticker": m["ticker"], "ts": ts, "pull_id": pull_id, "kind": kind,
            **{f: m.get(f) for f in _SNAP_FIELDS}}


def store_markets(parsed: List[Dict[str, Any]], pull_id: Optional[int], kind: str,
                  kalshi_category_by_event: Optional[Dict[str, str]] = None, always_snapshot: bool = False) -> int:
    """Upsert current market state and append snapshots. Returns snapshots written."""
    if not parsed:
        return 0
    settings = get_settings()
    ts = now_ts()
    tickers = [m["ticker"] for m in parsed if m.get("ticker")]
    snaps = []
    with db.get_engine().begin() as conn:
        existing: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(tickers), 500):
            chunk = tickers[i:i + 500]
            for r in conn.execute(select(db.markets).where(db.markets.c.ticker.in_(chunk))):
                existing[r.ticker] = dict(r._mapping)
        for m in parsed:
            if not m.get("ticker"):
                continue
            old = existing.get(m["ticker"])
            kcat = (kalshi_category_by_event or {}).get(m.get("event_ticker")) or (old or {}).get("kalshi_category")
            if kcat is None:
                ev = conn.execute(select(db.events.c.kalshi_category, db.events.c.title)
                                  .where(db.events.c.event_ticker == m.get("event_ticker"))).first()
                if ev:
                    kcat = ev.kalshi_category
                    m.setdefault("event_title", ev.title)
            title = " ".join(x for x in (m.get("event_title") or (old or {}).get("event_title"), m.get("title")) if x)
            cat, is_prop = categorize(kcat, m.get("series_ticker") or "", title, settings["props_series_regex"])
            row = dict(m, kalshi_category=kcat, category=cat, is_prop=is_prop, updated_ts=ts, pull_id=pull_id)
            if row.get("event_title") is None and old:
                row["event_title"] = old.get("event_title")
            if always_snapshot or _changed(old, row):
                snaps.append(_snapshot_row(row, ts, pull_id, kind))
            if old:
                conn.execute(update(db.markets).where(db.markets.c.ticker == row["ticker"]).values(**row))
            else:
                conn.execute(insert(db.markets).values(**row))
        if snaps:
            conn.execute(insert(db.market_snapshots), snaps)
    return len(snaps)


def full_scan() -> Dict[str, Any]:
    events_seen = markets_seen = snaps = 0
    for page, pull_id in kalshi.iter_open_events():
        parsed: List[Dict[str, Any]] = []
        cats: Dict[str, str] = {}
        with db.get_engine().begin() as conn:
            for e in page:
                ev = kalshi.parse_event(e)
                if not ev["event_ticker"]:
                    continue
                db.upsert(db.events, ["event_ticker"], dict(ev, updated_ts=now_ts()), conn=conn)
                cats[ev["event_ticker"]] = ev["kalshi_category"]
                for m in e.get("markets") or []:
                    parsed.append(kalshi.parse_market(m, e))
        events_seen += len(page)
        markets_seen += len(parsed)
        snaps += store_markets(parsed, pull_id, "scan", cats)
        with db.get_engine().begin() as conn:
            conn.execute(update(db.data_pulls).where(db.data_pulls.c.id == pull_id).values(rows=len(parsed)))
    return {"events": events_seen, "markets": markets_seen, "snapshots": snaps}


def gas_series_set(settings: Optional[Dict[str, Any]] = None) -> Set[str]:
    """Configured gas series plus any open event whose title looks like an AAA gas price market."""
    s = settings or get_settings()
    out = set(s["gas"]["series"])
    inc, exc = re.compile(s["gas"]["title_regex"]), re.compile(s["gas"]["exclude_regex"])
    for r in db.fetch_all(select(db.events.c.series_ticker, db.events.c.title)
                          .where(db.events.c.title.ilike("%gas%"))):
        if r["title"] and inc.search(r["title"]) and not exc.search(r["title"]):
            out.add(r["series_ticker"])
    return out


def active_tickers(settings: Dict[str, Any]) -> Set[str]:
    out = set(settings.get("watched_tickers") or [])
    n = int(settings.get("top_volume_active_count") or 0)
    if n:
        rows = db.fetch_all(select(db.markets.c.ticker).where(db.markets.c.status.in_(_OPEN_STATUSES))
                            .order_by(db.markets.c.volume_24h.desc()).limit(n))
        out.update(r["ticker"] for r in rows)
    rows = db.fetch_all(select(db.trades.c.ticker).where(db.trades.c.status.in_(("resting", "open"))))
    out.update(r["ticker"] for r in rows)
    return out


def active_snapshot() -> Dict[str, Any]:
    settings = get_settings()
    series_list = list(dict.fromkeys(list(settings["active_series"]) + list(settings["nfl"]["series"])
                                     + sorted(gas_series_set(settings))))
    snaps = 0
    covered: Set[str] = set()
    errors = []
    for s in series_list:
        try:
            raw, pulls = kalshi.get_markets({"series_ticker": s, "status": "open"}, max_pages=5)
        except kalshi_errors() as e:
            errors.append(f"{s}: {e}")
            continue
        parsed = [kalshi.parse_market(m) for m in raw]
        covered.update(m["ticker"] for m in parsed)
        snaps += store_markets(parsed, pulls[-1] if pulls else None, "minute", always_snapshot=True)
    rest = sorted(t for t in active_tickers(settings) if t not in covered)
    for i in range(0, len(rest), 100):
        chunk = rest[i:i + 100]
        try:
            raw, pulls = kalshi.get_markets({"tickers": ",".join(chunk)}, max_pages=2)
        except kalshi_errors() as e:
            errors.append(str(e))
            continue
        parsed = [kalshi.parse_market(m) for m in raw]
        snaps += store_markets(parsed, pulls[-1] if pulls else None, "minute", always_snapshot=True)
    if errors and not snaps:
        raise RuntimeError("; ".join(errors[:3]))
    return {"series": len(series_list), "snapshots": snaps, "errors": errors[:3]}


def kalshi_errors():
    from ..sources.http import FetchError
    return (FetchError,)


def refresh_series(tickers: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    settings = get_settings()
    wanted = set(tickers or [])
    if not wanted:
        wanted = set(settings["nfl"]["series"]) | gas_series_set(settings) | set(settings["active_series"])
        rows = db.fetch_all(select(db.trades.c.series_ticker).where(db.trades.c.status.in_(("resting", "open"))))
        wanted |= {r["series_ticker"] for r in rows if r["series_ticker"]}
    done = 0
    errors = []
    for st in sorted(wanted):
        try:
            s, pull_id = kalshi.get_series(st)
        except kalshi_errors() as e:
            errors.append(f"{st}: {e}")
            continue
        if not s:
            continue
        db.upsert(db.series, ["series_ticker"], {
            "series_ticker": st, "title": s.get("title"), "category": s.get("category"),
            "frequency": s.get("frequency"), "fee_type": s.get("fee_type"),
            "fee_multiplier": s.get("fee_multiplier"),
            "settlement_sources": db.dumps(s.get("settlement_sources") or []),
            "updated_ts": now_ts(), "pull_id": pull_id,
        })
        done += 1
    if errors and not done:
        raise RuntimeError("; ".join(errors[:3]))
    return {"series": done, "errors": errors[:3]}


def series_row(series_ticker: Optional[str]) -> Optional[Dict[str, Any]]:
    if not series_ticker:
        return None
    return db.fetch_one(select(db.series).where(db.series.c.series_ticker == series_ticker))


def open_markets(series: Iterable[str]) -> List[Dict[str, Any]]:
    return db.fetch_all(select(db.markets).where(and_(
        db.markets.c.series_ticker.in_(list(series)), db.markets.c.status.in_(_OPEN_STATUSES),
        db.markets.c.close_ts > now_ts())))


def history(ticker: str, since_ts: Optional[int] = None, limit: int = 5000) -> List[Dict[str, Any]]:
    q = select(db.market_snapshots).where(db.market_snapshots.c.ticker == ticker)
    if since_ts:
        q = q.where(db.market_snapshots.c.ts >= since_ts)
    rows = db.fetch_all(q.order_by(db.market_snapshots.c.ts.desc()).limit(limit))
    return list(reversed(rows))
