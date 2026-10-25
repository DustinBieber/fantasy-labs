"""Follow proven bettors.

1. discover(): candidates from Polymarket's sports leaderboard, graded on their resolved NFL record.
2. refresh_picks(): open NFL game-winner positions of proven wallets, mapped onto Kalshi tickers.
   These become expert picks, which the tier rule already uses (G needs proven agreement; a proven
   bettor on the other side forces Pass).
3. grade_picks(): when the Kalshi market settles, each pick is graded, building our own record of
   every followed bettor (the rolling grade turns on after 50 picks).
4. feed(): what they hold now, next to the Kalshi price and our fair price. Following is a signal,
   never a reason to chase a price that has already moved past fair.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, insert, select, update

from .. import db
from ..engine import follow as fl
from ..engine.experts import proof_grade, rolling_grade
from ..engine.gas import parse_ticker_date
from ..engine.teams import kalshi_market_team, team_code, team_name
from ..settings_store import get_settings
from ..sources import polymarket
from ..sources.http import FetchError
from ..timeutil import hst, now_ts
from . import scanner
from .portfolio import settlement_yes_value

log = logging.getLogger("kalshi_edge.follow")


def short(wallet: str) -> str:
    return f"{wallet[:6]}…{wallet[-4:]}" if wallet and len(wallet) > 12 else (wallet or "?")


def profile_url(wallet: str) -> str:
    return f"https://polymarket.com/profile/{wallet}"


def _expert_by_wallet(wallet: str) -> Optional[Dict[str, Any]]:
    return db.fetch_one(select(db.experts).where(db.experts.c.wallet == wallet.lower()))


def grade_wallet(wallet: str, name: Optional[str] = None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pull a wallet's closed positions, grade its NFL record, and upsert it into the expert roster."""
    s = settings or get_settings()
    cfg = s["follow"]
    wallet = wallet.strip().lower()
    if not (wallet.startswith("0x") and len(wallet) == 42):
        raise ValueError("Polymarket wallets look like 0x followed by 40 hex characters")
    positions = polymarket.closed_positions(wallet, int(cfg["max_position_pages"]))
    stats = fl.grade(positions, cfg)
    values = dict(fl.expert_fields(stats), name=(name or short(wallet))[:120], sport=cfg["sport"].upper(),
                  proof_url=profile_url(wallet), source="polymarket", wallet=wallet, stats=db.dumps(stats),
                  graded_ts=now_ts(), active=True)
    existing = _expert_by_wallet(wallet)
    with db.get_engine().begin() as c:
        if existing:
            c.execute(update(db.experts).where(db.experts.c.id == existing["id"]).values(**values))
            eid = existing["id"]
        else:
            eid = c.execute(insert(db.experts).values(created_ts=now_ts(), **values)).inserted_primary_key[0]
    return dict(stats, expert_id=int(eid), wallet=wallet)


def discover() -> Dict[str, Any]:
    s = get_settings()
    cfg = s["follow"]
    if not cfg.get("enabled", True):
        return {"skipped": "following is turned off in Settings"}
    candidates: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    for window in cfg["leaderboard_windows"]:
        try:
            for r in polymarket.leaderboard(cfg["leaderboard_category"], window, int(cfg["candidates_per_window"])):
                candidates.setdefault(r["wallet"].lower(), r)
        except FetchError as e:
            errors.append(f"leaderboard {window}: {e}")
    graded = eligible = 0
    for wallet, r in candidates.items():
        existing = _expert_by_wallet(wallet)
        if existing and existing.get("graded_ts") and now_ts() - existing["graded_ts"] < 20 * 3600:
            continue
        try:
            stats = grade_wallet(wallet, r.get("name"), s)
        except FetchError as e:
            errors.append(f"{short(wallet)}: {e}")
            continue
        if stats["nfl_picks"] == 0 and not existing:
            # No NFL record at all: keep the roster clean.
            with db.get_engine().begin() as c:
                c.execute(db.experts.delete().where(db.experts.c.id == stats["expert_id"]))
            continue
        graded += 1
        eligible += 1 if stats["eligible"] else 0
    if errors and not graded and not candidates:
        raise RuntimeError("; ".join(errors[:3]))
    return {"candidates": len(candidates), "graded": graded, "eligible": eligible, "errors": errors[:3]}


