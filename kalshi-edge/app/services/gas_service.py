"""AAA gas ladder tracker: AAA pulls, settlement backfill, calibration, ladder board, and edges."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, insert, select

from .. import db
from ..engine import gas as g
from ..engine.edge import evaluate, sort_key
from ..engine.tiers import NONE
from ..settings_store import get_settings
from ..sources import aaa, kalshi
from ..sources.http import FetchError
from ..timeutil import hst, now_ts
from . import portfolio, scanner

log = logging.getLogger("kalshi_edge.gas")
ET = ZoneInfo("America/New_York")
SOURCE_PRIORITY = {"kalshi_settlement": 0, "aaa_site": 1, "manual": 2}


def _store_aaa(d: str, value: float, source: str, pull_id: Optional[int], note: Optional[str] = None) -> None:
    db.upsert(db.aaa_prices, ["date", "source"], {"date": d, "value": round(float(value), 4), "source": source,
                                                    "ts": now_ts(), "pull_id": pull_id, "note": note})


def pull_aaa() -> Dict[str, Any]:
    page, pull_id = aaa.fetch_page()
    parsed = aaa.parse_national_average(page)
    d = parsed["date"] or datetime.now(ET).date().isoformat()
    _store_aaa(d, parsed["value"], "aaa_site", pull_id, db.dumps(parsed["table"]))
    yday = parsed["table"].get("Yesterday Avg.")
    if yday is not None:
        prev = (g.to_date(d) - timedelta(days=1)).isoformat()
        _store_aaa(prev, yday, "aaa_site", pull_id, "from 'Yesterday Avg.' row")
    with db.get_engine().begin() as c:
        c.execute(db.data_pulls.update().where(db.data_pulls.c.id == pull_id).values(rows=1))
    return {"date": d, "value": parsed["value"]}


def add_manual_aaa(d: str, value: float, note: str = "") -> Dict[str, Any]:
    g.to_date(d)
    if not 1.0 < float(value) < 10.0:
        raise ValueError("AAA national average should be in dollars, e.g. 3.123")
    _store_aaa(d, value, "manual", None, note or "entered by hand")
    return {"date": d, "value": float(value)}


def aaa_history() -> List[Tuple[date, float]]:
    """One value per date, preferring the value Kalshi settled on, then the AAA site, then manual."""
    best: Dict[str, Tuple[int, float]] = {}
    for r in db.fetch_all(select(db.aaa_prices)):
        pr = SOURCE_PRIORITY.get(r["source"], 9)
        if r["date"] not in best or pr < best[r["date"]][0]:
            best[r["date"]] = (pr, r["value"])
    return sorted((g.to_date(d), v) for d, (_, v) in best.items())


def aaa_rows(limit: int = 60) -> List[Dict[str, Any]]:
    return db.fetch_all(select(db.aaa_prices).order_by(db.aaa_prices.c.date.desc(), db.aaa_prices.c.source).limit(limit))


def _settled_by_event(series_ticker: str) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[int], List[str]]:
    """Settled markets from the live endpoint plus Kalshi's /historical archive (markets settled before
    the historical cutoff are only served there). Each market is tagged with where it came from."""
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen = set()
    errors: List[str] = []
    last_pull = None
    for label, fetch in (("live", lambda: kalshi.get_markets({"series_ticker": series_ticker, "status": "settled"},
                                                            max_pages=20)),
                         ("historical", lambda: kalshi.get_historical_markets({"series_ticker": series_ticker},
                                                                              max_pages=20))):
        try:
            raw, pulls = fetch()
        except FetchError as e:
            errors.append(f"{series_ticker} {label}: {e}")
            continue
        last_pull = pulls[-1] if pulls else last_pull
        for m in raw:
            pm = kalshi.parse_market(m)
            if pm["ticker"] in seen:
                continue
            seen.add(pm["ticker"])
            pm["_archive"] = label == "historical"
            out[pm["event_ticker"]].append(pm)
    return out, last_pull, errors


def _actual_of(mkts: List[Dict[str, Any]]) -> Optional[float]:
    for m in mkts:
        v = m.get("expiration_value")
        if v:
            try:
                return float(str(v).replace("$", "").replace(",", ""))
            except ValueError:
                continue
    return None


def backfill(settings: Optional[Dict[str, Any]] = None, with_candles: bool = True) -> Dict[str, Any]:
    """Pull settled gas markets: their expiration_value is the AAA print Kalshi settled on.
    Then rebuild each ladder as it stood `calibration_horizon_hours` before close from hourly candles."""
    s = settings or get_settings()
    cfg = s["gas"]
    series_list = sorted(scanner.gas_series_set(s))
    actuals = marks = 0
    errors: List[str] = []
    oldest = now_ts() - int(cfg["calibration_backfill_days"]) * 86400
    # Pass 1: store every AAA print Kalshi settled on, so pass 2 can price each day with full prior history.
    to_grade = []
    for st in series_list:
        events_map, pull_id, errs = _settled_by_event(st)
        errors.extend(errs)
        for ev, mkts in events_map.items():
            d = g.parse_ticker_date(ev)
            actual = _actual_of(mkts)
            if d is None or actual is None:
                continue
            _store_aaa(d.isoformat(), actual, "kalshi_settlement", pull_id, f"{ev} expiration_value")
            actuals += 1
            to_grade.append((st, ev, d, actual, mkts))
    # Pass 2: rebuild each recent ladder as it stood before close and grade it.
    for st, ev, d, actual, mkts in sorted(to_grade, key=lambda x: x[2]):
        close = max((m["close_ts"] or 0) for m in mkts)
        if not with_candles or close < oldest:
            continue
        if db.fetch_one(select(db.ladder_marks.c.id).where(and_(
                db.ladder_marks.c.event_ticker == ev, db.ladder_marks.c.source == "candles"))):
            continue
        as_of = close - int(cfg["calibration_horizon_hours"]) * 3600
        try:
            mark = _ladder_from_candles(st, mkts, as_of)
        except FetchError as e:
            errors.append(f"{ev}: {e}")
            continue
        if mark:
            _store_mark(ev, st, d, as_of, "candles", mark, actual)
            marks += 1
    if errors and not actuals:
        raise RuntimeError("; ".join(errors[:3]))
    return {"actuals": actuals, "calibration_marks": marks, "series": series_list, "errors": errors[:5]}


def _ladder_from_candles(series_ticker: str, mkts: List[Dict[str, Any]], as_of: int) -> Optional[Dict[str, Any]]:
    priced = []
    for m in mkts:
        start, end = as_of - 6 * 3600, as_of + 60
        if m.get("_archive"):
            candles, _ = kalshi.get_historical_candlesticks(m["ticker"], start, end, 60)
        else:
            candles, _ = kalshi.get_candlesticks(series_ticker, m["ticker"], start, end, 60)
        before = [c for c in candles if (c.get("end_period_ts") or 0) <= as_of + 60]
        mid = kalshi.candle_mid(before[-1]) if before else None
        if mid is not None:
            priced.append(dict(m, yes_bid=None, yes_ask=None, last_price=mid))
    return _mark_from_priced(priced)


def _mark_from_priced(priced: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    lad = g.ladder_points(priced)
    if lad["kind"] == "empty":
        return None
    med, how = g.implied_median(lad.get("strikes", []), lad.get("survival", []))
    kp = g.market_yes_probs(priced)
    rows = [{"ticker": m["ticker"], "strike_type": m.get("strike_type"), "floor_strike": m.get("floor_strike"),
             "cap_strike": m.get("cap_strike"), "market_yes": kp.get(m["ticker"])} for m in priced]
    return {"ladder": lad, "median": med, "how": how, "rows": rows}


def history_known_at(hist: List[Tuple[date, float]], as_of: int, before: date) -> List[Tuple[date, float]]:
    """AAA values a trader could have seen at `as_of`: dates before the as-of day (ET), plus the as-of day
    itself once AAA has published it (after 10am ET). Never the resolution date or later."""
    at = datetime.fromtimestamp(as_of, tz=ET)
    return [(dd, v) for dd, v in hist if dd < before and (dd < at.date() or (dd == at.date() and at.hour >= 10))]


def _store_mark(ev: str, st: str, d: date, as_of: int, source: str, mark: Dict[str, Any], actual: float) -> None:
    dist = g.model_distribution(history_known_at(aaa_history(), as_of, d), d, get_settings()["gas"])
    model_med = dist.get("mu") if dist.get("ok") and dist.get("mu") is not None else None
    db.upsert(db.ladder_marks, ["event_ticker", "source"], {
        "event_ticker": ev, "series_ticker": st, "resolution_date": d.isoformat(), "as_of_ts": as_of,
        "source": source, "implied_median": mark["median"], "model_median": model_med, "actual": actual,
        "ladder_json": db.dumps({"strikes": mark["ladder"].get("strikes"), "survival": mark["ladder"].get("survival"),
                                 "how": mark["how"], "rows": mark.get("rows", [])}),
    })


def mark_from_snapshots() -> Dict[str, int]:
    """Our own calibration record: for settled gas events, rebuild the ladder from stored snapshots."""
    s = get_settings()
    horizon = int(s["gas"]["calibration_horizon_hours"]) * 3600
    gs = scanner.gas_series_set(s)
    done = 0
    rows = db.fetch_all(select(db.markets).where(and_(db.markets.c.series_ticker.in_(list(gs)),
                                                      db.markets.c.result.in_(("yes", "no")))))
    by_ev: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for m in rows:
        by_ev[m["event_ticker"]].append(m)
    for ev, mkts in by_ev.items():
        if db.fetch_one(select(db.ladder_marks.c.id).where(and_(db.ladder_marks.c.event_ticker == ev,
                                                                db.ladder_marks.c.source == "snapshots"))):
            continue
        d = g.parse_ticker_date(ev)
        actual = _actual_of(mkts)
        if d is None or actual is None:
            continue
        as_of = max(m["close_ts"] or 0 for m in mkts) - horizon
        priced = []
        for m in mkts:
            snap = db.fetch_one(select(db.market_snapshots).where(and_(
                db.market_snapshots.c.ticker == m["ticker"], db.market_snapshots.c.ts <= as_of))
                .order_by(db.market_snapshots.c.ts.desc()).limit(1))
            if snap and snap["ts"] >= as_of - 3 * 3600:
                priced.append(dict(m, yes_bid=snap["yes_bid"], yes_ask=snap["yes_ask"], last_price=snap["last_price"]))
        mark = _mark_from_priced(priced)
        if not mark:
            continue
        _store_mark(ev, mkts[0]["series_ticker"], d, as_of, "snapshots", mark, actual)
        done += 1
    return {"marks": done}


def calibration() -> Dict[str, Any]:
    marks = db.fetch_all(select(db.ladder_marks).order_by(db.ladder_marks.c.resolution_date))
    return {
        "market": g.calibration_stats(marks, "implied_median"),
        "model": g.calibration_stats(marks, "model_median"),
        "records": [{k: r[k] for k in ("event_ticker", "resolution_date", "source", "implied_median", "model_median",
                                       "actual")} for r in marks[-90:]],
    }


_score_cache: Dict[str, Any] = {"ts": 0, "key": None, "data": None}


def model_score(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Out-of-sample check on settled days: Brier score of fair = w x model + (1 - w) x Kalshi for several w,
    using only AAA values known at the time each ladder was priced. w = 0 is 'just trust Kalshi'."""
    s = settings or get_settings()
    cfg = s["gas"]
    key = db.dumps([cfg, db.fetch_one(select(db.ladder_marks.c.id).order_by(db.ladder_marks.c.id.desc()).limit(1))])
    if _score_cache["key"] == key and now_ts() - _score_cache["ts"] < 600:
        return _score_cache["data"]
    hist = aaa_history()
    records = []
    for r in db.fetch_all(select(db.ladder_marks)):
        rows = (json.loads(r["ladder_json"] or "{}")).get("rows") or []
        if not rows or r["actual"] is None:
            continue
        d = g.to_date(r["resolution_date"])
        dist = g.model_distribution(history_known_at(hist, r["as_of_ts"], d), d, cfg)
        if not dist.get("ok") or dist.get("mu") is None:
            continue
        for row in rows:
            kp = row.get("market_yes")
            mp = g.model_prob(row, dist, float(cfg.get("aaa_tick", 0.0001)))
            y = g.outcome_yes(row, r["actual"])
            if kp is None or mp is None or y is None:
                continue
            records.append((mp, kp, y, r["event_ticker"]))
    data = g.blend_scores(records, float(cfg["model_weight"]), int(cfg.get("min_graded_days", 10)))
    _score_cache.update(ts=now_ts(), key=key, data=data)
    return data


