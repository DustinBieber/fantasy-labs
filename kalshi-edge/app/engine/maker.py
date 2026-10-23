"""Maker (resting) price: the limit price we would post without crossing the spread."""
from __future__ import annotations

from typing import Optional, Tuple


def _round_tick(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 4)


def maker_price(best_bid: Optional[float], best_ask: Optional[float], tick: float = 0.01,
                improve_ticks: int = 1) -> Tuple[Optional[float], str]:
    """Price to rest a buy order at for one side of a market.

    - Improve the best bid by `improve_ticks` ticks, but stay at least one tick under the ask.
    - Spread of one tick: join the bid.
    - No bid: rest one tick under the ask (thin book).
    - No ask: improve the bid.
    - Neither: no price (we never invent one).
    """
    tick = tick or 0.01
    bid = best_bid if best_bid and best_bid > 0 else None
    ask = best_ask if best_ask and best_ask < 1 else None
    if bid is None and ask is None:
        return None, "No bid or ask on this side; no maker price."
    if bid is None:
        p = _round_tick(ask - tick, tick)
        if p <= 0:
            return None, "Ask is at the minimum tick; nothing to rest under it."
        return p, f"Empty bid. Rest one tick under the {ask:.2f} ask (thin book, fill unlikely)."
    if ask is None:
        p = _round_tick(min(bid + improve_ticks * tick, 1 - tick), tick)
        return p, f"No ask. Improve the {bid:.2f} bid by {improve_ticks} tick."
    target = bid + improve_ticks * tick
    ceiling = ask - tick
    if target <= ceiling + 1e-9:
        return _round_tick(target, tick), f"Improve the {bid:.2f} bid by {improve_ticks} tick (ask {ask:.2f})."
    return _round_tick(bid, tick), f"Spread is one tick ({bid:.2f}/{ask:.2f}); join the bid."


def side_quotes(yes_bid: Optional[float], yes_ask: Optional[float], no_bid: Optional[float],
                no_ask: Optional[float]):
    """Best bid/ask for YES and NO, deriving the missing side from the other (NO bid = 1 - YES ask)."""
    nb = no_bid if no_bid is not None else (round(1 - yes_ask, 4) if yes_ask is not None else None)
    na = no_ask if no_ask is not None else (round(1 - yes_bid, 4) if yes_bid is not None else None)
    return {"yes": (yes_bid, yes_ask), "no": (nb, na)}