def _kalshi_index() -> Dict[Tuple[str, Any], str]:
    s = get_settings()
    idx: Dict[Tuple[str, Any], str] = {}
    for m in scanner.open_markets(s["nfl"]["series"]):
        team = kalshi_market_team(m["ticker"], m.get("yes_sub_title"))
        d = parse_ticker_date(m["event_ticker"])
        if team and d:
            idx[(team, d)] = m["ticker"]
    return idx


def _map_ticker(idx: Dict[Tuple[str, Any], str], team: str, d) -> Optional[str]:
    from datetime import timedelta
    for delta in (0, -1, 1):
        t = idx.get((team, d + timedelta(days=delta)))
        if t:
            return t
    return None


def proven_wallet_experts() -> List[Dict[str, Any]]:
    rows = db.fetch_all(select(db.experts).where(and_(db.experts.c.source == "polymarket",
                                                      db.experts.c.active.is_(True))))
    return [e for e in rows if proof_grade(e)[1]]


def refresh_picks() -> Dict[str, Any]:
    s = get_settings()
    cfg = s["follow"]
    if not cfg.get("enabled", True):
        return {"skipped": "following is turned off in Settings"}
    idx = _kalshi_index()
    now = now_ts()
    wallets = proven_wallet_experts()
    seen_total = mapped = 0
    errors: List[str] = []
    for e in wallets:
        try:
            positions = polymarket.open_positions(e["wallet"])
        except FetchError as err:
            errors.append(f"{short(e['wallet'])}: {err}")
            continue
        seen_refs = set()
        for p in positions:
            if not (fl.is_nfl(p) and fl.is_moneyline(p)):
                continue
            team = team_code(p["outcome"])
            parsed = fl.parse_nfl_slug(p["event_slug"] or p["slug"])
            if not team or not parsed:
                continue
            value = p.get("current_value")
            if value is None and p.get("size") is not None:
                value = p["size"] * (p.get("cur_price") or p.get("avg_price") or 0)
            if (value or 0) < float(cfg["min_position_usd"]):
                continue
            ref = f"{p['event_slug'] or p['slug']}:{team}"
            seen_refs.add(ref)
            ticker = _map_ticker(idx, team, parsed[2])
            mapped += 1 if ticker else 0
            row = {"expert_id": e["id"], "ticker": ticker or f"PM:{ref}", "side": "yes", "price": p.get("avg_price"),
                   "size_usd": round(value or 0, 2), "status": "open", "last_seen_ts": now, "external_ref": ref,
                   "label": f"{team_name(team)} to win ({parsed[2].isoformat()})", "source_url": profile_url(e["wallet"])}
            existing = db.fetch_one(select(db.expert_picks).where(and_(
                db.expert_picks.c.expert_id == e["id"], db.expert_picks.c.external_ref == ref)))
            with db.get_engine().begin() as c:
                if existing:
                    c.execute(update(db.expert_picks).where(db.expert_picks.c.id == existing["id"]).values(**row))
                else:
                    c.execute(insert(db.expert_picks).values(picked_ts=now, first_seen_ts=now, **row))
        seen_total += len(seen_refs)
        # Positions they exited before the game no longer count as agreement.
        with db.get_engine().begin() as c:
            for old in c.execute(select(db.expert_picks).where(and_(
                    db.expert_picks.c.expert_id == e["id"], db.expert_picks.c.status == "open",
                    db.expert_picks.c.result.is_(None)))).fetchall():
                if old.external_ref and old.external_ref not in seen_refs:
                    c.execute(update(db.expert_picks).where(db.expert_picks.c.id == old.id).values(status="closed"))
    if errors and not seen_total and len(errors) == len(wallets):
        raise RuntimeError("; ".join(errors[:3]))
    return {"wallets": len(wallets), "picks": seen_total, "mapped_to_kalshi": mapped, "errors": errors[:3]}