def _resolution_date(ev: str, mkts: List[Dict[str, Any]]) -> Optional[date]:
    d = g.parse_ticker_date(ev)
    if d:
        return d
    exp = max((m.get("expiration_ts") or 0) for m in mkts)
    return datetime.fromtimestamp(exp, tz=ET).date() if exp else None


def board() -> Dict[str, Any]:
    settings = get_settings()
    cfg = settings["gas"]
    gs = scanner.gas_series_set(settings)
    bank = portfolio.summary()["bankroll"]
    exposure = portfolio.exposure_by_key(gs).get("AAA-GAS", 0.0)
    hist = aaa_history()
    tr = g.trends(hist)
    latest = tr.get("latest")
    stale_min = settings["staleness_minutes"]["kalshi_scan"]
    now = now_ts()
    tick = float(cfg.get("aaa_tick", 0.0001))
    score = model_score(settings)

    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for m in scanner.open_markets(gs):
        by_event[m["event_ticker"]].append(m)

    ladders, opps = [], []
    for ev, mkts in by_event.items():
        rd = _resolution_date(ev, mkts)
        ev_row = scanner.event_row(ev)
        lad = g.ladder_points(mkts)
        med, how = g.implied_median(lad.get("strikes", []), lad.get("survival", []))
        dist = g.model_distribution(hist, rd, cfg) if rd else {"ok": False, "why": "no resolution date"}
        w = float(cfg["model_weight"])
        market_p = g.market_yes_probs(mkts)
        rows = []
        fair_steps: Dict[str, List[str]] = {}
        for m in sorted(mkts, key=lambda x: (x.get("floor_strike") or x.get("cap_strike") or 0)):
            mid, basis = g.mid_of(m)
            row = {"ticker": m["ticker"], "strike_type": m["strike_type"], "floor": m["floor_strike"],
                   "cap": m["cap_strike"], "label": m.get("yes_sub_title") or m.get("subtitle") or m.get("title"),
                   "yes_bid": m["yes_bid"], "yes_ask": m["yes_ask"], "mid": mid, "mid_basis": basis,
                   "volume": m["volume"], "oi": m["open_interest"], "model_yes": None, "market_yes": None,
                   "fair_yes": None}
            if dist.get("ok"):
                py = g.model_prob(m, dist, tick)
                kp = market_p.get(m["ticker"])
                row["model_yes"] = round(py, 4) if py is not None else None
                row["market_yes"] = round(kp, 4) if kp is not None else None
                if dist.get("known") is not None:
                    fy = py
                    fair_steps[m["ticker"]] = [f"AAA already printed {dist['known']:.4f} for the resolution date -> "
                                               f"P(YES) = {py:.0f}"]
                else:
                    fy = g.blend_fair(py, kp, w)
                    fair_steps[m["ticker"]] = dist["steps"] + [
                        f"Model P(YES) = {py:.4f} (clamped to 1-99%)",
                        (f"Kalshi smoothed P(YES) = {kp:.4f}" if kp is not None else "No Kalshi quote to blend; model only"),
                        (f"Fair YES = {w:g} x model + {1 - w:g} x Kalshi = {fy:.4f}" if kp is not None
                         else f"Fair YES = model {fy:.4f}"),
                    ]
                row["fair_yes"] = round(fy, 4) if fy is not None else None
            rows.append(row)
            if row["fair_yes"] is None:
                continue
            age = (now - (m["updated_ts"] or 0)) / 60
            for side, q in (("yes", row["fair_yes"]), ("no", 1 - row["fair_yes"])):
                warnings, force, cap, cap_why = [], None, None, ""
                if age > stale_min:
                    force = f"Pass: Kalshi price is stale ({age:.0f} min old)."
                if latest is not None:
                    trap = g.deceleration_trap(m, side, latest, tr)
                    if trap:
                        force = "Pass: " + trap
                        warnings.append(trap)
                if dist.get("known") is not None:
                    cap, cap_why = "W", "AAA already printed the resolution-date value"
                    warnings.append("AAA has already published the resolution-date value; verify the rules' exact print "
                                    "time before acting.")
                elif not score.get("adds_value"):
                    cap, cap_why = "W", "the gas model has not yet beaten Kalshi's own prices on settled days"
                    warnings.append("Model check: " + score.get("verdict", ""))
                subj = _side_text(m, side)
                fair_info = {"steps": fair_steps.get(m["ticker"], []),
                             "plain": f"Our AAA model (official AAA history) blended {w:g}/{1 - w:g} with Kalshi's own ladder",
                             "source": "aaa_model", "as_of": tr.get("latest_date")}
                opp = evaluate(m, side, q, fair_info, settings, bank, scanner.series_row(m["series_ticker"]),
                               exposure, NONE, "", "gas", warnings, force_pass=force, max_tier=cap, plain_subject=subj,
                               event_row=ev_row, cap_reason=cap_why)
                opp["board"] = "Commodities"
                opp["resolution_date"] = rd.isoformat() if rd else None
                opps.append(opp)
        ladders.append({
            "event_ticker": ev, "series_ticker": mkts[0]["series_ticker"],
            "title": mkts[0].get("event_title") or mkts[0].get("title"),
            "resolution_date": rd.isoformat() if rd else None, "close_hst": hst(min(m["close_ts"] for m in mkts)),
            "implied_median": round(med, 4) if med is not None else None, "median_note": how,
            "model": {k: dist.get(k) for k in ("ok", "why", "mu", "sigma", "horizon", "drift", "sd_daily", "known")},
            "model_weight": w, "rows": rows, "ladder_kind": lad["kind"],
            "survival": lad.get("survival"), "strikes": lad.get("strikes"),
            "price_as_of": hst(max(m["updated_ts"] or 0 for m in mkts)),
        })
    ladders.sort(key=lambda x: x["resolution_date"] or "9999")
    best: Dict[str, Dict[str, Any]] = {}
    for o in sorted(opps, key=sort_key):
        best.setdefault(o["ticker"], o)
    return {
        "aaa": {"latest": latest, "latest_date": tr.get("latest_date"), "trends": tr,
                "history": [{"date": d.isoformat(), "value": v} for d, v in hist[-60:]],
                "rows": aaa_rows(20)},
        "ladders": ladders, "calibration": calibration(), "model_score": score,
        "opportunities": sorted(best.values(), key=sort_key), "series": sorted(gs),
        "warning": ("DECELERATION TRAP: a slowing rise is not a fall. Kalshi's implied gas medians have been well "
                    "calibrated, so this app will not recommend a bet that only wins if a slowdown turns into a drop."),
    }


def _side_text(m: Dict[str, Any], side: str) -> str:
    st = (m.get("strike_type") or "").lower()
    fl, cp = m.get("floor_strike"), m.get("cap_strike")

    def f(x):
        return f"${x:.3f}" if x is not None else "open"

    if st in ("greater", "greater_or_equal"):
        return f"AAA {'above' if side == 'yes' else 'at or below'} {f(fl)}"
    if st in ("less", "less_or_equal"):
        return f"AAA {'below' if side == 'yes' else 'at or above'} {f(cp)}"
    if st == "between":
        return f"AAA {'inside' if side == 'yes' else 'outside'} {f(fl)} to {f(cp)}"
    return m.get("yes_sub_title") or m["ticker"]
