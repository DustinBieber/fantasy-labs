"""Turn (market, side, fair probability) into a fully explained opportunity."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import tiers as tiers_mod
from .fees import fee_rates_for, kalshi_fee
from .kelly import size_position
from .maker import maker_price, side_quotes

TIER_ORDER = {"S": 0, "G": 1, "W": 2, "P": 3}


def _net_ev_pct(fair: float, price: float, contracts: int, fee: float) -> float:
    cost = contracts * price + fee
    if cost <= 0:
        return 0.0
    return 100.0 * (contracts * fair - cost) / cost


def px(p: float) -> str:
    """Price in dollars with only the precision it has (0.56, 0.565, 0.5655)."""
    t = f"{p:.4f}".rstrip("0")
    return t + "0" * max(0, 2 - len(t.split(".")[1])) if "." in t else f"{p:.2f}"


def cents(p: float) -> str:
    c = round(100 * p, 2)
    return (f"{c:.0f}" if abs(c - round(c)) < 1e-9 else f"{c:g}") + "¢"


def side_label(market: Dict[str, Any], side: str) -> str:
    if side == "yes":
        return market.get("yes_sub_title") or "YES"
    return market.get("no_sub_title") or f"NO on {market.get('yes_sub_title') or market.get('ticker')}"


def evaluate(
    market: Dict[str, Any],
    side: str,
    fair: float,
    fair_info: Dict[str, Any],
    settings: Dict[str, Any],
    bankroll: float,
    series_row: Optional[Dict[str, Any]] = None,
    corr_exposure: float = 0.0,
    expert_status: str = tiers_mod.NONE,
    expert_note: str = "",
    module: str = "",
    warnings: Optional[List[str]] = None,
    force_pass: Optional[str] = None,
    max_tier: Optional[str] = None,
    plain_subject: str = "",
    *,
    event_row: Optional[Dict[str, Any]] = None,
    cap_reason: str = "",
) -> Dict[str, Any]:
    warnings = list(warnings or [])
    t = settings["tiers"]
    quotes = side_quotes(market.get("yes_bid"), market.get("yes_ask"), market.get("no_bid"), market.get("no_ask"))
    bid, ask = quotes[side]
    tick = market.get("tick_size") or 0.01
    mp, maker_note = maker_price(bid, ask, tick, int(settings["maker"]["improve_ticks"]), market.get("price_ranges"))
    rates = fee_rates_for(market.get("series_ticker"), series_row, settings["fees"], event_row)

    def fee(contracts: int, price: float, rate: float) -> float:
        return kalshi_fee(contracts, price, rate, rates.precision)

    base = {
        "ticker": market["ticker"],
        "event_ticker": market.get("event_ticker"),
        "series_ticker": market.get("series_ticker"),
        "category": market.get("category"),
        "module": module,
        "title": market.get("title"),
        "event_title": market.get("event_title"),
        "side": side,
        "side_label": side_label(market, side),
        "subject": plain_subject or side_label(market, side),
        "close_ts": market.get("close_ts"),
        "fair": round(fair, 4) if fair is not None else None,
        "fair_info": fair_info,
        "best_bid": bid,
        "best_ask": ask,
        "maker_price": mp,
        "maker_note": maker_note,
        "taker_price": ask,
        "fee_rates": {"maker": rates.maker_rate, "taker": rates.taker_rate, "basis": rates.basis,
                      "precision": rates.precision},
        "price_ts": market.get("updated_ts"),
        "price_pull_id": market.get("pull_id"),
        "expert": {"status": expert_status, "note": expert_note},
        "warnings": warnings,
    }

    if mp is None or fair is None:
        base.update(tier="P", tier_reason="Pass: no usable price or fair probability.", raw_edge_pts=None,
                    net_ev_pct=None, size={"contracts": 0, "dollars": 0, "fee": 0, "max_loss": 0, "steps": []},
                    reason="No price to post or no fair probability, so nothing to evaluate.", math=[])
        return base

    raw_edge = 100.0 * (fair - mp)
    fee_pc = rates.maker_rate * mp * (1 - mp)
    ev_unrounded = 100.0 * (fair - mp - fee_pc) / (mp + fee_pc)

    corr_room = settings["correlated_cap_pct"] / 100.0 * bankroll - corr_exposure

    def tier_for(ev):
        tier, why = tiers_mod.assign_tier(raw_edge, ev, expert_status, t)
        if force_pass:
            return "P", force_pass
        if max_tier and TIER_ORDER[tier] < TIER_ORDER[max_tier]:
            label = {"W": "Watch", "G": "G", "P": "Pass"}.get(max_tier, max_tier)
            return max_tier, (f"Capped at {label}{f' ({cap_reason})' if cap_reason else ''}. "
                              f"On the numbers alone: {why}")
        return tier, why

    def size_for(tier):
        mult = tiers_mod.size_multiplier(tier, t)
        return size_position(
            q=fair, price=mp, fee_rate=rates.maker_rate, bankroll=bankroll,
            kelly_mult=float(settings["kelly_fraction"]), tier_mult=mult,
            per_strike_cap_pct=float(settings["per_strike_cap_pct"]),
            correlated_room=corr_room, fee_fn=fee,
        )

    tier, why = tier_for(ev_unrounded)
    size = size_for(tier)
    net_ev = ev_unrounded
    if size["contracts"] > 0:
        net_ev = _net_ev_pct(fair, mp, size["contracts"], size["fee"])
        tier2, why2 = tier_for(net_ev)
        if tier2 != tier:
            tier, why = tier2, why2
            size = size_for(tier)
            if size["contracts"] > 0:
                net_ev = _net_ev_pct(fair, mp, size["contracts"], size["fee"])

    # Reference fee/EV for a 100-contract maker order (shown when the recommended size is 0).
    ref_fee = fee(100, mp, rates.maker_rate)
    ref_ev = _net_ev_pct(fair, mp, 100, ref_fee)
    taker_ev = None
    if ask is not None and 0 < ask < 1:
        tf = fee(100, ask, rates.taker_rate)
        taker_ev = round(_net_ev_pct(fair, ask, 100, tf), 2)

    pm, pq = px(mp), px(1 - mp)
    if size["contracts"] > 0:
        c = size["contracts"]
        fee_line = (f"Fee = roundup({rates.maker_rate:g} x {c} x {pm} x {pq}, ${rates.precision:g}) = ${size['fee']:.4f}")
        ev_line = (f"Net EV = ({c} x {fair:.4f} - {c} x {pm} - {size['fee']:.4f}) / ({c} x {pm} + {size['fee']:.4f})"
                   f" = {net_ev:.2f}%")
    else:
        fee_line = (f"Fee per contract = {rates.maker_rate:g} x {pm} x {pq} = ${fee_pc:.4f} "
                    f"(100 contracts: ${ref_fee:.4f}, rounded up per order)")
        ev_line = f"Net EV = ({fair:.4f} - {pm} - {fee_pc:.4f}) / ({pm} + {fee_pc:.4f}) = {net_ev:.2f}%"

    math_lines = list(fair_info.get("steps", [])) + [
        f"Maker price ({side.upper()}): {pm}. {maker_note}",
        f"Raw edge = fair {fair:.4f} - maker {pm} = {raw_edge:.2f} pts",
        f"Fee basis: maker rate {rates.maker_rate:g} ({rates.basis})",
        fee_line,
        ev_line,
    ] + size["steps"] + [why]

    subject = plain_subject or base["side_label"]
    reason = (f"{fair_info.get('plain', 'Our fair estimate')} puts {subject} at {100 * fair:.1f}%. "
              f"You can rest a {side.upper()} bid at {cents(mp)}, so the edge is {raw_edge:.1f} points; "
              f"after Kalshi fees you'd expect {net_ev:+.1f}% on the money risked. {why}")
    if size["contracts"] > 0:
        reason += (f" Size: {size['contracts']} contracts (${size['dollars']:.2f} + ${size['fee']:.2f} fee); "
                   f"max loss ${size['max_loss']:.2f}.")

    base.update(
        tier=tier, tier_reason=why, raw_edge_pts=round(raw_edge, 2), net_ev_pct=round(net_ev, 2),
        ref_net_ev_pct_100=round(ref_ev, 2), taker_net_ev_pct_100=taker_ev, size=size, reason=reason, math=math_lines,
    )
    return base


def sort_key(opp: Dict[str, Any]):
    return (TIER_ORDER.get(opp.get("tier", "P"), 9), -(opp.get("net_ev_pct") or -1e9))
