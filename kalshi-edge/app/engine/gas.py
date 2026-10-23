"""AAA gas price ladder math: implied median, transparent fair model, trap guard, calibration.

Resolution source for every gas market is the AAA National Average only.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def parse_ticker_date(event_ticker: str) -> Optional[date]:
    """Kalshi event tickers carry the date as YYMMMDD, e.g. KXAAAGASD-26OCT24 -> 2026-10-24."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", event_ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


def mid_of(market: Dict[str, Any]) -> Tuple[Optional[float], str]:
    bid, ask = market.get("yes_bid"), market.get("yes_ask")
    if bid is not None and ask is not None and bid > 0 and ask < 1 and ask >= bid:
        return (bid + ask) / 2.0, "mid"
    if bid is not None and ask is not None and bid > 0 and ask >= 1:
        return bid, "bid (no ask)"
    lp = market.get("last_price")
    if lp is not None and 0 < lp < 1:
        return lp, "last"
    if ask is not None and 0 < ask < 1 and (bid is None or bid == 0):
        return ask / 2.0, "half ask (no bid)"
    return None, "none"


def pava_nonincreasing(values: Sequence[float]) -> List[float]:
    """Pool-adjacent-violators: closest non-increasing sequence (equal weights)."""
    blocks: List[List[float]] = []  # [sum, count]
    for v in values:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    out: List[float] = []
    for s, c in blocks:
        out.extend([s / c] * c)
    return out


