"""The AI trader: your rules, run automatically, inside hard limits.

It does not invent prices or bets. Each cycle it takes the rows the edge engine already rates S or G
(fair price from sportsbooks / the AAA model, net EV after fees, quarter Kelly, your caps, proven-bettor
agreement for G) and turns them into resting maker orders, then manages those orders.

Modes: off | paper | live. Live needs all of: the go-live test passed on paper, a passed live check on
your Kalshi account, your API key, and a dashboard password. Hard stops (total and daily loss, error
streaks, the kill switch) pause it until you resume it yourself.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, insert, select, update

from .. import config, db
from ..settings_store import get_settings
from ..sources import kalshi_auth, notify
from ..sources.http import FetchError
from ..timeutil import HST, hst, hst_date, now_ts
from . import portfolio, scanner

log = logging.getLogger("kalshi_edge.autotrader")
_STATE_KEY = "autotrader_state"


# ------------------------------------------------------------------ state, events, equity

def state() -> Dict[str, Any]:
    r = db.fetch_one(select(db.app_settings.c.value).where(db.app_settings.c.key == _STATE_KEY))
    return json.loads(r["value"]) if r else {}


def save_state(st: Dict[str, Any]) -> None:
    db.upsert(db.app_settings, ["key"], {"key": _STATE_KEY, "value": db.dumps(st), "updated_ts": now_ts()})


def event(level: str, action: str, message: str, ticker: Optional[str] = None, mode: Optional[str] = None,
          alert: bool = False) -> None:
    with db.get_engine().begin() as c:
        c.execute(insert(db.autotrader_events).values(ts=now_ts(), mode=mode or get_settings()["autotrader"]["mode"],
                                                      level=level, action=action, ticker=ticker, message=message[:2000]))
    if alert:
        notify.send(f"Kalshi Edge AI trader: {message}")


def _trade_mode(mode: str) -> str:
    return "real" if mode == "live" else "paper"


def auto_trades(mode: str, statuses: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
    q = select(db.trades).where(and_(db.trades.c.source == "auto", db.trades.c.mode == _trade_mode(mode)))
    if statuses:
        q = q.where(db.trades.c.status.in_(statuses))
    return db.fetch_all(q)


def equity(mode: str) -> Dict[str, float]:
    """The AI trader's own book: its bankroll plus realized and marked-to-bid P&L of its trades."""
    bankroll = float(get_settings()["autotrader"]["bankroll"])
    realized = unrealized = open_cost = resting_cost = 0.0
    for t in auto_trades(mode):
        cost = t["contracts"] * t["limit_price"] + (t["fees"] or 0.0)
        if t["status"] in ("settled", "closed"):
            realized += t["realized_pnl"] or 0.0
        elif t["status"] == "open":
            open_cost += cost
            mark = _mark(t)
            if mark is not None:
                unrealized += t["contracts"] * mark - cost
        elif t["status"] == "resting":
            resting_cost += cost
    return {"equity": round(bankroll + realized + unrealized, 4), "realized": round(realized, 4),
            "unrealized": round(unrealized, 4), "open_cost": round(open_cost, 4), "resting_cost": round(resting_cost, 4),
            "bankroll": bankroll}


def _mark(t: Dict[str, Any]) -> Optional[float]:
    if t.get("combo_legs"):
        return None
    m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == t["ticker"]))
    if not m:
        return None
    if t["side"] == "yes":
        return m.get("yes_bid")
    return m.get("no_bid") if m.get("no_bid") is not None else (1 - m["yes_ask"] if m.get("yes_ask") else None)


def _snapshot(mode: str, eq: Dict[str, float]) -> None:
    last = db.fetch_one(select(db.equity_snapshots).where(db.equity_snapshots.c.mode == mode)
                        .order_by(db.equity_snapshots.c.ts.desc()).limit(1))
    now = now_ts()
    if last and now - last["ts"] < 1800 and hst_date(last["ts"]) == hst_date(now):
        return
    spy = db.fetch_one(select(db.spy_prices).order_by(db.spy_prices.c.date.desc()).limit(1))
    with db.get_engine().begin() as c:
        c.execute(insert(db.equity_snapshots).values(ts=now, mode=mode, equity=eq["equity"], realized=eq["realized"],
                                                     unrealized=eq["unrealized"], spy_close=spy["close"] if spy else None))


