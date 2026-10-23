"""Fractional Kelly sizing for a binary contract that pays $1, with caps and exact fees."""
from __future__ import annotations

import math
from typing import Callable, Dict, Optional


def kelly_binary(q: float, cost: float) -> float:
    """Full-Kelly bankroll fraction for paying `cost` per contract that returns $1 with probability q."""
    if cost <= 0 or cost >= 1 or q <= cost:
        return 0.0
    return (q - cost) / (1.0 - cost)


def size_position(
    q: float,
    price: float,
    fee_rate: float,
    bankroll: float,
    kelly_mult: float,
    tier_mult: float,
    per_strike_cap_pct: float,
    correlated_room: Optional[float],
    fee_fn: Callable[[int, float, float], float],
) -> Dict:
    """Return contracts, dollars, fee, and the constraint that bound the size.

    cost per contract used in Kelly = price + unrounded fee per contract.
    The final contract count is reduced until price*C + rounded fee fits the stake.
    """
    fee_pc = fee_rate * price * (1.0 - price)
    cost = price + fee_pc
    f_full = kelly_binary(q, cost)
    f_used = f_full * kelly_mult * tier_mult
    kelly_dollars = f_used * bankroll
    cap_dollars = per_strike_cap_pct / 100.0 * bankroll
    limits = {"kelly": kelly_dollars, "per-strike cap": cap_dollars}
    if correlated_room is not None:
        limits["correlated cap room"] = max(0.0, correlated_room)
    binding = min(limits, key=lambda k: limits[k])
    stake = max(0.0, limits[binding])

    contracts = int(math.floor(stake / cost)) if cost > 0 else 0
    fee = fee_fn(contracts, price, fee_rate)
    while contracts > 0 and contracts * price + fee > stake + 1e-9:
        contracts -= 1
        fee = fee_fn(contracts, price, fee_rate)

    dollars = round(contracts * price, 2)
    steps = [
        f"Cost per contract = price {price:.4f} + fee {fee_pc:.4f} = {cost:.4f}",
        f"Full Kelly = (q - cost) / (1 - cost) = ({q:.4f} - {cost:.4f}) / (1 - {cost:.4f}) = {f_full:.4f}",
        f"Used = {f_full:.4f} x Kelly {kelly_mult:g} x tier {tier_mult:g} = {f_used:.4f} of bankroll = ${kelly_dollars:,.2f}",
        f"Per-strike cap {per_strike_cap_pct:g}% = ${cap_dollars:,.2f}"
        + (f"; correlated room ${max(0.0, correlated_room):,.2f}" if correlated_room is not None else ""),
        f"Binding limit: {binding} -> stake ${stake:,.2f} -> {contracts} contracts",
    ]
    return {
        "contracts": contracts,
        "dollars": dollars,
        "fee": fee,
        "max_loss": round(dollars + fee, 2),
        "kelly_full": f_full,
        "kelly_used": f_used,
        "binding": binding,
        "stake_limit": round(stake, 2),
        "steps": steps,
    }
