"""Fair prices for NFL spreads, totals, and player props at Kalshi's exact strikes.

Kalshi lists ladders:
  spread  "Chicago wins by over 3.5 points"   -> P(Chicago margin > 3.5)
  total   "Over 44.5 points scored"           -> P(total points > 44.5)
  prop    "Colston Loveland: 25+ receiving yards" -> P(stat >= 25)

Fair-price order for each strike, per sportsbook, then the median across books (sharp books first):
  1. exact: a two-sided line at that exact point (main or alternate market), vig removed proportionally;
  2. scaled (props only): a one-sided alternate milestone at that point, scaled by the same book's
     own vig on its two-sided main line for that player and stat;
  3. model (spreads/totals only, labeled, off for parlays by default): a normal distribution fitted to the
     book's main line (NFL margin sd 13.5, total sd 10 by default).
Anything else is left unrated. Nothing is guessed.
"""
from __future__ import annotations

import math
import re
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .devig import american_to_implied, devig_two_way
from .gas import normal_cdf, parse_ticker_date
from .teams import _ALIAS, team_code

# ---------------------------------------------------------------- Kalshi market parsing

_STATS = [
    (re.compile(r"receiving yards|receiving yds|rec(?:eiving)? yds", re.I), "rec_yds"),
    (re.compile(r"rushing yards|rushing yds|rush yds", re.I), "rush_yds"),
    (re.compile(r"passing yards|passing yds|pass yds", re.I), "pass_yds"),
    (re.compile(r"passing touchdowns|passing tds|pass tds", re.I), "pass_tds"),
    (re.compile(r"receptions|catches", re.I), "receptions"),
    (re.compile(r"touchdowns?|tds?\b", re.I), "tds"),
]
STAT_LABEL = {"rec_yds": "receiving yards", "rush_yds": "rushing yards", "pass_yds": "passing yards",
              "receptions": "receptions", "pass_tds": "passing TDs", "tds": "touchdowns"}
_PROP_TITLE = re.compile(r"^\s*(?P<player>[^:]+?)\s*:\s*(?P<n>\d+)\s*\+\s*(?P<stat>[A-Za-z .+]+?)\s*\??\s*$")


def split_matchup(event_ticker: str) -> Optional[Tuple[str, str]]:
    """KXNFLSPREAD-26OCT22NECHI -> ('NE', 'CHI') using the known team codes (away first, then home)."""
    parts = (event_ticker or "").split("-")
    m = re.match(r"^\d{2}[A-Z]{3}\d{2}([A-Z]+)$", parts[1]) if len(parts) > 1 else None
    blob = m.group(1) if m else ""
    for i in range(2, len(blob) - 1):
        a, b = _ALIAS.get(blob[:i]), _ALIAS.get(blob[i:])
        if a and b and a != b:
            return a, b
    return None


def game_key(event_ticker: str):
    teams = split_matchup(event_ticker)
    d = parse_ticker_date(event_ticker)
    return (frozenset(teams), d) if teams and d else None


