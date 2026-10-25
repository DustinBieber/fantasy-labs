"""Module 8 (Phase 1 slice): paper + real trade log, maker fill simulation, settlement, P&L, exposure."""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, insert, select, update

from .. import db
from ..engine.fees import fee_rates_for, kalshi_fee
from ..settings_store import get_settings
from ..timeutil import hst, hst_date, now_ts
from . import scanner

OPEN_STATES = ("resting", "open")


def corr_key(series_ticker: Optional[str], event_ticker: Optional[str], gas_series: Optional[set] = None) -> str:
    """All AAA gas strikes share one underlying, so they share one correlation bucket."""
    gs = gas_series if gas_series is not None else scanner.gas_series_set()
    if series_ticker and series_ticker in gs:
        return "AAA-GAS"
    return event_ticker or series_ticker or "?"


def _cost(t: Dict[str, Any]) -> float:
    return round(t["contracts"] * t["limit_price"] + (t["fees"] or 0.0), 4)


def _rates_for(series_ticker: Optional[str], event_ticker: Optional[str]):
    ev = db.fetch_one(select(db.events).where(db.events.c.event_ticker == event_ticker)) if event_ticker else None
    return fee_rates_for(series_ticker, scanner.series_row(series_ticker), get_settings()["fees"], ev)


def exposure_by_key(gas_series: Optional[set] = None) -> Dict[str, float]:
    gs = gas_series if gas_series is not None else scanner.gas_series_set()
    out: Dict[str, float] = defaultdict(float)
    for t in db.fetch_all(select(db.trades).where(db.trades.c.status.in_(OPEN_STATES))):
        out[corr_key(t["series_ticker"], t["event_ticker"], gs)] += _cost(t)
    return dict(out)


def create_trade(p: Dict[str, Any]) -> Dict[str, Any]:
    mode = p.get("mode", "paper")
    if mode not in ("paper", "real"):
        raise ValueError("mode must be paper or real")
    side = (p.get("side") or "").lower()
    if side not in ("yes", "no"):
        raise ValueError("side must be yes or no")
    price = float(p["limit_price"])
    contracts = int(p["contracts"])
    if not 0 < price < 1:
        raise ValueError("price must be between 0.01 and 0.99 dollars")
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    order_type = p.get("order_type", "maker")
    m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == p["ticker"])) or {}
    if not m and mode == "paper":
        raise ValueError(f"Unknown ticker {p['ticker']}: paper trades need a market we have scanned")
    rates = _rates_for(m.get("series_ticker"), m.get("event_ticker"))
    fee = p.get("fees")
    if fee in (None, ""):
        fee = kalshi_fee(contracts, price, rates.maker_rate if order_type == "maker" else rates.taker_rate,
                         rates.precision)
    ts = now_ts()
    filled = mode == "real" or order_type == "taker" or bool(p.get("assume_filled"))
    row = {
        "mode": mode, "ticker": p["ticker"], "event_ticker": m.get("event_ticker") or p.get("event_ticker"),
        "series_ticker": m.get("series_ticker") or p.get("series_ticker"), "category": m.get("category"),
        "title": m.get("title") or p.get("title"), "side": side, "order_type": order_type,
        "limit_price": price, "contracts": contracts, "fees": float(fee),
        "status": "open" if filled else "resting", "tier": p.get("tier"), "reason": p.get("reason"),
        "expert_source": p.get("expert_source"), "news_link": p.get("news_link"),
        "fair_at_entry": p.get("fair_at_entry"), "edge_at_entry": p.get("edge_at_entry"),
        "created_ts": ts, "filled_ts": ts if filled else None,
        "fill_note": "logged as filled" if filled else "resting maker order; fills when the market trades to it",
    }
    with db.get_engine().begin() as c:
        tid = c.execute(insert(db.trades).values(**row)).inserted_primary_key[0]
    return get_trade(int(tid))


def get_trade(tid: int) -> Optional[Dict[str, Any]]:
    return db.fetch_one(select(db.trades).where(db.trades.c.id == tid))


