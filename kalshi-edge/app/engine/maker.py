"""Maker (resting) price: the limit price we would post without crossing the spread.

Kalshi publishes each market's valid price grid as price_ranges bands of (start, end, step); prices off
the grid are rejected. Whole cents are valid everywhere, so 1 cent is the fallback step. The grids are
symmetric around 50 cents, so the same bands apply to NO prices.
"""
from __future__ import annotations

import json
from typing import List, Optional, Sequence, Tuple, Union

Ranges = Optional[Union[str, Sequence[Sequence[float]]]]


def _ranges(r: Ranges) -> List[Sequence[float]]:
    if not r:
        return []
    if isinstance(r, str):
        try:
            return json.loads(r)
        except ValueError:
            return []
    return list(r)


def tick_at(ranges: Ranges, price: float, default: float = 0.01) -> float:
    """Grid step for a price. At a band boundary, the finer of the two bands applies."""
    steps = [st for a, b, st in _ranges(ranges) if a - 1e-9 <= price <= b + 1e-9]
    return min(steps) if steps else default


def _snap(x: float, step: float) -> float:
    return round(round(x / step) * step, 4)


def maker_price(best_bid: Optional[float], best_ask: Optional[float], tick: float = 0.01,
                improve_ticks: int = 1, ranges: Ranges = None) -> Tuple[Optional[float], str]:
    """Price to rest a buy order at for one side of a market.

    - Improve the best bid by `improve_ticks` grid steps, but stay at least one step under the ask.
    - Spread of one step: join the bid.
    - No bid: rest one step under the ask (thin book).
    - No ask: improve the bid.
    - Neither: no price (we never invent one).
    """
    tick = tick or 0.01
    bid = best_bid if best_bid and best_bid > 0 else None
    ask = best_ask if best_ask and best_ask < 1 else None

    def step(p: float) -> float:
        return tick_at(ranges, p, tick) if ranges else tick

    if bid is None and ask is None:
        return None, "No bid or ask on this side; no maker price."
    if bid is None:
        st = step(ask)
        p = _snap(ask - st, st)
        if p <= 0:
            return None, "Ask is at the minimum tick; nothing to rest under it."
        return p, f"Empty bid. Rest one step ({st:g}) under the {ask:.4g} ask (thin book, fill unlikely)."
    sb = step(bid)
    if ask is None:
        p = _snap(min(bid + improve_ticks * sb, 1 - sb), sb)
        return p, f"No ask. Improve the {bid:.4g} bid by {improve_ticks} step ({sb:g})."
    target = bid + improve_ticks * sb
    ceiling = ask - step(ask)
    if target <= ceiling + 1e-9:
        return _snap(target, sb), f"Improve the {bid:.4g} bid by {improve_ticks} step ({sb:g}); ask {ask:.4g}."
    return _snap(bid, sb), f"Spread is one step ({bid:.4g}/{ask:.4g}); join the bid."


def side_quotes(yes_bid: Optional[float], yes_ask: Optional[float], no_bid: Optional[float],
                no_ask: Optional[float]):
    """Best bid/ask for YES and NO, deriving the missing side from the other (NO bid = 1 - YES ask)."""
    nb = no_bid if no_bid is not None else (round(1 - yes_ask, 4) if yes_ask is not None else None)
    na = no_ask if no_ask is not None else (round(1 - yes_bid, 4) if yes_bid is not None else None)
    return {"yes": (yes_bid, yes_ask), "no": (nb, na)}