def ladder_points(markets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert one event's markets into survival points P(X > K), sorted by strike."""
    thresh, buckets = [], []
    for m in markets:
        mid, basis = mid_of(m)
        st = (m.get("strike_type") or "").lower()
        if mid is None:
            continue
        if st in ("greater", "greater_or_equal") and m.get("floor_strike") is not None:
            thresh.append((float(m["floor_strike"]), mid, m["ticker"], basis))
        elif st in ("less", "less_or_equal") and m.get("cap_strike") is not None:
            thresh.append((float(m["cap_strike"]), 1.0 - mid, m["ticker"], basis))
        elif st == "between":
            buckets.append((m.get("floor_strike"), m.get("cap_strike"), mid, m["ticker"], basis))
    if thresh:
        thresh.sort(key=lambda x: x[0])
        raw = [p for _, p, _, _ in thresh]
        fit = pava_nonincreasing(raw)
        return {"kind": "threshold", "strikes": [k for k, _, _, _ in thresh], "survival_raw": raw,
                "survival": fit, "tickers": [t for _, _, t, _ in thresh], "basis": [b for *_, b in thresh]}
    if buckets:
        buckets.sort(key=lambda b: (b[0] if b[0] is not None else -1e9))
        total = sum(b[2] for b in buckets)
        if total <= 0:
            return {"kind": "empty", "strikes": [], "survival": []}
        cum, strikes, surv = 0.0, [], []
        for lo, hi, p, _, _ in buckets:
            cum += p / total
            if hi is not None:
                strikes.append(float(hi))
                surv.append(max(0.0, 1.0 - cum))
        return {"kind": "bucket", "strikes": strikes, "survival_raw": surv, "survival": pava_nonincreasing(surv),
                "tickers": [b[3] for b in buckets], "bucket_sum": total}
    return {"kind": "empty", "strikes": [], "survival": []}


def implied_median(strikes: Sequence[float], survival: Sequence[float]) -> Tuple[Optional[float], str]:
    """Strike where P(X > K) crosses 0.5, by linear interpolation between adjacent strikes."""
    if not strikes:
        return None, "no strikes"
    pts = list(zip(strikes, survival))
    if all(s > 0.5 for _, s in pts):
        return None, f"above the top strike ({pts[-1][0]:.3f})"
    if all(s < 0.5 for _, s in pts):
        return None, f"below the bottom strike ({pts[0][0]:.3f})"
    for (k1, s1), (k2, s2) in zip(pts, pts[1:]):
        if s1 >= 0.5 >= s2:
            if s1 == s2:
                return (k1 + k2) / 2.0, "flat at 0.5"
            return k1 + (s1 - 0.5) * (k2 - k1) / (s1 - s2), "interpolated"
    for k, s in pts:
        if s == 0.5:
            return k, "exact"
    return None, "no crossing"


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def daily_changes(history: Sequence[Tuple[date, float]]) -> List[Tuple[date, float]]:
    """Day-over-day changes using only consecutive calendar days."""
    out = []
    h = sorted(history)
    for (d1, v1), (d2, v2) in zip(h, h[1:]):
        if (d2 - d1).days == 1:
            out.append((d2, v2 - v1))
    return out


def trends(history: Sequence[Tuple[date, float]]) -> Dict[str, Any]:
    h = sorted(history)
    if not h:
        return {}
    latest_d, latest_v = h[-1]
    by_date = dict(h)
    out: Dict[str, Any] = {"latest_date": latest_d.isoformat(), "latest": latest_v}
    for n in (1, 3, 7, 14):
        prev = by_date.get(date.fromordinal(latest_d.toordinal() - n))
        out[f"chg_{n}d"] = round(latest_v - prev, 4) if prev is not None else None
    ch = daily_changes(h)
    if len(ch) >= 4:
        last = ch[-1][1]
        prior = mean(c for _, c in ch[-4:-1])
        out["decelerating"] = prior > 0 and 0 <= last < prior
        out["last_daily"] = round(last, 4)
        out["prior3_avg_daily"] = round(prior, 4)
    return out


def forecast_errors(changes: Sequence[float], drift_weight: float) -> List[float]:
    """One-day-ahead errors of the drift rule itself: change[i] - drift_weight * mean(change[i-3:i])."""
    return [changes[i] - drift_weight * mean(changes[i - 3:i]) for i in range(3, len(changes))]


def model_distribution(history: Sequence[Tuple[date, float]], resolution: date, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Normal model for the AAA value on `resolution` from official history only.

    mean  = latest + drift_weight * mean(last 3 daily changes) * horizon_days
    sigma = max(RMS of the drift rule's own past one-day forecast errors, floor) * sqrt(horizon_days)
    """
    h = sorted(history)
    if not h:
        return {"ok": False, "why": "no AAA history"}
    ch = daily_changes(h)
    need = int(cfg["min_history_days"])
    if len(ch) < need:
        return {"ok": False, "why": f"only {len(ch)} daily AAA changes on file; need {need}"}
    a0_date, a0 = h[-1]
    horizon = (resolution - a0_date).days
    if horizon <= 0:
        known = dict(h).get(resolution)
        return {"ok": known is not None, "known": known, "horizon": horizon, "a0": a0, "a0_date": a0_date.isoformat(),
                "why": "AAA value for the resolution date is already published" if known is not None
                else "resolution date is not after the latest AAA reading"}
    dw = float(cfg["drift_weight"])
    changes = [c for _, c in ch]
    errs = forecast_errors(changes, dw)[-int(cfg["sigma_lookback_days"]):]
    rms = math.sqrt(mean(e * e for e in errs)) if errs else 0.0
    last3 = changes[-3:]
    drift = dw * mean(last3)
    sd = max(rms, float(cfg["sigma_floor"]))
    mu = a0 + drift * horizon
    sigma = sd * math.sqrt(horizon)
    steps = [
        f"Latest AAA {a0:.3f} on {a0_date.isoformat()}; resolves {resolution.isoformat()} ({horizon} day(s) ahead)",
        f"Drift = {dw:g} x mean(last 3 daily changes {', '.join(f'{c:+.4f}' for c in last3)}) = {drift:+.4f}/day",
        f"Daily sigma = RMS of the drift rule's last {len(errs)} one-day forecast errors = {rms:.4f} "
        f"(floor {float(cfg['sigma_floor']):g}) -> {sd:.4f}",
        f"Model: mean {mu:.4f}, sigma {sigma:.4f}",
    ]
    return {"ok": True, "mu": mu, "sigma": sigma, "a0": a0, "a0_date": a0_date.isoformat(), "horizon": horizon,
            "drift": drift, "sd_daily": sd, "steps": steps}


def market_yes_probs(markets: List[Dict[str, Any]]) -> Dict[str, float]:
    """Kalshi's own P(YES) per ticker from the monotone-smoothed ladder (mid prices)."""
    lad = ladder_points(markets)
    out: Dict[str, float] = {}
    by_ticker = {m["ticker"]: m for m in markets}
    if lad["kind"] == "threshold":
        for t, s in zip(lad["tickers"], lad["survival"]):
            st = (by_ticker[t].get("strike_type") or "").lower()
            out[t] = s if st.startswith("greater") else 1.0 - s
    elif lad["kind"] == "bucket":
        total = lad.get("bucket_sum") or 0
        for m in markets:
            mid, _ = mid_of(m)
            if mid is not None and total > 0:
                out[m["ticker"]] = mid / total
    return out


def blend_fair(model_p: Optional[float], market_p: Optional[float], weight: float, clamp: float = 0.01) -> Optional[float]:
    """fair = weight * clamp(model) + (1 - weight) * Kalshi smoothed probability."""
    if model_p is None:
        return None
    mp = min(max(model_p, clamp), 1 - clamp)
    if market_p is None:
        return mp
    return weight * mp + (1 - weight) * market_p


def model_prob(market: Dict[str, Any], dist: Dict[str, Any]) -> Optional[float]:
    """P(YES) under the model for threshold or bucket markets."""
    st = (market.get("strike_type") or "").lower()
    fl, cp = market.get("floor_strike"), market.get("cap_strike")
    if dist.get("known") is not None:
        x = dist["known"]
        if st in ("greater",):
            return 1.0 if x > fl else 0.0
        if st == "greater_or_equal":
            return 1.0 if x >= fl else 0.0
        if st in ("less",):
            return 1.0 if x < cp else 0.0
        if st == "less_or_equal":
            return 1.0 if x <= cp else 0.0
        if st == "between":
            return 1.0 if (fl is None or x >= fl) and (cp is None or x <= cp) else 0.0
        return None
    mu, sg = dist["mu"], dist["sigma"]

    def above(k):
        return 1.0 - normal_cdf((k - mu) / sg)

    if st in ("greater", "greater_or_equal") and fl is not None:
        return above(fl)
    if st in ("less", "less_or_equal") and cp is not None:
        return 1.0 - above(cp)
    if st == "between":
        lo = above(fl) if fl is not None else 1.0
        hi = above(cp) if cp is not None else 0.0
        return max(0.0, lo - hi)
    return None


def winning_region(market: Dict[str, Any], side: str) -> Tuple[Optional[float], Optional[float]]:
    """(low, high) of AAA values where `side` wins. None means unbounded. Complement sides of a
    bucket are two regions, which we report as (None, None) (not a one-direction bet)."""
    st = (market.get("strike_type") or "").lower()
    fl, cp = market.get("floor_strike"), market.get("cap_strike")
    if st in ("greater", "greater_or_equal"):
        return (fl, None) if side == "yes" else (None, fl)
    if st in ("less", "less_or_equal"):
        return (None, cp) if side == "yes" else (cp, None)
    if st == "between" and side == "yes":
        return (fl, cp)
    return (None, None)


def deceleration_trap(market: Dict[str, Any], side: str, latest: float, tr: Dict[str, Any]) -> Optional[str]:
    """Block bets that only win if the price reverses against the recent trend.

    Rising trend (3-day change > 0): a bet that wins only below today's AAA needs an outright fall.
    A slowing rise is not a fall. Mirror rule for a falling trend.
    """
    chg3 = tr.get("chg_3d")
    if chg3 is None:
        return None
    lo, hi = winning_region(market, side)
    if chg3 > 0 and hi is not None and hi < latest:
        slow = " The rise is slowing, but" if tr.get("decelerating") else ""
        return (f"DECELERATION TRAP: AAA is up {100 * chg3:.1f}¢ over 3 days.{slow} this bet only wins if the "
                f"average falls below {hi:.3f} from {latest:.3f}. A slowing rise is not a fall, and Kalshi's implied "
                f"medians have been well calibrated. Not recommended.")
    if chg3 < 0 and lo is not None and lo > latest:
        return (f"REVERSAL TRAP: AAA is down {100 * -chg3:.1f}¢ over 3 days; this bet only wins if the average "
                f"rises above {lo:.3f} from {latest:.3f}. A slowing fall is not a rise. Not recommended.")
    return None


def calibration_stats(records: Sequence[Dict[str, Any]], key: str = "implied_median") -> Dict[str, Any]:
    """How well a median forecast matched the AAA outcome.

    bias = mean(actual - median); pct_above = share of outcomes above the median (50% if calibrated).
    """
    pairs = [(r[key], r["actual"]) for r in records if r.get(key) is not None and r.get("actual") is not None]
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    errs = [a - m for m, a in pairs]
    above = sum(1 for m, a in pairs if a > m)
    ties = sum(1 for m, a in pairs if a == m)
    return {
        "n": n,
        "bias": round(mean(errs), 4),
        "mae": round(mean(abs(e) for e in errs), 4),
        "pct_above": round(100.0 * (above + 0.5 * ties) / n, 1),
        "within_1c": round(100.0 * sum(1 for e in errs if abs(e) <= 0.01 + 1e-9) / n, 1),
        "within_2c": round(100.0 * sum(1 for e in errs if abs(e) <= 0.02 + 1e-9) / n, 1),
    }


def to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()