def day_start_equity(mode: str, current: float) -> float:
    start = int(datetime.now(HST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    first = db.fetch_one(select(db.equity_snapshots).where(and_(db.equity_snapshots.c.mode == mode,
                                                                db.equity_snapshots.c.ts >= start))
                         .order_by(db.equity_snapshots.c.ts).limit(1))
    return first["equity"] if first else current


# ------------------------------------------------------------------ benchmark

def refresh_spy() -> Dict[str, Any]:
    from ..sources import benchmark
    rows, _ = benchmark.fetch_spy()
    with db.get_engine().begin() as c:
        for d, close in rows[-400:]:
            db.upsert(db.spy_prices, ["date"], {"date": d, "close": close}, conn=c)
    return {"days": len(rows), "latest": rows[-1]}


def spy_return(start_ts: int, end_ts: Optional[int] = None) -> Optional[float]:
    start_d = datetime.fromtimestamp(start_ts, tz=HST).date().isoformat()
    end_d = datetime.fromtimestamp(end_ts or now_ts(), tz=HST).date().isoformat()
    a = db.fetch_one(select(db.spy_prices).where(db.spy_prices.c.date >= start_d).order_by(db.spy_prices.c.date).limit(1))
    b = db.fetch_one(select(db.spy_prices).where(db.spy_prices.c.date <= end_d).order_by(db.spy_prices.c.date.desc()).limit(1))
    if not a or not b or b["date"] < a["date"]:
        return None
    return b["close"] / a["close"] - 1


# ------------------------------------------------------------------ go-live test, live check, blockers

def gate() -> Dict[str, Any]:
    s = get_settings()
    cfg, g = s["autotrader"], s["autotrader"]["gate"]
    st = state()
    start = st.get("paper_started_ts")
    if not start:
        return {"passed": False, "started": None, "checks": [("Paper trading started", False, "Switch the AI trader to Paper")]}
    now = now_ts()
    trades = [t for t in auto_trades("paper") if t["created_ts"] >= start]
    settled = [t for t in trades if t["status"] in ("settled", "closed")]
    pnl = sum(t["realized_pnl"] or 0.0 for t in settled)
    ret = pnl / float(cfg["bankroll"])
    spy = spy_return(start, now)
    clvs = [t["clv_pts"] for t in settled if t.get("clv_pts") is not None]
    avg_clv = mean(clvs) if clvs else None
    errors = db.fetch_all(select(db.autotrader_events).where(and_(db.autotrader_events.c.level == "error",
                                                                  db.autotrader_events.c.ts >= now - 72 * 3600)))
    days = (now - start) / 86400
    checks = [
        (f"{g['paper_days']} days of paper trading", days >= g["paper_days"], f"{days:.1f} of {g['paper_days']} days"),
        (f"{g['min_settled']}+ settled paper trades", len(settled) >= g["min_settled"],
         f"{len(settled)} settled (a thin record is mostly luck, so it keeps paper trading until there are enough)"),
    ]
    if g.get("require_profit", True):
        checks.append(("Profitable after fees", pnl > 0, f"${pnl:+,.2f} ({100 * ret:+.2f}%)"))
    if g.get("require_beat_spy", True):
        checks.append(("Beat the S&P 500 over the same window", spy is not None and ret > spy,
                       f"AI trader {100 * ret:+.2f}% vs S&P {100 * spy:+.2f}%" if spy is not None else "no S&P data yet"))
    if g.get("require_clv", True):
        checks.append(("Entry prices beat closing prices (avg CLV >= 0)", avg_clv is not None and avg_clv >= 0,
                       f"avg CLV {avg_clv:+.2f} pts over {len(clvs)} trades" if avg_clv is not None else "no CLV yet"))
    checks.append(("No errors in the last 3 days", not errors, f"{len(errors)} errors"))
    return {"passed": all(ok for _, ok, _ in checks), "started": start, "started_hst": hst(start), "days": round(days, 1),
            "settled": len(settled), "pnl": round(pnl, 2), "return_pct": round(100 * ret, 2),
            "spy_pct": round(100 * spy, 2) if spy is not None else None, "avg_clv": avg_clv, "checks": checks}


def live_blockers() -> List[str]:
    out = []
    if not kalshi_auth.configured():
        out.append("Kalshi API key not set: add KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH to .env")
    elif kalshi_auth.key_file_exposed():
        out.append(f"Your Kalshi private key file can be read by other users: run  chmod 600 {config.KALSHI_PRIVATE_KEY_PATH}")
    if not config.APP_PASSWORD:
        out.append("Set APP_PASSWORD in .env so nobody else on your network can control the trader")
    lc = state().get("live_check") or {}
    if not lc.get("ok") or now_ts() - (lc.get("ts") or 0) > 7 * 86400:
        out.append("Run the live check (it must have passed within the last 7 days)")
    if not gate()["passed"]:
        out.append("The go-live test has not passed yet (see the checklist)")
    return out


def live_check() -> Dict[str, Any]:
    """Prove the order mapping on your real account with orders that cannot fill: buy YES at 1c and buy NO
    at 1c on a liquid market, confirm Kalshi recorded exactly that, then cancel both."""
    steps: List[str] = []
    placed: List[str] = []
    ok = False
    try:
        bal = kalshi_auth.balance()
        if bal["balance"] is None:
            raise RuntimeError(f"Could not read your balance from {list(bal['raw'])[:8]}")
        steps.append(f"Authenticated. Cash balance ${bal['balance']:,.2f}.")
        m = db.fetch_one(select(db.markets).where(and_(
            db.markets.c.status.in_(("active", "open")), db.markets.c.yes_bid >= 0.05, db.markets.c.yes_ask <= 0.95,
            db.markets.c.close_ts > now_ts() + 3600)).order_by(db.markets.c.volume_24h.desc()).limit(1))
        if not m:
            raise RuntimeError("No liquid open market found to test with; run the Kalshi scan first")
        steps.append(f"Test market {m['ticker']} (bid {m['yes_bid']:.2f} / ask {m['yes_ask']:.2f}); 1c orders cannot fill.")
        want = {}
        for side in ("yes", "no"):
            o = kalshi_auth.create_order(m["ticker"], side, 0.01, 1)
            placed.append(o["order_id"])
            want[o["client_order_id"]] = side
            steps.append(f"Placed buy {side.upper()} 1 @ 1c: order {o['order_id']}.")
        resting = {o["client_order_id"]: o for o in kalshi_auth.orders("resting", m["ticker"])}
        for coid, side in want.items():
            o = resting.get(coid)
            if not o:
                raise RuntimeError(f"Order {coid} is not listed as resting")
            same = (o["side"] == side and o["action"] in ("buy", "") and abs((o["price"] or 0) - 0.01) < 1e-6)
            mirrored = (side == "no" and o["side"] == "yes" and o["action"] == "sell" and abs((o["price"] or 0) - 0.99) < 1e-6)
            if not (same or mirrored):
                raise RuntimeError(f"Kalshi recorded {o['side']} {o['action']} @ {o['price']} for our buy {side} @ 0.01")
            steps.append(f"Kalshi shows buy {side.upper()} @ 1c as intended.")
        ok = True
    except Exception as e:  # noqa: BLE001 - every failure is reported, and the trader stays out of live mode
        steps.append(f"FAILED: {type(e).__name__}: {e}")
    finally:
        for oid in placed:
            try:
                kalshi_auth.cancel_order(oid)
                steps.append(f"Cancelled test order {oid}.")
            except Exception as e:  # noqa: BLE001
                ok = False
                steps.append(f"Could not cancel test order {oid}: {e}. Cancel it in the Kalshi app.")
    st = state()
    st["live_check"] = {"ok": ok, "ts": now_ts(), "steps": steps}
    save_state(st)
    # A failed check is a warning, not a trader error: it already blocks live mode by itself, and logging it as an
    # error would also hold the go-live test's "no errors in 3 days" check for 3 days.
    event("info" if ok else "warn", "live_check", ("passed: " if ok else "failed: ") + " | ".join(steps[-3:]), alert=not ok)
    return st["live_check"]


# ------------------------------------------------------------------ kill switch

def kill(reason: str) -> Dict[str, Any]:
    st = state()
    st.update(killed=True, kill_reason=reason, kill_ts=now_ts())
    save_state(st)
    mode = get_settings()["autotrader"]["mode"]
    cancelled = _cancel_all(mode, f"kill switch: {reason}")
    event("kill", "killed", f"STOPPED: {reason}. Cancelled {cancelled} resting order(s). Resume it yourself when ready.",
          alert=True)
    return {"killed": True, "reason": reason, "cancelled": cancelled}


def resume() -> Dict[str, Any]:
    st = state()
    st.update(killed=False, kill_reason="", consecutive_errors=0)
    save_state(st)
    event("info", "resumed", "Resumed by you.", alert=True)
    return {"killed": False}


def _cancel_all(mode: str, reason: str) -> int:
    n = 0
    for t in auto_trades(mode, ("resting",)):
        if _cancel(t, mode, reason):
            n += 1
    return n


def _cancel(t: Dict[str, Any], mode: str, reason: str) -> bool:
    try:
        if mode == "live" and t.get("external_order_id"):
            kalshi_auth.cancel_order(t["external_order_id"])  # fills are reconciled by sync_live
            with db.get_engine().begin() as c:
                c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(fill_note=f"cancel sent: {reason}"))
        else:
            portfolio.cancel_trade(t["id"])
        event("trade", "cancelled", f"Cancelled {t['side'].upper()} {t['contracts']} @ {t['limit_price']:.4g}: {reason}",
              t["ticker"], mode)
        return True
    except Exception as e:  # noqa: BLE001
        event("error", "cancel_failed", f"Cancel failed for {t['ticker']}: {e}", t["ticker"], mode, alert=True)
        return False


# ------------------------------------------------------------------ live order sync

def sync_live() -> Dict[str, int]:
    resting = auto_trades("live", ("resting",))
    if not resting:
        return {"filled": 0, "closed": 0}
    open_ids = {o["order_id"] for o in kalshi_auth.orders("resting")}
    fills = kalshi_auth.fills(min(t["created_ts"] for t in resting) - 60)
    filled = closed = 0
    for t in resting:
        if t["external_order_id"] in open_ids:
            continue
        mine = [f for f in fills if f["order_id"] == t["external_order_id"]]
        count = sum(f["count"] for f in mine)
        with db.get_engine().begin() as c:
            if count > 0:
                px = sum(f["count"] * (f["price"] or t["limit_price"]) for f in mine) / count
                c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(
                    status="open", contracts=int(round(count)), limit_price=round(px, 4),
                    fees=round(sum(f["fee"] for f in mine), 4), filled_ts=now_ts(),
                    fill_note=f"filled {count:g} on Kalshi"))
                filled += 1
            else:
                c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(
                    status="cancelled", closed_ts=now_ts(), realized_pnl=0.0, fill_note="no fill; order closed on Kalshi"))
                closed += 1
        if count > 0:
            event("trade", "filled", f"Filled {count:g} {t['side'].upper()} {t['ticker']} @ {px:.4g}", t["ticker"], "live",
                  alert=True)
    return {"filled": filled, "closed": closed}