def list_trades(status: Optional[str] = None, mode: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(db.trades)
    if status:
        q = q.where(db.trades.c.status.in_(status.split(",")))
    if mode:
        q = q.where(db.trades.c.mode == mode)
    return db.fetch_all(q.order_by(db.trades.c.created_ts.desc()))


def cancel_trade(tid: int) -> Dict[str, Any]:
    t = get_trade(tid)
    if not t or t["status"] != "resting":
        raise ValueError("only resting orders can be cancelled")
    with db.get_engine().begin() as c:
        c.execute(update(db.trades).where(db.trades.c.id == tid).values(
            status="cancelled", closed_ts=now_ts(), realized_pnl=0.0, fill_note="cancelled by you"))
    return get_trade(tid)


def close_trade(tid: int, exit_price: float, exit_fees: Optional[float] = None) -> Dict[str, Any]:
    t = get_trade(tid)
    if not t or t["status"] != "open":
        raise ValueError("only open positions can be closed")
    if not 0 <= exit_price <= 1:
        raise ValueError("exit price must be between 0 and 1")
    if exit_fees is None:
        m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == t["ticker"])) or {}
        rates = _rates_for(t["series_ticker"], t["event_ticker"])
        exit_fees = kalshi_fee(t["contracts"], exit_price, rates.taker_rate, rates.precision) if m else 0.0
    pnl = round(t["contracts"] * (exit_price - t["limit_price"]) - t["fees"] - exit_fees, 4)
    with db.get_engine().begin() as c:
        c.execute(update(db.trades).where(db.trades.c.id == tid).values(
            status="closed", exit_price=exit_price, exit_fees=exit_fees, closed_ts=now_ts(), realized_pnl=pnl))
    return get_trade(tid)


def _side_quotes(snap: Dict[str, Any], side: str):
    if side == "yes":
        return snap.get("yes_bid"), snap.get("yes_ask")
    nb = snap.get("no_bid") if snap.get("no_bid") is not None else (1 - snap["yes_ask"] if snap.get("yes_ask") else None)
    na = snap.get("no_ask") if snap.get("no_ask") is not None else (1 - snap["yes_bid"] if snap.get("yes_bid") else None)
    return nb, na


def process_fills() -> Dict[str, int]:
    """A resting paper bid fills when a later snapshot shows the ask at or through our price, or a
    trade printed at or below our price with volume after we posted. Unfilled orders cancel at close."""
    filled = cancelled = 0
    now = now_ts()
    for t in db.fetch_all(select(db.trades).where(and_(db.trades.c.status == "resting", db.trades.c.mode == "paper"))):
        snaps = db.fetch_all(select(db.market_snapshots).where(and_(
            db.market_snapshots.c.ticker == t["ticker"], db.market_snapshots.c.ts >= t["created_ts"]))
            .order_by(db.market_snapshots.c.ts))
        base_vol = snaps[0]["volume"] if snaps else None
        hit = None
        for s in snaps:
            _, ask = _side_quotes(s, t["side"])
            if ask is not None and ask <= t["limit_price"] + 1e-9:
                hit = (s, f"ask {ask:.2f} reached our {t['limit_price']:.2f} bid")
                break
            lp = s.get("last_price")
            side_last = lp if t["side"] == "yes" else (1 - lp if lp is not None else None)
            if (side_last is not None and side_last <= t["limit_price"] + 1e-9 and base_vol is not None
                    and (s.get("volume") or 0) > base_vol):
                hit = (s, f"trade printed at {side_last:.2f} after we posted")
                break
        if hit:
            s, why = hit
            with db.get_engine().begin() as c:
                c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(
                    status="open", filled_ts=s["ts"], fill_note=f"paper fill: {why} (snapshot {s['id']})"))
            filled += 1
            continue
        m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == t["ticker"]))
        if m and m.get("close_ts") and m["close_ts"] < now:
            with db.get_engine().begin() as c:
                c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(
                    status="cancelled", closed_ts=now, realized_pnl=0.0, fill_note="market closed before our bid filled"))
            cancelled += 1
    return {"filled": filled, "cancelled": cancelled}


def closing_side_price(ticker: str, side: str, close_ts: Optional[int]) -> Optional[float]:
    q = select(db.market_snapshots).where(db.market_snapshots.c.ticker == ticker)
    if close_ts:
        q = q.where(db.market_snapshots.c.ts <= close_ts)
    s = db.fetch_one(q.order_by(db.market_snapshots.c.ts.desc()).limit(1))
    if not s:
        return None
    bid, ask = _side_quotes(s, side)
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 4)
    return None


def settlement_yes_value(m: Optional[Dict[str, Any]]) -> Optional[float]:
    """What one YES contract paid: 1 or 0 on a yes/no result, or Kalshi's settlement value
    (e.g. $0.50 per side when an NFL game ends in a tie). None until the market has settled."""
    if not m:
        return None
    if m.get("result") == "yes":
        return 1.0
    if m.get("result") == "no":
        return 0.0
    if m.get("settlement_value") is not None and (m.get("result") == "scalar"
                                                  or m.get("status") in ("settled", "finalized", "determined")):
        return max(0.0, min(1.0, float(m["settlement_value"])))
    return None


