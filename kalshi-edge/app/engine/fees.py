"""Kalshi trading fees.

fee = round_up_to_cent(rate * C * P * (1 - P)), charged per order.
Rates come from Settings -> fees, refined per series by the public /series fee_type and fee_multiplier.
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


def kalshi_fee(contracts: int, price: float, rate: float) -> float:
    """Fee in dollars for one order of `contracts` at `price` (0-1), rounded up to the next cent."""
    if contracts <= 0 or rate <= 0:
        return 0.0
    if not 0.0 < price < 1.0:
        return 0.0
    p = Decimal(str(price))
    raw = Decimal(str(rate)) * Decimal(contracts) * p * (Decimal(1) - p)
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING))


def fee_per_contract_unrounded(price: float, rate: float) -> float:
    return rate * price * (1.0 - price)


def fee_rates_for(series_ticker: Optional[str], series_row: Optional[Dict[str, Any]], fee_cfg: Dict[str, Any]) -> FeeRates:
    taker = float(fee_cfg["taker_rate"])
    maker = float(fee_cfg["maker_rate"])
    st = (series_ticker or "").upper()
    notes = []

    is_index = any(st.startswith(p.upper()) for p in fee_cfg.get("index_series_prefixes", []))
    if is_index:
        taker = float(fee_cfg.get("index_taker_rate", taker))
        notes.append(f"index market taker rate {taker}")

    fee_type = (series_row or {}).get("fee_type")
    mult = (series_row or {}).get("fee_multiplier")
    if fee_type:
        if mult is not None and 0 < float(mult) <= 5:
            base_taker = float(fee_cfg["taker_rate"])
            taker = base_taker * float(mult)
            maker = float(fee_cfg["maker_rate"]) * float(mult)
            notes.append(f"series fee_multiplier {float(mult):g}")
        if fee_type == "quadratic_with_maker_fees":
            notes.append("series charges maker fees")
        elif fee_type == "quadratic":
            maker = 0.0
            notes.append("series fee_type quadratic: no maker fee")
        else:
            notes.append(f"series fee_type '{fee_type}' not recognized; using defaults")
    else:
        if not fee_cfg.get("assume_maker_fee_when_unknown", True):
            maker = 0.0
            notes.append("series fee type not loaded; maker fee assumed 0 (setting)")
        else:
            notes.append("series fee type not loaded yet; maker fee assumed (conservative)")

    override = (fee_cfg.get("series_overrides") or {}).get(st)
    if override:
        taker = float(override.get("taker_rate", taker))
        maker = float(override.get("maker_rate", maker))
        notes.append("manual series override")

    return FeeRates(taker_rate=round(taker, 6), maker_rate=round(maker, 6), basis="; ".join(notes))