# ------------------------------------------------------------------ the cycle

def _module_allowed(o: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    mod = o.get("module") or ""
    if mod.startswith("nfl"):
        return bool(cfg["allow_sports"] and cfg["trade_nfl_singles"])
    if mod == "gas":
        return bool(cfg["trade_gas"])
    return False


def _candidates(data: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for grp in data["groups"]:
        for o in grp["items"]:
            if o.get("tier") in cfg["tiers"] and (o.get("size") or {}).get("contracts", 0) > 0 and o.get("maker_price"):
                if _module_allowed(o, cfg):
                    out.append(o)
    out.sort(key=lambda o: ({"S": 0, "G": 1}.get(o["tier"], 9), -(o.get("net_ev_pct") or 0)))
    return out


def _today_count(mode: str, combos: bool) -> int:
    start = int(datetime.now(HST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return sum(1 for t in auto_trades(mode) if t["created_ts"] >= start and bool(t.get("combo_legs")) == combos)


def _place(o: Dict[str, Any], contracts: int, mode: str) -> Optional[Dict[str, Any]]:
    reason = f"AI trader: {o['tier_reason']}"[:500]
    base = {"ticker": o["ticker"], "side": o["side"], "limit_price": o["maker_price"], "contracts": contracts,
            "order_type": "maker", "tier": o["tier"], "reason": reason, "fair_at_entry": o["fair"],
            "edge_at_entry": o["raw_edge_pts"], "expert_source": (o.get("expert") or {}).get("note")}
    if mode == "paper":
        t = portfolio.create_trade(dict(base, mode="paper"))
    else:
        coid = kalshi_auth.new_client_order_id()
        try:
            order = kalshi_auth.create_order(o["ticker"], o["side"], o["maker_price"], contracts, coid)
        except FetchError as e:
            if e.status is None:  # timeout: the order may exist; look it up by our client id before giving up
                found = [x for x in kalshi_auth.orders("resting", o["ticker"]) if x["client_order_id"] == coid]
                if not found:
                    raise
                order = found[0]
            else:
                raise
        t = portfolio.create_trade(dict(base, mode="real", resting_real=True))
        with db.get_engine().begin() as c:
            c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(
                external_order_id=order["order_id"], client_order_id=coid))
    with db.get_engine().begin() as c:
        c.execute(update(db.trades).where(db.trades.c.id == t["id"]).values(source="auto"))
    event("trade", "placed", f"[{o['tier']}] rest {o['side'].upper()} {contracts} @ {o['maker_price']:.4g} "
                             f"({o['subject']}; fair {100 * o['fair']:.1f}%, net EV {o['net_ev_pct']:+.1f}%)",
          o["ticker"], mode, alert=True)
    return t


def cycle() -> Dict[str, Any]:
    from . import edges, parlay_service
    s = get_settings()
    cfg = s["autotrader"]
    mode = cfg["mode"]
    st = state()
    st["last_cycle_ts"] = now_ts()
    if mode == "off":
        save_state(st)
        return {"mode": "off"}
    if mode == "paper" and not st.get("paper_started_ts"):
        st["paper_started_ts"] = now_ts()
        event("info", "paper_started", "Paper trading started; the 2-week go-live test clock is running.", mode=mode,
              alert=True)
    save_state(st)

    if mode == "live":
        blockers = live_blockers()
        if blockers:
            if now_ts() - (st.get("last_block_note") or 0) > 3600:
                event("warn", "live_blocked", "Live mode is on but blocked: " + "; ".join(blockers), mode=mode, alert=True)
                st["last_block_note"] = now_ts()
                save_state(st)
            return {"mode": mode, "blocked": blockers}
        try:
            sync_live()
        except Exception as e:  # noqa: BLE001
            return _error(f"sync with Kalshi failed: {e}", mode, cfg)

    eq = equity(mode)
    _snapshot(mode, eq)
    if st.get("killed"):
        return {"mode": mode, "killed": st.get("kill_reason")}
    lost_total = float(cfg["bankroll"]) - eq["equity"]
    if lost_total >= float(cfg["total_stop_usd"]):
        return kill(f"down ${lost_total:,.2f} from the ${cfg['bankroll']:,.0f} start (stop is ${cfg['total_stop_usd']:,.0f})")
    lost_today = day_start_equity(mode, eq["equity"]) - eq["equity"]
    if lost_today >= float(cfg["daily_loss_limit_usd"]):
        return kill(f"down ${lost_today:,.2f} today (daily limit ${cfg['daily_loss_limit_usd']:,.0f})")

    data = edges.compute(force=True)
    by_key = {(o["ticker"], o["side"]): o for grp in data["groups"] for o in grp["items"]}
    now = now_ts()
    cancelled = placed = 0

    # 1. Manage resting orders.
    for t in auto_trades(mode, ("resting",)):
        if t.get("combo_legs"):
            continue
        o = by_key.get((t["ticker"], t["side"]))
        reason = None
        if now - t["created_ts"] > 60 * int(cfg["order_ttl_minutes"]):
            reason = f"unfilled after {cfg['order_ttl_minutes']} min; will re-check the price"
        elif not o or o.get("tier") not in cfg["tiers"]:
            reason = f"edge gone (now {o['tier'] if o else 'unrated'})"
        elif o.get("close_ts") and o["close_ts"] - now < 60 * int(cfg["min_minutes_before_close"]):
            reason = "market closes soon"
        elif o.get("maker_price") and abs(o["maker_price"] - t["limit_price"]) > 1e-6:
            reason = f"best price moved to {o['maker_price']:.4g}"
        if reason and _cancel(t, mode, reason):
            cancelled += 1

    # 2. New single orders.
    held = {t["ticker"] for t in auto_trades(mode, ("resting", "open")) if not t.get("combo_legs")}
    eq = equity(mode)
    budget = float(cfg["max_open_usd"]) - eq["open_cost"] - eq["resting_cost"]
    orders_today = _today_count(mode, False)
    # The boards size against your manual bankroll. The trader re-scales that to its own equity, so quarter
    # Kelly and the per-strike cap apply to its money, and caps each game (or the gas complex) at the
    # correlated % of its own equity, counting what it already holds there, parlays included.
    own = max(0.0, eq["equity"])
    board_bank = portfolio.summary()["bankroll"]
    scale = own / board_bank if board_bank > 0 else 0.0
    gs = scanner.gas_series_set(s)
    game_cap = float(s["correlated_cap_pct"]) / 100.0 * own
    game_exp = portfolio.exposure_by_key(gs, auto=True, mode=_trade_mode(mode))
    for o in _candidates(data, cfg):
        if orders_today >= int(cfg["max_orders_per_day"]) or budget < 1:
            break
        if o["ticker"] in held:
            continue
        if o.get("close_ts") and o["close_ts"] - now < 60 * int(cfg["min_minutes_before_close"]):
            continue
        if o.get("price_ts") and now - o["price_ts"] > 60 * int(cfg["max_price_age_minutes"]):
            continue
        per = o["maker_price"] + (o.get("fee_rates") or {}).get("maker", 0.0175) * o["maker_price"] * (1 - o["maker_price"])
        key = portfolio.corr_key(o.get("series_ticker"), o.get("event_ticker"), gs)
        room = game_cap - game_exp.get(key, 0.0)
        contracts = min(math.floor(int(o["size"]["contracts"]) * scale + 1e-9),
                        math.floor(min(float(cfg["max_order_usd"]), budget, room) / per))
        if contracts < 1:
            continue
        try:
            _place(o, contracts, mode)
        except Exception as e:  # noqa: BLE001
            return _error(f"order on {o['ticker']} failed: {e}", mode, cfg)
        held.add(o["ticker"])
        budget -= contracts * per
        game_exp[key] = game_exp.get(key, 0.0) + contracts * per
        orders_today += 1
        placed += 1

    # 3. Parlays.
    parlays = 0
    if cfg["trade_parlays"] and cfg["allow_sports"]:
        parlays = _parlays(parlay_service.board(), mode, cfg, st)

    st = state()
    st["consecutive_errors"] = 0
    save_state(st)
    _daily_summary(mode)
    return {"mode": mode, "placed": placed, "cancelled": cancelled, "parlays": parlays, "equity": eq["equity"]}


def _parlays(board: Dict[str, Any], mode: str, cfg: Dict[str, Any], st: Dict[str, Any]) -> int:
    done = 0
    today = _today_count(mode, True)
    existing = {t["ticker"] for t in auto_trades(mode) if t.get("combo_legs")}
    for t in board["tickets"]:
        if today >= int(cfg["max_parlays_per_day"]):
            break
        if t["tier"] not in cfg["tiers"] or not t["size"]["contracts"] or not t["max_price"]:
            continue
        legs = [{"ticker": g["ticker"], "side": "yes", "label": g["label"]} for g in t["legs"]]
        ticker = portfolio.combo_ticker(legs)
        if ticker in existing:
            continue
        price = t["est_quote"]
        contracts = min(int(t["size"]["contracts"]), math.floor(float(cfg["max_parlay_usd"]) / price))
        if contracts < 1:
            continue
        if mode == "paper":
            tr = portfolio.create_combo({"mode": "paper", "combo_legs": legs, "limit_price": price, "contracts": contracts,
                                         "tier": t["tier"], "fair_at_entry": t["fair"], "edge_at_entry": t["edge_pts"],
                                         "reason": "AI trader parlay (paper: assumes the quote equals the product of "
                                                   "leg asks; real quotes can be worse)"})
            with db.get_engine().begin() as c:
                c.execute(update(db.trades).where(db.trades.c.id == tr["id"]).values(source="auto"))
            event("trade", "placed", f"[{t['tier']}] paper parlay {contracts} @ {price:.4g}: "
                                     + " + ".join(g["label"] for g in t["legs"]), ticker, mode, alert=True)
            done += 1
            today += 1
        elif not cfg.get("parlays_live"):
            sent = set(st.get("parlay_alerts") or [])
            key = f"{hst_date()}:{ticker}"
            if key not in sent:
                notify.send("Parlay to place by hand in the Kalshi app (live combo quotes are not automated yet):\n"
                            + "\n".join(t["setup"]))
                event("info", "parlay_manual", "Sent you a parlay ticket to place by hand: "
                      + " + ".join(g["label"] for g in t["legs"]), ticker, mode)
                sent.add(key)
                st["parlay_alerts"] = sorted(sent)[-50:]
                save_state(st)
    return done


def _error(msg: str, mode: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    st = state()
    st["consecutive_errors"] = int(st.get("consecutive_errors") or 0) + 1
    save_state(st)
    event("error", "error", msg, mode=mode, alert=True)
    if st["consecutive_errors"] >= int(cfg["max_consecutive_errors"]):
        return kill(f"{st['consecutive_errors']} errors in a row (last: {msg})")
    return {"mode": mode, "error": msg}


def _daily_summary(mode: str) -> None:
    st = state()
    today = hst_date()
    if st.get("last_summary_date") == today or datetime.now(HST).hour < 7:
        return
    eq = equity(mode)
    g = gate()
    spy = f"{g['spy_pct']:+.2f}%" if g.get("spy_pct") is not None else "n/a"
    notify.send(f"Kalshi Edge daily ({mode}): equity ${eq['equity']:,.2f} (start ${eq['bankroll']:,.0f}), "
                f"realized ${eq['realized']:+,.2f}, open ${eq['open_cost']:,.2f}. Since paper start: "
                f"{g.get('return_pct', 0):+.2f}% vs S&P {spy}. Go-live test: {'PASSED' if g['passed'] else 'not yet'}.")
    st["last_summary_date"] = today
    save_state(st)


def status() -> Dict[str, Any]:
    s = get_settings()
    cfg = s["autotrader"]
    mode = cfg["mode"]
    st = state()
    eq = equity(mode if mode != "off" else "paper")
    snaps = db.fetch_all(select(db.equity_snapshots).where(db.equity_snapshots.c.mode == (mode if mode != "off" else "paper"))
                         .order_by(db.equity_snapshots.c.ts.desc()).limit(500))
    events = db.fetch_all(select(db.autotrader_events).order_by(db.autotrader_events.c.id.desc()).limit(60))
    for e in events:
        e["hst"] = hst(e["ts"], "%b %d %I:%M %p")
    trades = [dict(t, created_hst=hst(t["created_ts"])) for t in auto_trades(mode if mode != "off" else "paper")
              if t["status"] in ("resting", "open")]
    g = gate()
    return {
        "mode": mode, "killed": bool(st.get("killed")), "kill_reason": st.get("kill_reason"), "kill_hst": hst(st.get("kill_ts")),
        "last_cycle_hst": hst(st.get("last_cycle_ts")), "equity": eq, "day_start": day_start_equity(mode, eq["equity"]),
        "limits": {k: cfg[k] for k in ("bankroll", "max_order_usd", "max_open_usd", "max_orders_per_day",
                                        "max_parlays_per_day", "max_parlay_usd", "daily_loss_limit_usd", "total_stop_usd")},
        "markets": {k: cfg[k] for k in ("trade_nfl_singles", "trade_parlays", "trade_gas", "allow_sports", "parlays_live")},
        "gate": dict(g, checks=[{"label": a, "ok": b, "detail": c} for a, b, c in g["checks"]]),
        "live_blockers": live_blockers(),
        "live_check": st.get("live_check"), "keys": kalshi_auth.configured(), "telegram": notify.configured(),
        "password": bool(config.APP_PASSWORD), "events": events, "positions": trades,
        "equity_series": [{"ts": x["ts"], "equity": x["equity"], "spy": x["spy_close"]} for x in reversed(snaps)],
    }