def settle_positions() -> Dict[str, int]:
    """Refresh the current state of every market we hold (markets can close early), then settle by result."""
    from ..sources import kalshi
    from ..sources.http import FetchError
    now = now_ts()
    held = db.fetch_all(select(db.trades).where(db.trades.c.status.in_(OPEN_STATES)))
    tickers = sorted({t["ticker"] for t in held})
    refresh_errors = 0
    for i in range(0, len(tickers), 100):
        try:
            raw, pulls = kalshi.get_markets({"tickers": ",".join(tickers[i:i + 100])}, max_pages=2)
            scanner.store_markets([kalshi.parse_market(m) for m in raw], pulls[-1] if pulls else None, "scan")
        except FetchError:
            refresh_errors += 1
    settled = 0
    for t in db.fetch_all(select(db.trades).where(db.trades.c.status == "open")):
        m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == t["ticker"]))
        yes_value = settlement_yes_value(m)
        if yes_value is None:
            continue
        payout = yes_value if t["side"] == "yes" else 1.0 - yes_value
        pnl = round(t["contracts"] * (payout - t["limit_price"]) - t["fees"], 4)
        close_px = closing_side_price(t["ticker"], t["side"], m.get("close_ts"))
        clv = round(100 * (close_px - t["limit_price"]), 2) if close_px is not None else None
        with db.get_engine().begin() as c:
            c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(
                status="settled", settlement_result=m.get("result") or f"{yes_value:g}", closed_ts=now,
                realized_pnl=pnl, exit_price=payout, exit_fees=0.0, closing_price=close_px, clv_pts=clv))
        settled += 1
    return {"settled": settled, "refresh_errors": refresh_errors}


def summary() -> Dict[str, Any]:
    settings = get_settings()
    gs = scanner.gas_series_set(settings)
    trades = db.fetch_all(select(db.trades))
    realized_paper = sum(t["realized_pnl"] or 0 for t in trades if t["mode"] == "paper" and t["realized_pnl"] is not None)
    realized_real = sum(t["realized_pnl"] or 0 for t in trades if t["mode"] == "real" and t["realized_pnl"] is not None)
    today = hst_date()
    today_pnl = sum(t["realized_pnl"] or 0 for t in trades
                    if t["closed_ts"] and hst_date(t["closed_ts"]) == today and t["realized_pnl"] is not None)
    bankroll = round(float(settings["bankroll"]) + realized_paper, 2)
    positions = []
    by_cat: Dict[str, float] = defaultdict(float)
    by_key: Dict[str, float] = defaultdict(float)
    unrealized = 0.0
    for t in trades:
        if t["status"] not in OPEN_STATES:
            continue
        cost = _cost(t)
        by_cat[t["category"] or "Other"] += cost
        by_key[corr_key(t["series_ticker"], t["event_ticker"], gs)] += cost
        m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == t["ticker"])) or {}
        bid, _ = _side_quotes(m, t["side"]) if m else (None, None)
        mark = bid
        upnl = round(t["contracts"] * mark - cost, 2) if (mark is not None and t["status"] == "open") else None
        if upnl is not None:
            unrealized += upnl
        positions.append(dict(t, cost=cost, mark=mark, unrealized_pnl=upnl, max_loss=cost,
                              created_hst=hst(t["created_ts"]), close_hst=hst(m.get("close_ts"))))
    cap = settings["correlated_cap_pct"] / 100.0 * bankroll
    warnings = [f"Correlated exposure on {k} is ${v:,.2f}, above the {settings['correlated_cap_pct']:g}% cap (${cap:,.2f})."
                for k, v in by_key.items() if v > cap + 1e-9]
    closed = [t for t in trades if t["status"] in ("settled", "closed")]
    by_tier: Dict[str, Dict[str, float]] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for t in closed:
        b = by_tier[t["tier"] or "-"]
        b["n"] += 1
        b["wins"] += 1 if (t["realized_pnl"] or 0) > 0 else 0
        b["pnl"] = round(b["pnl"] + (t["realized_pnl"] or 0), 2)
    return {
        "bankroll_start": float(settings["bankroll"]), "bankroll": bankroll,
        "realized_paper": round(realized_paper, 2), "realized_real": round(realized_real, 2),
        "unrealized": round(unrealized, 2), "today_realized": round(today_pnl, 2),
        "open_exposure": round(sum(by_cat.values()), 2), "exposure_by_category": dict(by_cat),
        "exposure_by_group": dict(by_key), "correlated_cap": round(cap, 2), "warnings": warnings,
        "positions": positions, "by_tier": dict(by_tier),
    }


CSV_FIELDS = ["id", "mode", "status", "ticker", "title", "side", "order_type", "limit_price", "contracts", "fees",
              "tier", "reason", "expert_source", "news_link", "fair_at_entry", "edge_at_entry", "created_ts",
              "filled_ts", "closed_ts", "exit_price", "exit_fees", "settlement_result", "realized_pnl",
              "closing_price", "clv_pts", "fill_note"]


def to_csv() -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS + ["created_hst", "closed_hst"], extrasaction="ignore")
    w.writeheader()
    for t in list_trades():
        w.writerow(dict(t, created_hst=hst(t["created_ts"]), closed_hst=hst(t["closed_ts"])))
    return buf.getvalue()
