"""NFL parlay tickets (Kalshi Combos), built only from legs that are +EV against our fair price.

Kalshi prices combos by market-maker quote after you build them in the app, and requesting a quote
needs a logged-in account, so this app never requests or accepts one. Each ticket tells you exactly
which legs to add, our fair price, our estimate of the quote, and the highest quote worth accepting.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from sqlalchemy import select

from .. import db
from ..engine import parlay as pm
from ..engine.fees import kalshi_fee
from ..engine.kelly import size_position
from ..engine.tiers import assign_tier, size_multiplier
from ..settings_store import get_settings
from ..timeutil import hst
from ..engine import lines as ln
from ..engine.gas import parse_ticker_date
from . import lines_service, nfl_service, portfolio


def _leg(o: Dict[str, Any], leg_type: str, label: str, game: str, game_title: str) -> Dict[str, Any]:
    return {"ticker": o["ticker"], "event_ticker": o["event_ticker"], "game": game, "game_title": game_title,
            "leg_type": leg_type, "label": label, "fair": o["fair"], "ask": o["best_ask"], "maker": o.get("maker_price"),
            "expert": o["expert"]["status"], "expert_note": o["expert"]["note"],
            "fair_source": (o.get("fair_info") or {}).get("plain"), "fair_kind": o.get("fair_kind", "exact"),
            "close_ts": o.get("close_ts"), "close_hst": hst(o.get("close_ts")),
            "leg_edge_pts": round(100 * (o["fair"] - o["best_ask"]), 2)}


def _usable(o: Dict[str, Any]) -> bool:
    if o["side"] != "yes" or o.get("fair") is None or o.get("best_ask") is None:
        return False
    reason = o.get("tier_reason") or ""
    return not (o["tier"] == "P" and ("stale" in reason or "started" in reason))


def _legs_from_board(board: Dict[str, Any], lines: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    types = set(cfg.get("leg_types", ["moneyline", "spread", "total", "prop"]))
    legs = []
    if "moneyline" in types:
        for o in board["opportunities"]:
            if _usable(o):
                teams = ln.split_matchup(o["event_ticker"])
                d = parse_ticker_date(o["event_ticker"])
                game = f"{'-'.join(sorted(teams))}|{d.isoformat()}" if teams and d else o["event_ticker"]
                legs.append(_leg(o, "moneyline", o["subject"], game, o.get("game_title") or ""))
    for o in lines["opportunities"]:
        if o.get("leg_type") not in types or not _usable(o):
            continue
        if o["fair_kind"] == "model" and not cfg.get("allow_model_legs", False):
            continue
        if o["fair_kind"] == "scaled" and not cfg.get("allow_scaled_props", True):
            continue
        legs.append(_leg(o, o["leg_type"], o["leg_label"], o["game"], o.get("game_title") or ""))
    return legs


def board() -> Dict[str, Any]:
    s = get_settings()
    cfg, t = s["parlays"], s["tiers"]
    fees = s["fees"]
    rate, precision = float(fees["taker_rate"]), float(fees.get("rounding_precision", 0.0001))
    bank = portfolio.summary()["bankroll"]
    exposure = portfolio.exposure_by_key()
    room = s["correlated_cap_pct"] / 100.0 * bank - exposure.get("COMBO", 0.0)
    nfl = nfl_service.board(include_all_sides=True)
    lines = lines_service.board()
    legs = _legs_from_board(nfl, lines, cfg)
    pool = pm.candidate_legs(legs, int(cfg["candidate_legs"]))

    def fee(c, p, r):
        return kalshi_fee(c, p, r, precision)

    tickets = []
    for c in pm.build(legs, cfg, rate)[: int(cfg["show"])]:
        tier, why = assign_tier(c["edge_pts"], c["net_ev_pct"], c["expert"], t)
        leg_rooms = [s["correlated_cap_pct"] / 100.0 * bank
                     - exposure.get(portfolio.corr_key(g["event_ticker"].split("-")[0], g["event_ticker"]), 0.0)
                     for g in c["legs"]]
        kinds = {g["fair_kind"] for g in c["legs"]}
        size = size_position(q=c["fair"], price=c["est_quote"], fee_rate=rate, bankroll=bank,
                             kelly_mult=float(s["kelly_fraction"]), tier_mult=size_multiplier(tier, t),
                             per_strike_cap_pct=float(s["per_strike_cap_pct"]), correlated_room=min([room] + leg_rooms),
                             fee_fn=fee)
        n = len(c["legs"])
        product_text = " x ".join(f"{g['fair']:.4f}" for g in c["legs"])
        math_lines = [
            *(f"Leg {i + 1} [{g['leg_type']}]: {g['label']} fair {g['fair']:.4f} ({g['fair_source']}), Kalshi ask "
              f"{g['ask']:.4f}, leg edge {g['leg_edge_pts']:+.1f} pts" for i, g in enumerate(c["legs"])),
            f"Different games, so legs are independent: fair = {product_text} = {c['fair']:.4f}",
            f"Estimated quote = product of leg asks = {c['est_quote']:.4f} (market makers quote off the legs)",
            f"Edge at that quote = {c['edge_pts']:.2f} pts; net EV after the {rate:g} taker fee = {c['net_ev_pct']:.2f}%",
            (f"Highest quote worth accepting (net EV >= {cfg['min_net_ev_pct']:g}%) = {c['max_price']:.4f}"
             if c["max_price"] else f"No quote clears {cfg['min_net_ev_pct']:g}% net EV"),
            *size["steps"], why,
        ]
        setup = [
            "Open the Kalshi app, go to NFL, and start a Combo.",
            *(f"Add leg: {g['label']} ({g['game_title']}, closes {g['close_hst']})" for g in c["legs"]),
            f"Request a quote for {size['contracts'] or 'your'} contracts.",
            (f"Accept only at or below {100 * c['max_price']:.1f}¢ per contract. Our estimate of the quote is "
             f"{100 * c['est_quote']:.1f}¢; fair is {100 * c['fair']:.1f}¢." if c["max_price"]
             else "Do not accept any quote: no price clears your minimum EV."),
            "Log it below with the exact price you got (paper or real).",
        ]
        tickets.append({
            "id": "-".join(g["ticker"] for g in c["legs"]), "legs": c["legs"], "n_legs": n, "fair": round(c["fair"], 4),
            "est_quote": c["est_quote"], "max_price": c["max_price"], "edge_pts": round(c["edge_pts"], 2),
            "net_ev_pct": round(c["net_ev_pct"], 2), "tier": tier, "tier_reason": why, "expert": c["expert"],
            "size": size, "math": math_lines, "setup": setup, "fee_rate": rate, "precision": precision,
            "pays": f"{1 / c['est_quote']:.1f}x" if c["est_quote"] else None,
            "scaled_legs": "scaled" in kinds,
        })
    combos = [dict(tr, legs=json.loads(tr["combo_legs"] or "[]"), created_hst=hst(tr["created_ts"]))
              for tr in db.fetch_all(select(db.trades).where(db.trades.c.combo_legs.isnot(None))
                                     .order_by(db.trades.c.created_ts.desc()))]
    by_type: Dict[str, int] = {}
    for g in legs:
        by_type[g["leg_type"]] = by_type.get(g["leg_type"], 0) + 1
    return {"tickets": tickets, "pool": pool, "legs_considered": len(legs), "legs_by_type": by_type, "combos": combos,
            "min_net_ev_pct": cfg["min_net_ev_pct"],
            "note": ("One leg per game. Legs from the same game (a team's spread and its QB's yards, say) are correlated, "
                     "so multiplying them would misprice the combo; same-game legs wait for a correlation model.")}
