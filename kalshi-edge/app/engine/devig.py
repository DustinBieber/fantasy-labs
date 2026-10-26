"""Sportsbook odds -> fair probability (proportional devig), with the math spelled out."""
from __future__ import annotations

from statistics import mean, median
from typing import Dict, List, Sequence


def american_to_implied(odds: float) -> float:
    o = float(odds)
    if o == 0 or -100 < o < 100:
        raise ValueError(f"invalid American odds: {odds}")
    if o < 0:
        return -o / (-o + 100.0)
    return 100.0 / (o + 100.0)


def implied_to_american(p: float) -> int:
    if not 0 < p < 1:
        raise ValueError("probability must be between 0 and 1")
    if p >= 0.5:
        return int(round(-100.0 * p / (1 - p)))
    return int(round(100.0 * (1 - p) / p))


def devig_proportional(implied: Sequence[float]) -> List[float]:
    total = sum(implied)
    if total <= 0:
        raise ValueError("implied probabilities must sum to > 0")
    return [p / total for p in implied]


def devig_two_way(home_odds: float, away_odds: float) -> Dict:
    """Proportional devig of a two-way moneyline. Returns fair probs plus readable steps."""
    ih = american_to_implied(home_odds)
    ia = american_to_implied(away_odds)
    overround = ih + ia
    fh, fa = devig_proportional([ih, ia])
    steps = [
        f"Home {home_odds:+.0f} -> implied {ih:.4f}; Away {away_odds:+.0f} -> implied {ia:.4f}",
        f"Sum = {overround:.4f} (vig {100 * (overround - 1):.2f}%)",
        f"Fair home = {ih:.4f} / {overround:.4f} = {fh:.4f}; fair away = {ia:.4f} / {overround:.4f} = {fa:.4f}",
    ]
    return {"home": fh, "away": fa, "overround": overround, "implied_home": ih, "implied_away": ia, "steps": steps}


def consensus(fair_probs: Sequence[float], method: str = "median") -> float:
    if not fair_probs:
        raise ValueError("no probabilities to combine")
    return float(median(fair_probs) if method == "median" else mean(fair_probs))