def grade_picks() -> Dict[str, int]:
    """Grade open picks whose Kalshi market has settled: units = profit per unit staked at their price."""
    graded = 0
    for p in db.fetch_all(select(db.expert_picks).where(db.expert_picks.c.result.is_(None))):
        if not p["ticker"] or p["ticker"].startswith("PM:"):
            continue
        m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == p["ticker"]))
        v = settlement_yes_value(m)
        if v is None:
            continue
        value = v if p["side"] == "yes" else 1.0 - v
        price = p["price"] if p["price"] and 0 < p["price"] < 1 else 0.5
        result = "win" if value >= 0.999 else ("loss" if value <= 0.001 else "push")
        with db.get_engine().begin() as c:
            c.execute(update(db.expert_picks).where(db.expert_picks.c.id == p["id"]).values(
                result=result, units=round((value - price) / price, 3)))
        graded += 1
    return {"graded_picks": graded}


def feed(nfl_board: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    import json
    from . import nfl_service
    board = nfl_board or nfl_service.board(include_all_sides=True)
    opps = {(o["ticker"], o["side"]): o for o in board["opportunities"]}
    experts = {e["id"]: e for e in db.fetch_all(select(db.experts).where(db.experts.c.source == "polymarket"))}
    wallets = []
    for e in experts.values():
        grade, weight, why = proof_grade(e)
        picks = db.fetch_all(select(db.expert_picks).where(db.expert_picks.c.expert_id == e["id"]))
        wallets.append({"id": e["id"], "name": e["name"], "wallet": e["wallet"], "short": short(e["wallet"]),
                        "profile": e["proof_url"], "grade": grade, "proven": weight, "why": why,
                        "stats": json.loads(e["stats"] or "{}"), "graded_hst": hst(e["graded_ts"]),
                        "rolling": rolling_grade(picks), "open_picks": sum(1 for p in picks if p["status"] == "open"
                                                                           and not p["result"])})
    wallets.sort(key=lambda w: (not w["proven"], -(w["stats"].get("units") or 0)))
    rows = []
    for p in db.fetch_all(select(db.expert_picks).where(and_(db.expert_picks.c.status == "open",
                                                             db.expert_picks.c.result.is_(None)))):
        e = experts.get(p["expert_id"])
        if not e:
            continue
        o = opps.get((p["ticker"], p["side"]))
        if p["ticker"].startswith("PM:"):
            verdict = "No matching Kalshi game market open yet."
        elif not o:
            verdict = "Kalshi market found, but no fair price yet: add a line or ODDS_API_KEY."
        elif o["tier"] == "P" and ("stale" in o["tier_reason"] or "started" in o["tier_reason"]):
            verdict = o["tier_reason"]
        elif (o.get("raw_edge_pts") or 0) > 0:
            verdict = (f"Kalshi still below our fair price: rest a bid at {100 * o['maker_price']:.1f}¢ "
                       f"({o['tier']}).")
        else:
            verdict = "Kalshi price is already at or past our fair price: don't chase."
        rows.append({"pick_id": p["id"], "expert_id": e["id"], "name": e["name"], "short": short(e["wallet"]),
                     "proven": proof_grade(e)[1], "label": p["label"], "ticker": p["ticker"], "side": p["side"],
                     "their_price": p["price"], "size_usd": p["size_usd"], "first_seen_hst": hst(p["first_seen_ts"]),
                     "kalshi_bid": o and o.get("best_bid"), "kalshi_ask": o and o.get("best_ask"),
                     "maker": o and o.get("maker_price"), "fair": o and o.get("fair"),
                     "edge_pts": o and o.get("raw_edge_pts"), "net_ev_pct": o and o.get("net_ev_pct"),
                     "tier": o and o.get("tier"), "verdict": verdict})
    consensus: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not r["proven"]:
            continue
        c = consensus.setdefault(r["ticker"], {"ticker": r["ticker"], "label": r["label"], "wallets": 0, "usd": 0.0})
        c["wallets"] += 1
        c["usd"] += r["size_usd"] or 0
    rows.sort(key=lambda r: (not r["proven"], -(r["size_usd"] or 0)))
    return {"wallets": wallets, "picks": rows,
            "consensus": sorted(consensus.values(), key=lambda c: (-c["wallets"], -c["usd"])),
            "proven_count": sum(1 for w in wallets if w["proven"])}
