"""Kalshi trading fees.

Fee model (Kalshi fee schedule + API docs):
  taker fee = 0.07 x fee_multiplier x C x P x (1 - P)
  maker fee = taker fee x maker ratio, where the ratio depends on the fee type:
      quadratic                        -> 0     (no maker fee)
      quadratic_with_maker_fees        -> 0.25  (0.0175 at multiplier 1)
      quadratic_with_combo_maker_fees  -> 0.5
  An event-level fee_type_override / fee_multiplier_override takes precedence over the series.

Rounding (docs: Fee Rounding): the trade fee is rounded up to $0.000001 and a rounding fee/rebate
accumulator settles the net fee to the member's balance precision: $0.0001 for direct members,
$0.01 for non-direct members. We round each order's fee UP to that precision.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any, Dict, Optional


@dataclass
class FeeRates:
    taker_rate: float
    maker_rate: float
    basis: str  # human-readable explanation of where the rates came from
    precision: float = 0.0001


def kalshi_fee(contracts: int, price: float, rate: float, precision: float = 0.0001) -> float:
    """Fee in dollars for one order of `contracts` at `price` (0-1), rounded up to `precision`."""
    if contracts <= 0 or rate <= 0:
        return 0.0
    if not 0.0 < price < 1.0:
        return 0.0
    p = Decimal(str(price))
    raw = Decimal(str(rate)) * Decimal(contracts) * p * (Decimal(1) - p)
    return float(raw.quantize(Decimal(str(precision)), rounding=ROUND_CEILING))


def fee_per_contract_unrounded(price: float, rate: float) -> float:
    return rate * price * (1.0 - price)


def fee_rates_for(series_ticker: Optional[str], series_row: Optional[Dict[str, Any]], fee_cfg: Dict[str, Any],
                  event_row: Optional[Dict[str, Any]] = None) -> FeeRates:
    base = float(fee_cfg["taker_rate"])
    precision = float(fee_cfg.get("rounding_precision", 0.0001))
    ratios = fee_cfg.get("maker_ratio_by_fee_type") or {}
    st = (series_ticker or "").upper()
    notes = []

    fee_type, mult, origin = None, None, None
    if event_row and event_row.get("fee_type_override"):
        fee_type, mult, origin = event_row["fee_type_override"], event_row.get("fee_multiplier_override"), "event override"
    elif series_row and series_row.get("fee_type"):
        fee_type, mult, origin = series_row["fee_type"], series_row.get("fee_multiplier"), "series"

    if fee_type in ratios:
        m = float(mult) if mult is not None and 0 <= float(mult) <= 5 else 1.0
        taker = base * m
        maker = taker * float(ratios[fee_type])
        notes.append(f"{origin} fee_type {fee_type}, multiplier {m:g} -> taker {taker:g}, "
                     f"maker {float(ratios[fee_type]):g} x taker")
    else:
        is_index = any(st.startswith(p.upper()) for p in fee_cfg.get("index_series_prefixes", []))
        taker = float(fee_cfg.get("index_taker_rate", base)) if is_index else base
        if is_index:
            notes.append(f"index market taker rate {taker:g}")
        if fee_type:
            notes.append(f"{origin} fee_type '{fee_type}' has no coded maker rule; using conservative defaults")
        elif fee_cfg.get("assume_maker_fee_when_unknown", True):
            notes.append("series fee type not loaded yet; maker fee assumed (conservative)")
        maker = float(fee_cfg["maker_rate"]) if (fee_type or fee_cfg.get("assume_maker_fee_when_unknown", True)) else 0.0

    override = (fee_cfg.get("series_overrides") or {}).get(st)
    if override:
        taker = float(override.get("taker_rate", taker))
        maker = float(override.get("maker_rate", maker))
        notes.append("manual series override")
    notes.append(f"rounded up to ${precision:g}")
    return FeeRates(taker_rate=round(taker, 6), maker_rate=round(maker, 6), basis="; ".join(notes), precision=precision)
