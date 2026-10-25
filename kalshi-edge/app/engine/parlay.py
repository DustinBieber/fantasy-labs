"""Parlay (Kalshi Combo) math. Legs can be moneylines, spreads, totals, or player props; at most one per game.

A combo pays $1 only if every leg wins. For legs from different games, outcomes are independent, so
fair = product of leg fair probabilities. Kalshi combos are priced by market-maker quotes, which start
from the leg prices; the product of the leg asks is our estimate of the quote. The maximum quote to
accept is the highest price that still clears your minimum net EV after the taker fee.

Only legs that are +EV on their own are used: a parlay of fair-priced legs is not +EV, it is just
higher variance.
"""
from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Dict, List, Optional


def product(xs) -> float:
    return math.prod(xs)


def net_ev_pct(fair: float, price: float, fee_rate: float) -> float:
    fee = fee_rate * price * (1 - price)
    return 100.0 * (fair - price - fee) / (price + fee)


def max_price_for_ev(fair: float, min_ev_pct: float, fee_rate: float, step: float = 0.0001) -> Optional[float]:
    """Highest price on a `step` grid whose net EV is still >= min_ev_pct. None if no price works."""
    lo, hi = 0.0, fair
    if hi <= step or net_ev_pct(fair, step, fee_rate) < min_ev_pct:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if net_ev_pct(fair, mid, fee_rate) >= min_ev_pct:
            lo = mid
        else:
            hi = mid
    p = math.floor(lo / step) * step
    while p > step and net_ev_pct(fair, p, fee_rate) < min_ev_pct:
        p -= step
    return round(p, 4) if p >= step else None


def game_of(leg: Dict[str, Any]) -> str:
    """One leg per game: moneyline, spread, total, and prop markets of the same game share this key."""
    return leg.get("game") or leg["event_ticker"]


def candidate_legs(legs: List[Dict[str, Any]], k: int) -> List[Dict[str, Any]]:
    """+EV legs only (fair above the ask), best per game, top k by fair / ask."""
    good = [g for g in legs if g.get("fair") and g.get("ask") and 0 < g["ask"] < 1 and g["fair"] > g["ask"]
            and g.get("expert") != "disagree"]
    best_per_game: Dict[str, Dict[str, Any]] = {}
    for g in sorted(good, key=lambda g: g["fair"] / g["ask"], reverse=True):
        best_per_game.setdefault(game_of(g), g)
    return sorted(best_per_game.values(), key=lambda g: g["fair"] / g["ask"], reverse=True)[:k]


def build(legs: List[Dict[str, Any]], cfg: Dict[str, Any], fee_rate: float) -> List[Dict[str, Any]]:
    pool = candidate_legs(legs, int(cfg["candidate_legs"]))
    out = []
    for n in range(int(cfg["min_legs"]), int(cfg["max_legs"]) + 1):
        for combo in combinations(pool, n):
            if len({game_of(g) for g in combo}) < n:
                continue
            fair = product(g["fair"] for g in combo)
            quote = math.ceil(product(g["ask"] for g in combo) * 10000 - 1e-9) / 10000
            if quote <= 0 or quote >= 1:
                continue
            experts = {g.get("expert", "none") for g in combo}
            out.append({
                "legs": list(combo),
                "fair": fair,
                "est_quote": quote,
                "edge_pts": 100 * (fair - quote),
                "net_ev_pct": net_ev_pct(fair, quote, fee_rate),
                "max_price": max_price_for_ev(fair, float(cfg["min_net_ev_pct"]), fee_rate),
                "expert": "agree" if experts == {"agree"} else ("disagree" if "disagree" in experts else "none"),
            })
    out.sort(key=lambda c: -c["net_ev_pct"])
    return out
