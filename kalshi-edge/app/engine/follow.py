"""Grade bettors from their public record, and read their NFL picks.

Proof rule (yours): pick weight only with 200+ graded picks or a full season in this exact sport,
with positive units. A wallet's record here is graded by this app from its resolved Polymarket
positions, so the proof type is 'independent'. Wallets that routinely hold both sides of the same
game look like market makers, not bettors: context only.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .teams import team_code

_SLUG = re.compile(r"^nfl-([a-z]{2,4})-([a-z]{2,4})-(\d{4})-(\d{2})-(\d{2})")
_MONEYLINE_TITLE = re.compile(r"^[A-Za-z .'&-]+ vs\.? [A-Za-z .'&-]+$", re.I)


def parse_nfl_slug(slug: str) -> Optional[Tuple[str, str, date]]:
    """nfl-dal-den-2026-10-26 -> ('dal', 'den', date(2026, 10, 26))."""
    m = _SLUG.match(slug or "")
    if not m:
        return None
    try:
        return m.group(1), m.group(2), date(int(m.group(3)), int(m.group(4)), int(m.group(5)))
    except ValueError:
        return None


def is_nfl(p: Dict[str, Any]) -> bool:
    return (p.get("event_slug") or p.get("slug") or "").startswith("nfl-")


def is_moneyline(p: Dict[str, Any]) -> bool:
    """Game-winner market: the outcome is a team and the title is just 'Team vs. Team'."""
    title = (p.get("title") or "").strip()
    return bool(team_code(p.get("outcome")) and _MONEYLINE_TITLE.match(title))


def _season(ts: int) -> int:
    d = datetime.fromtimestamp(ts, tz=timezone.utc)
    return d.year if d.month >= 8 else d.year - 1


def grade(positions: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Grade resolved NFL positions. units = profit / average stake (one unit = one average bet)."""
    nfl = [p for p in positions if is_nfl(p) and p.get("realized_pnl") is not None]
    n = len(nfl)
    wins = sum(1 for p in nfl if p["realized_pnl"] > 0)
    losses = sum(1 for p in nfl if p["realized_pnl"] < 0)
    pnl = round(sum(p["realized_pnl"] for p in nfl), 2)
    stakes = [p["avg_price"] * p["total_bought"] for p in nfl
              if p.get("avg_price") is not None and p.get("total_bought")]
    stake_sum = sum(stakes)
    avg_stake = stake_sum / len(stakes) if stakes else 0.0
    units = round(pnl / avg_stake, 1) if avg_stake > 0 else 0.0
    roi = round(100 * pnl / stake_sum, 1) if stake_sum > 0 else None

    weeks: Dict[int, set] = defaultdict(set)
    for p in nfl:
        ts = p.get("end_ts") or p.get("ts")
        if ts:
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isocalendar()
            weeks[_season(ts)].add((iso[0], iso[1]))
    best_season_weeks = max((len(w) for w in weeks.values()), default=0)
    full_season = best_season_weeks >= int(cfg.get("full_season_weeks", 15))

    outcomes_by_market: Dict[str, set] = defaultdict(set)
    for p in nfl:
        outcomes_by_market[p.get("condition_id") or p.get("slug") or p.get("title")].add(p.get("outcome"))
    hedged = sum(1 for o in outcomes_by_market.values() if len(o) > 1)
    hedge_share = round(hedged / len(outcomes_by_market), 3) if outcomes_by_market else 0.0

    top = sorted((p["realized_pnl"] for p in nfl), reverse=True)[:3]
    top3_share = round(sum(top) / pnl, 2) if pnl > 0 else None

    min_picks = int(cfg.get("min_proof_picks", 200))
    reasons = []
    if n < min_picks and not full_season:
        reasons.append(f"only {n} resolved NFL picks (needs {min_picks}) and no full season")
    if pnl <= 0:
        reasons.append(f"NFL profit is ${pnl:,.0f} (not positive)")
    market_maker = hedge_share > float(cfg.get("max_hedge_share", 0.3))
    if market_maker:
        reasons.append(f"held both sides in {100 * hedge_share:.0f}% of NFL markets (market-maker pattern)")
    return {
        "nfl_picks": n, "wins": wins, "losses": losses, "win_pct": round(100 * wins / (wins + losses), 1) if wins + losses else None,
        "pnl": pnl, "stake": round(stake_sum, 2), "avg_stake": round(avg_stake, 2), "units": units, "roi_pct": roi,
        "best_season_weeks": best_season_weeks, "full_season": full_season, "hedge_share": hedge_share,
        "top3_share": top3_share, "market_maker": market_maker, "eligible": not reasons,
        "why_not": "; ".join(reasons),
    }


def expert_fields(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Map a grade onto the expert roster columns that the proof rule reads."""
    summary = (f"Graded by this app from {stats['nfl_picks']} resolved NFL positions: "
               f"{stats['wins']}-{stats['losses']}, ${stats['pnl']:,.0f} profit, {stats['units']:+.1f} units"
               + (f", ROI {stats['roi_pct']:+.1f}%" if stats.get("roi_pct") is not None else "")
               + (f". Not eligible: {stats['why_not']}." if stats["why_not"] else "."))
    if stats.get("top3_share") and stats["top3_share"] > 0.8:
        summary += " Warning: 3 bets produced most of the profit."
    return {
        "proof_type": "none" if stats["market_maker"] else "independent",
        "proof_picks": stats["nfl_picks"],
        "proof_full_season": stats["full_season"],
        "proof_units": stats["units"],
        "notes": summary,
    }