def parse_spread(m: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """(team code, X) for 'TEAM wins by over X'."""
    x = m.get("floor_strike")
    title = f"{m.get('title') or ''} {m.get('yes_sub_title') or ''}"
    if x is None:
        mt = re.search(r"by over (\d+(?:\.\d+)?)", title, re.I)
        x = float(mt.group(1)) if mt else None
    suffix = (m.get("ticker") or "").rsplit("-", 1)[-1]
    team = _ALIAS.get(re.sub(r"\d+$", "", suffix))
    if not team:
        mt = re.match(r"^\s*(.+?)\s+wins by", m.get("title") or m.get("yes_sub_title") or "", re.I)
        team = team_code(mt.group(1)) if mt else None
    if team is None or x is None:
        return None
    if (m.get("strike_type") or "greater") == "greater_or_equal":
        x = float(x) - 0.5  # integer margins: >= N is > N - 0.5
    return team, float(x)


def parse_total(m: Dict[str, Any]) -> Optional[float]:
    x = m.get("floor_strike")
    if x is None:
        mt = re.search(r"over (\d+(?:\.\d+)?)", f"{m.get('title') or ''} {m.get('yes_sub_title') or ''}", re.I)
        x = float(mt.group(1)) if mt else None
    if x is None:
        return None
    if (m.get("strike_type") or "greater") == "greater_or_equal":
        x = float(x) - 0.5
    return float(x)


def parse_prop(m: Dict[str, Any]) -> Optional[Tuple[str, str, int]]:
    """(player, stat, N) for 'Player: N+ stat' (YES wins at stat >= N)."""
    for text in (m.get("title"), m.get("yes_sub_title"), m.get("subtitle")):
        mt = _PROP_TITLE.match(text or "")
        if not mt:
            continue
        stat = next((key for rx, key in _STATS if rx.search(mt.group("stat"))), None)
        if stat:
            return mt.group("player").strip(), stat, int(mt.group("n"))
    return None


def norm_name(name: Optional[str]) -> str:
    s = re.sub(r"[^a-z ]", "", (name or "").lower().replace("-", " "))
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- pricing from book outcomes

def _latest_by_book(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Keep only each book's most recent pull."""
    newest: Dict[str, int] = {}
    for r in rows:
        newest[r["book"]] = max(newest.get(r["book"], 0), r["ts"])
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if r["ts"] == newest[r["book"]]:
            out.setdefault(r["book"], []).append(r)
    return out


def _combine(per_book: List[Tuple[str, float, str, str]], sharp: Sequence[str]) -> Optional[Dict[str, Any]]:
    """per_book: (book, p, kind, step). Prefer exact over scaled over model; sharp books first."""
    for kind in ("exact", "scaled", "model"):
        rows = [r for r in per_book if r[2] == kind]
        if not rows:
            continue
        sharp_rows = [r for r in rows if r[0].lower() in {b.lower() for b in sharp}]
        use = sharp_rows or rows
        p = float(median(r[1] for r in use))
        names = ", ".join(sorted({r[0] for r in use}))
        steps = [r[3] for r in rows] + [f"Fair = median of {len(use)} {kind} price(s){' from sharp books' if sharp_rows else ''}"
                                         f" ({names}) = {p:.4f}"]
        return {"p": p, "kind": kind, "books": len(use), "book_names": names, "steps": steps,
                "confidence": "normal" if kind == "exact" and (sharp_rows or len(use) >= 2) else "low"}
    return None


def _pairs(outs: List[Dict[str, Any]], keys: Sequence[str]) -> Dict[float, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Over/Under pairs by point within the given market keys."""
    by_point: Dict[float, Dict[str, Dict[str, Any]]] = {}
    for o in outs:
        if o["market_key"] in keys and o["point"] is not None and o["outcome_name"].lower() in ("over", "under"):
            by_point.setdefault(o["point"], {})[o["outcome_name"].lower()] = o
    return {pt: (d["over"], d["under"]) for pt, d in by_point.items() if "over" in d and "under" in d}


def spread_fair(rows: Sequence[Dict[str, Any]], team: str, x: float, sharp: Sequence[str] = (),
                sd: float = 13.5, allow_model: bool = True) -> Optional[Dict[str, Any]]:
    """P(team's margin > x). A book line 'TEAM -q' covers when margin > q."""
    per_book = []
    for book, outs in _latest_by_book(rows).items():
        sides: Dict[Tuple[str, float], Dict[str, Any]] = {}
        for o in outs:
            if o["market_key"] in ("spreads", "alternate_spreads") and o["point"] is not None:
                code = team_code(o["outcome_name"])
                if code:
                    sides[(code, o["point"])] = o
        thresholds: Dict[float, float] = {}
        main_thr = None
        for (code, pt), o in sides.items():
            if code != team:
                continue
            opp = next((v for (c2, p2), v in sides.items() if c2 != team and abs(p2 + pt) < 1e-9), None)
            if not opp:
                continue
            d = devig_two_way(o["price"], opp["price"])
            thresholds[-pt] = d["home"]
            if o["market_key"] == "spreads":
                main_thr = -pt
        if x in thresholds:
            per_book.append((book, thresholds[x], "exact", f"[{book}] {team} {-x:+g} vs opponent {x:+g}: "
                                                         f"P(margin > {x:g}) = {thresholds[x]:.4f} (vig removed)"))
        elif allow_model and main_thr is not None:
            p0 = min(max(thresholds[main_thr], 1e-4), 1 - 1e-4)
            mu = main_thr + sd * _inv_norm(p0)
            p = 1 - normal_cdf((x - mu) / sd)
            per_book.append((book, p, "model", f"[{book}] model: main line P(margin > {main_thr:g}) = {p0:.4f} -> "
                                               f"mean margin {mu:.2f}, sd {sd:g} -> P(margin > {x:g}) = {p:.4f}"))
    return _combine(per_book, sharp)


def total_fair(rows: Sequence[Dict[str, Any]], x: float, sharp: Sequence[str] = (), sd: float = 10.0,
               allow_model: bool = True) -> Optional[Dict[str, Any]]:
    """P(total points > x)."""
    per_book = []
    for book, outs in _latest_by_book(rows).items():
        pairs = _pairs(outs, ("totals", "alternate_totals"))
        if x in pairs:
            ov, un = pairs[x]
            d = devig_two_way(ov["price"], un["price"])
            per_book.append((book, d["home"], "exact", f"[{book}] Over/Under {x:g}: {ov['price']:+d}/{un['price']:+d} "
                                                     f"-> P(over) = {d['home']:.4f} (vig removed)"))
        elif allow_model:
            main = [pt for pt, (ov, _) in pairs.items() if ov["market_key"] == "totals"]
            if main:
                pt = main[0]
                ov, un = pairs[pt]
                p0 = min(max(devig_two_way(ov["price"], un["price"])["home"], 1e-4), 1 - 1e-4)
                mu = pt + sd * _inv_norm(p0)
                p = 1 - normal_cdf((x - mu) / sd)
                per_book.append((book, p, "model", f"[{book}] model: P(over {pt:g}) = {p0:.4f} -> mean total {mu:.1f}, "
                                                   f"sd {sd:g} -> P(over {x:g}) = {p:.4f}"))
    return _combine(per_book, sharp)


def prop_fair(rows: Sequence[Dict[str, Any]], player: str, stat: str, n: int, markets: Tuple[str, Optional[str]],
              sharp: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
    """P(stat >= n) for a player. Book 'Over n-0.5' is the same bet."""
    main_key, alt_key = markets
    want = norm_name(player)
    point = n - 0.5
    per_book = []
    for book, outs in _latest_by_book(rows).items():
        mine = [o for o in outs if norm_name(o.get("description")) == want]
        if not mine:
            continue
        if stat == "tds":
            yes = next((o for o in mine if o["market_key"] == main_key and o["outcome_name"].lower() == "yes"), None)
            no = next((o for o in mine if o["market_key"] == main_key and o["outcome_name"].lower() == "no"), None)
            if n == 1 and yes and no:
                d = devig_two_way(yes["price"], no["price"])
                per_book.append((book, d["home"], "exact", f"[{book}] {player} anytime TD Yes/No {yes['price']:+d}/"
                                                         f"{no['price']:+d} -> {d['home']:.4f} (vig removed)"))
            continue
        keys = tuple(k for k in (main_key, alt_key) if k)
        pairs = _pairs(mine, keys)
        if point in pairs:
            ov, un = pairs[point]
            d = devig_two_way(ov["price"], un["price"])
            per_book.append((book, d["home"], "exact", f"[{book}] {player} Over/Under {point:g} {STAT_LABEL[stat]}: "
                                                     f"{ov['price']:+d}/{un['price']:+d} -> P({n}+) = {d['home']:.4f}"))
            continue
        alt_over = next((o for o in mine if o["market_key"] == alt_key and o["point"] == point
                         and o["outcome_name"].lower() in ("over", "yes")), None)
        main_pairs = _pairs(mine, (main_key,))
        if alt_over and main_pairs:
            mpt, (mov, mun) = next(iter(main_pairs.items()))
            fair_main = devig_two_way(mov["price"], mun["price"])["home"]
            factor = fair_main / american_to_implied(mov["price"])
            p = min(american_to_implied(alt_over["price"]) * factor, 0.99)
            per_book.append((book, p, "scaled", f"[{book}] one-sided {n}+ at {alt_over['price']:+d} "
                                                f"(implied {american_to_implied(alt_over['price']):.4f}) x this book's "
                                                f"main-line vig factor {factor:.4f} (Over {mpt:g}) = {p:.4f}"))
    return _combine(per_book, sharp)


def _inv_norm(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, |error| < 1.2e-9)."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - lo:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
