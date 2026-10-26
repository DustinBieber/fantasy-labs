"""NFL spreads, totals, and player props: book lines in, rated Kalshi opportunities out.

Data:
  - bulk pull (with the moneyline job): spreads + totals for every game;
  - per-game deep pull (button or API): alternate spreads/totals + player props, only for stats Kalshi lists;
  - manual entry: any two-sided line you can see, if you have no Odds API key.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, insert, select

from .. import db
from ..engine import lines as ln
from ..engine.edge import evaluate, sort_key
from ..engine.gas import parse_ticker_date
from ..engine.teams import team_name
from ..engine.tiers import NONE
from ..settings_store import get_settings
from ..sources import odds_api
from ..timeutil import hst, now_ts
from . import portfolio, scanner

SPORT = "NFL"
_EVENTS_KEY = "cache:odds_api_events"


def _cache_put(key: str, value: Any) -> None:
    db.upsert(db.app_settings, ["key"], {"key": key, "value": db.dumps(value), "updated_ts": now_ts()})


def _cache_get(key: str) -> Any:
    r = db.fetch_one(select(db.app_settings.c.value).where(db.app_settings.c.key == key))
    return json.loads(r["value"]) if r else None


def _game_id(a: str, b: str, d) -> str:
    return f"{'-'.join(sorted((a, b)))}|{d.isoformat() if d else ''}"


def store_outcomes(rows: List[Dict[str, Any]], source: str, pull_id: Optional[int]) -> int:
    ts = now_ts()
    out = [dict(r, sport=SPORT, source=source, ts=ts, pull_id=pull_id) for r in rows]
    if out:
        with db.get_engine().begin() as c:
            c.execute(insert(db.book_outcomes), out)
    return len(out)


def remember_events(events: List[Dict[str, Any]]) -> None:
    """Odds API event ids by game, for the per-game deep pull."""
    from ..engine.teams import team_code
    from ..timeutil import parse_iso
    known = _cache_get(_EVENTS_KEY) or {}
    for ev in events:
        h, a = team_code(ev.get("home_team")), team_code(ev.get("away_team"))
        ts = parse_iso(ev.get("commence_time"))
        if h and a and ev.get("id") and ts:
            d = datetime.fromtimestamp(ts, tz=ZoneInfo("America/New_York")).date()
            known[_game_id(h, a, d)] = {"id": ev["id"], "commence_ts": ts, "home": h, "away": a}
    _cache_put(_EVENTS_KEY, known)


def _event_for(teams: Tuple[str, str], d) -> Optional[Dict[str, Any]]:
    known = _cache_get(_EVENTS_KEY) or {}
    for delta in (0, -1, 1):
        e = known.get(_game_id(teams[0], teams[1], d + timedelta(days=delta)))
        if e:
            return e
    return None


def kalshi_line_markets() -> Dict[str, List[Dict[str, Any]]]:
    """Open NFL spread / total / prop markets from the scanner, grouped by type."""
    s = get_settings()["nfl"]
    prefix = s.get("prop_series_prefix", "KXNFL")
    out: Dict[str, List[Dict[str, Any]]] = {"spread": [], "total": [], "prop": []}
    rows = db.fetch_all(select(db.markets).where(and_(
        db.markets.c.series_ticker.like(f"{prefix}%"), db.markets.c.status.in_(("active", "open", "initialized")),
        db.markets.c.close_ts > now_ts())))
    for m in rows:
        st = m["series_ticker"]
        if st in s["series"]:
            continue
        if st in s["spread_series"]:
            if ln.parse_spread(m):
                out["spread"].append(m)
        elif st in s["total_series"]:
            if ln.parse_total(m) is not None:
                out["total"].append(m)
        elif ln.parse_prop(m):
            out["prop"].append(m)
    return out


def deep_pull(event_ticker: str) -> Dict[str, Any]:
    """Alternate spreads/totals + the player-prop markets Kalshi lists for this game. Costs credits."""
    if not odds_api.enabled():
        raise ValueError("Add ODDS_API_KEY to .env first (alternate lines and props come from The Odds API)")
    teams, d = ln.split_matchup(event_ticker), parse_ticker_date(event_ticker)
    if not teams or not d:
        raise ValueError(f"Can't read the game from {event_ticker}")
    ev = _event_for(teams, d)
    if not ev:
        raise ValueError("No Odds API event id for this game yet: run the Odds API pull first")
    stats = set()
    for m in kalshi_line_markets()["prop"]:
        if ln.split_matchup(m["event_ticker"]) and set(ln.split_matchup(m["event_ticker"])) == set(teams):
            stats.add(ln.parse_prop(m)[1])
    markets = list(odds_api.DEEP_GAME_MARKETS)
    for stat in sorted(stats):
        main, alt = odds_api.PROP_MARKETS[stat]
        markets += [k for k in (main, alt) if k]
    cfg = get_settings()["nfl"]
    body, pull_id, quota = odds_api.fetch_event_odds(ev["id"], markets, SPORT, cfg.get("deep_pull_regions", "us"))
    n = store_outcomes(odds_api.parse_outcomes([body]), "odds_api", pull_id)
    _cache_put("cache:odds_api_quota", dict(quota, ts=now_ts()))
    return {"game": f"{teams[0]} vs {teams[1]}", "markets": markets, "outcomes": n,
            "credits_used": quota.get("x-requests-last"), "credits_left": quota.get("x-requests-remaining")}


def add_manual(p: Dict[str, Any]) -> Dict[str, Any]:
    """A two-sided line you can see: spread (team, X, both prices), total (X, over/under), or prop
    (player, stat, N, over/under at N-0.5)."""
    from ..engine.devig import american_to_implied
    from ..engine.teams import team_code
    kind = p.get("kind")
    teams = ln.split_matchup(p.get("event_ticker", ""))
    if not teams:
        raise ValueError("event_ticker must be a Kalshi NFL game, e.g. KXNFLSPREAD-26OCT26DALDEN")
    a_price, b_price = int(p["price_a"]), int(p["price_b"])
    american_to_implied(a_price)
    american_to_implied(b_price)
    book = f"manual:{(p.get('book') or 'screen').strip()[:40]}"
    base = {"home_team": teams[1], "away_team": teams[0], "commence_ts": None, "event_id": None, "book": book}
    x = float(p["point"])
    if kind == "spread":
        team = team_code(p.get("team"))
        if team not in teams:
            raise ValueError("team must be one of the two teams in this game")
        opp = teams[0] if team == teams[1] else teams[1]
        rows = [dict(base, market_key="alternate_spreads", outcome_name=team_name(team), description=None,
                     point=-x, price=a_price),
                dict(base, market_key="alternate_spreads", outcome_name=team_name(opp), description=None,
                     point=x, price=b_price)]
    elif kind == "total":
        rows = [dict(base, market_key="alternate_totals", outcome_name="Over", description=None, point=x, price=a_price),
                dict(base, market_key="alternate_totals", outcome_name="Under", description=None, point=x, price=b_price)]
    elif kind == "prop":
        stat = p.get("stat")
        if stat not in odds_api.PROP_MARKETS or not p.get("player"):
            raise ValueError(f"stat must be one of {sorted(odds_api.PROP_MARKETS)} and player is required")
        main = odds_api.PROP_MARKETS[stat][0]
        if stat == "tds":
            rows = [dict(base, market_key=main, outcome_name="Yes", description=p["player"], point=None, price=a_price),
                    dict(base, market_key=main, outcome_name="No", description=p["player"], point=None, price=b_price)]
        else:
            rows = [dict(base, market_key=main, outcome_name="Over", description=p["player"], point=x, price=a_price),
                    dict(base, market_key=main, outcome_name="Under", description=p["player"], point=x, price=b_price)]
    else:
        raise ValueError("kind must be spread, total, or prop")
    return {"stored": store_outcomes(rows, "manual", None)}


def _rows_for_game(teams: Tuple[str, str], settings: Dict[str, Any], cache: Dict) -> List[Dict[str, Any]]:
    key = frozenset(teams)
    if key in cache:
        return cache[key]
    stale = settings["staleness_minutes"]
    now = now_ts()
    a, b = teams
    rows = db.fetch_all(select(db.book_outcomes).where(and_(
        db.book_outcomes.c.sport == SPORT,
        ((db.book_outcomes.c.home_team == a) & (db.book_outcomes.c.away_team == b))
        | ((db.book_outcomes.c.home_team == b) & (db.book_outcomes.c.away_team == a)),
        db.book_outcomes.c.ts >= now - 60 * int(max(stale["odds_api"], stale["manual_lines"])))))
    rows = [r for r in rows if r["ts"] >= now - 60 * int(stale["manual_lines" if r["source"] == "manual" else "odds_api"])]
    cache[key] = rows
    return rows


def board() -> Dict[str, Any]:
    settings = get_settings()
    cfg = settings["nfl"]
    sharp = cfg.get("sharp_books", [])
    bank = portfolio.summary()["bankroll"]
    exposure = portfolio.exposure_by_key()
    stale_min = settings["staleness_minutes"]["kalshi_scan"]
    now = now_ts()
    espn = _cache_get("cache:espn_nfl") or {}
    kickoffs = {frozenset((g["home_team"], g["away_team"])): g.get("commence_ts") for g in espn.get("games", [])}
    for e in (_cache_get(_EVENTS_KEY) or {}).values():
        kickoffs.setdefault(frozenset((e["home"], e["away"])), e.get("commence_ts"))

    markets = kalshi_line_markets()
    cache: Dict = {}
    opps: List[Dict[str, Any]] = []
    games: Dict[str, Dict[str, Any]] = {}
    for kind, mkts in markets.items():
        for m in mkts:
            teams = ln.split_matchup(m["event_ticker"])
            d = parse_ticker_date(m["event_ticker"])
            if not teams or not d:
                continue
            gid = _game_id(teams[0], teams[1], d)
            g = games.setdefault(gid, {"game": gid, "title": f"{team_name(teams[0])} at {team_name(teams[1])}",
                                       "date": d.isoformat(), "teams": list(teams), "spread": 0, "total": 0, "prop": 0,
                                       "rated": 0, "has_event_id": bool(_event_for(teams, d)),
                                       "sample_event_ticker": m["event_ticker"]})
            g[kind] += 1
            rows = _rows_for_game(teams, settings, cache)
            if kind == "spread":
                team, x = ln.parse_spread(m)
                fair = ln.spread_fair(rows, team, x, sharp, float(cfg["spread_sd"]))
                subject, leg_label = f"{team_name(team)} wins by {x:g}+", f"{team_name(team)} by over {x:g}"
            elif kind == "total":
                x = ln.parse_total(m)
                fair = ln.total_fair(rows, x, sharp, float(cfg["total_sd"]))
                subject = leg_label = f"Over {x:g} points ({team_name(teams[0])} at {team_name(teams[1])})"
            else:
                player, stat, n = ln.parse_prop(m)
                fair = ln.prop_fair(rows, player, stat, n, odds_api.PROP_MARKETS[stat], sharp)
                subject = leg_label = f"{player} {n}+ {ln.STAT_LABEL[stat]}"
            if not fair:
                continue
            g["rated"] += 1
            kick = kickoffs.get(frozenset(teams))
            warnings, force, cap, cap_why = [], None, None, ""
            if fair["kind"] == "model":
                cap, cap_why = "W", "priced by a normal model, not a sportsbook line at this number"
                warnings.append("Fair price comes from a normal model fitted to the main line, not a book's line at "
                                "this exact number. The model ignores NFL key numbers (3, 7, 10), so it can't be "
                                "rated above Watch. Load alt lines or enter the book's line at this number.")
            elif fair["kind"] == "scaled":
                warnings.append("Fair price comes from a one-sided alternate line scaled by the book's own main-line "
                                "vig (no two-sided price at this exact number).")
            age = (now - (m["updated_ts"] or 0)) / 60
            if age > stale_min:
                force = f"Pass: Kalshi price is stale ({age:.0f} min old)."
            if kick and kick <= now:
                force = "Pass: game has started; the pregame fair price no longer applies."
            fair_info = {"steps": fair["steps"], "source": f"odds_{fair['kind']}", "as_of": hst(max(r["ts"] for r in rows)),
                         "plain": {"exact": f"Sportsbook line at this exact number ({fair['book_names']}, vig removed)",
                                   "scaled": f"One-sided alt line, vig-scaled ({fair['book_names']})",
                                   "model": f"Normal model on the main line ({fair['book_names']})"}[fair["kind"]],
                         "pull_ids": sorted({r["pull_id"] for r in rows if r.get("pull_id")})}
            for side, q in (("yes", fair["p"]), ("no", 1 - fair["p"])):
                opp = evaluate(m, side, q, fair_info, settings, bank, scanner.series_row(m["series_ticker"]),
                               exposure.get(portfolio.corr_key(m["series_ticker"], m["event_ticker"]), 0.0), NONE, "",
                               f"nfl_{kind}", warnings, force_pass=force, max_tier=cap,
                               plain_subject=subject if side == "yes" else f"NOT: {subject}",
                               event_row=scanner.event_row(m["event_ticker"]), cap_reason=cap_why)
                opp.update(board="Props" if kind == "prop" else "Spreads & totals", sport=SPORT, leg_type=kind,
                           leg_label=leg_label, fair_kind=fair["kind"], game=gid, game_title=g["title"],
                           kickoff_ts=kick)
                opps.append(opp)
    opps.sort(key=sort_key)
    best: Dict[str, Dict[str, Any]] = {}
    for o in opps:
        best.setdefault(o["ticker"], o)
    quota = _cache_get("cache:odds_api_quota")
    return {"opportunities": opps, "best": sorted(best.values(), key=sort_key),
            "games": sorted(games.values(), key=lambda g: (g["date"], g["title"])),
            "counts": {k: len(v) for k, v in markets.items()}, "odds_api_enabled": odds_api.enabled(),
            "odds_api_quota": quota}
