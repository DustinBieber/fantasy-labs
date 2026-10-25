"""Proven-expert rules.

Pick weight only with a published or independently graded record in that exact sport of
200+ picks or a full season, with positive units. Proof never transfers across sports.
Everyone else is context only (zero weight). Our own rolling grade starts after 50 logged picks.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .tiers import AGREE, DISAGREE, NONE

MIN_PROOF_PICKS = 200
ROLLING_GRADE_MIN = 50


def proof_grade(e: Dict[str, Any]) -> Tuple[str, bool, str]:
    """Return (grade, has_pick_weight, explanation)."""
    ptype = (e.get("proof_type") or "none").lower()
    picks = int(e.get("proof_picks") or 0)
    full = bool(e.get("proof_full_season"))
    units = float(e.get("proof_units") or 0.0)
    if ptype not in ("published", "independent"):
        return "U", False, "No published or independently graded record: context only."
    if picks < MIN_PROOF_PICKS and not full:
        return "U", False, f"Only {picks} graded picks and no full season: context only."
    if units <= 0:
        return "U", False, f"Record is {units:+.1f} units (not positive): context only."
    grade = "A" if ptype == "independent" and picks >= 500 else "B"
    sample = f"{picks} picks" if picks else "a full season"
    return grade, True, f"{ptype.title()} record of {sample} at {units:+.1f} units in {e.get('sport')}."


def rolling_grade(picks: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    graded = [p for p in picks if p.get("result") in ("win", "loss", "push")]
    n = len(graded)
    wins = sum(1 for p in graded if p["result"] == "win")
    losses = sum(1 for p in graded if p["result"] == "loss")
    units = round(sum(float(p.get("units") or 0) for p in graded), 2)
    out = {"graded": n, "wins": wins, "losses": losses, "units": units, "active": n >= ROLLING_GRADE_MIN}
    if n >= ROLLING_GRADE_MIN and wins + losses:
        out["win_pct"] = round(100.0 * wins / (wins + losses), 1)
    return out


def normalize_outcome(ticker: str, side: str, event_pair: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
    """For two-outcome events (NFL game winner), NO on team A == YES on team B."""
    if event_pair and side == "no" and ticker in event_pair:
        return event_pair[ticker], "yes"
    return ticker, side


def expert_status(
    ticker: str, side: str, sport: str, experts: List[Dict[str, Any]], picks: List[Dict[str, Any]],
    event_pair: Optional[Dict[str, str]] = None,
) -> Tuple[str, str]:
    """AGREE / DISAGREE / NONE from proven experts in this sport only."""
    eligible = {e["id"]: e for e in experts
                if e.get("active", True) and (e.get("sport") or "").upper() == sport.upper() and proof_grade(e)[1]}
    want = normalize_outcome(ticker, side, event_pair)
    agree, disagree = [], []
    for p in picks:
        e = eligible.get(p["expert_id"])
        # Graded picks are history, and a position the bettor already exited is no longer a pick.
        if not e or p.get("result") or p.get("status") == "closed":
            continue
        got = normalize_outcome(p["ticker"], p["side"], event_pair)
        if got == want:
            agree.append(e["name"])
        elif got[0] == want[0] or (event_pair and got[0] in event_pair and want[0] in event_pair):
            disagree.append(e["name"])
    if disagree:
        return DISAGREE, "Proven expert(s) on the other side: " + ", ".join(sorted(set(disagree)))
    if agree:
        return AGREE, "Proven expert agreement: " + ", ".join(sorted(set(agree)))
    return NONE, "No proven expert pick on this market."
