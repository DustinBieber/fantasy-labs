"""NFL game-winner edges: Kalshi KXNFLGAME markets vs devigged sportsbook consensus."""
from __future__ import annotations

import json
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, insert, select

from .. import db
from ..engine import devig
from ..engine.edge import evaluate, sort_key
from ..engine.experts import expert_status
from ..engine.gas import parse_ticker_date
from ..engine.teams import kalshi_market_team, team_code, team_name
from ..settings_store import get_settings
from ..sources import espn, odds_api
from ..timeutil import hst, now_ts
from . import portfolio, scanner

SPORT = "NFL"
_CACHE_KEY = "cache:espn_nfl"


def _put_cache(key: str, value: Any) -> None:
    db.upsert(db.app_settings, ["key"], {"key": key, "value": db.dumps(value), "updated_ts": now_ts()})


def _get_cache(key: str) -> Any:
    r = db.fetch_one(select(db.app_settings.c.value).where(db.app_settings.c.key == key))
    return json.loads(r["value"]) if r else None


def pull_espn() -> Dict[str, Any]:
    body, pull_id = espn.fetch_scoreboard()
    games = espn.parse_games(body)
    ts = now_ts()
    rows = [{"sport": SPORT, "source": "espn", "book": g["book"] or "ESPN", "home_team": g["home_team"],
             "away_team": g["away_team"], "commence_ts": g["commence_ts"], "home_price": g["home_price"],
             "away_price": g["away_price"], "ts": ts, "pull_id": pull_id, "note": g.get("details")}
            for g in games if g["home_price"] is not None and g["away_price"] is not None and not g["completed"]]
    if rows:
        with db.get_engine().begin() as c:
            c.execute(insert(db.odds_lines), rows)
    _put_cache(_CACHE_KEY, {"ts": ts, "pull_id": pull_id, "games": games})
    with db.get_engine().begin() as c:
        c.execute(db.data_pulls.update().where(db.data_pulls.c.id == pull_id).values(rows=len(games)))
    return {"games": len(games), "with_moneyline": len(rows)}


def pull_odds_api() -> Dict[str, Any]:
    if not odds_api.enabled():
        return {"skipped": "no ODDS_API_KEY"}
    s = get_settings()["nfl"]
    events, pull_id, quota = odds_api.fetch_odds(SPORT, s.get("odds_api_regions", "us"), s.get("odds_api_bookmakers", ""),
                                                 s.get("odds_api_markets", "h2h"))
    from . import lines_service
    lines_service.remember_events(events)
    line_rows = lines_service.store_outcomes(odds_api.parse_outcomes(events), "odds_api", pull_id)
    parsed = odds_api.parse_h2h(events)
    ts = now_ts()
    rows = [dict(r, sport=SPORT, source="odds_api", ts=ts, pull_id=pull_id, note=None) for r in parsed]
    if rows:
        with db.get_engine().begin() as c:
            c.execute(insert(db.odds_lines), rows)
    _put_cache("cache:odds_api_quota", dict(quota, ts=ts))
    with db.get_engine().begin() as c:
        c.execute(db.data_pulls.update().where(db.data_pulls.c.id == pull_id).values(rows=len(rows)))
    return {"events": len(events), "book_lines": len(rows), "spread_total_outcomes": line_rows, "quota": quota}


def add_manual_line(p: Dict[str, Any]) -> Dict[str, Any]:
    home, away = team_code(p.get("home_team")), team_code(p.get("away_team"))
    if not home or not away or home == away:
        raise ValueError("Pick two different NFL teams")
    hp, ap = int(p["home_price"]), int(p["away_price"])
    devig.american_to_implied(hp)
    devig.american_to_implied(ap)
    row = {"sport": SPORT, "source": "manual", "book": (p.get("book") or "manual").strip()[:60], "home_team": home,
           "away_team": away, "commence_ts": p.get("commence_ts"), "home_price": hp, "away_price": ap,
           "ts": now_ts(), "pull_id": None, "note": p.get("note")}
    with db.get_engine().begin() as c:
        rid = c.execute(insert(db.odds_lines).values(**row)).inserted_primary_key[0]
    return dict(row, id=int(rid))


def _lines_for(teams: Tuple[str, str], source: str, since_ts: int, game_date=None) -> List[Dict[str, Any]]:
    a, b = teams
    q = select(db.odds_lines).where(and_(
        db.odds_lines.c.sport == SPORT, db.odds_lines.c.source == source, db.odds_lines.c.ts >= since_ts,
        ((db.odds_lines.c.home_team == a) & (db.odds_lines.c.away_team == b))
        | ((db.odds_lines.c.home_team == b) & (db.odds_lines.c.away_team == a))))
    rows = db.fetch_all(q.order_by(db.odds_lines.c.ts.desc()))
    if game_date is not None:
        def near(r):
            if not r.get("commence_ts"):
                return True
            from datetime import datetime, timezone
            d = datetime.fromtimestamp(r["commence_ts"], tz=timezone.utc).date()
            return abs((d - game_date).days) <= 2
        rows = [r for r in rows if near(r)]
    return rows


def fair_for_game(teams: Tuple[str, str], settings: Dict[str, Any], game_date=None) -> Optional[Dict[str, Any]]:
    """Fair win probability for teams[0] from the first fresh source in priority order."""
    stale = settings["staleness_minutes"]
    now = now_ts()
    windows = {"odds_api": stale["odds_api"], "manual": stale["manual_lines"], "espn": stale["espn_nfl"]}
    for source in settings["nfl"]["fair_source_priority"]:
        if source not in windows:
            continue
        rows = _lines_for(teams, source, now - int(windows[source]) * 60, game_date)
        if not rows:
            continue
        latest_by_book: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            latest_by_book.setdefault(r["book"], r)
        per_book, steps = [], []
        sharp_keys = {b.lower() for b in settings["nfl"].get("sharp_books", [])}
        sharp = []
        for book, r in sorted(latest_by_book.items()):
            d = devig.devig_two_way(r["home_price"], r["away_price"])
            p_first = d["home"] if r["home_team"] == teams[0] else d["away"]
            per_book.append(p_first)
            if book.lower() in sharp_keys:
                sharp.append((book, p_first))
            steps.append(f"[{book} @ {hst(r['ts'], '%I:%M %p')}] {team_name(r['away_team'])} {r['away_price']:+d} at "
                         f"{team_name(r['home_team'])} {r['home_price']:+d}: vig {100 * (d['overround'] - 1):.2f}%, "
                         f"fair {teams[0]} {p_first:.4f}")
        n = len(per_book)
        if source == "odds_api" and sharp:
            fair = float(median(p for _, p in sharp))
            names = ", ".join(b for b, _ in sharp)
            label = f"Sharp book line ({names}, vig removed)"
            steps.append(f"Sharp book(s) present ({names}): fair {teams[0]} = {fair:.4f} "
                         f"(all-book median would be {float(median(per_book)):.4f})")
        else:
            fair = float(median(per_book))
            label = {"odds_api": f"Sportsbook consensus (median of {n} book{'s' if n != 1 else ''}, vig removed)",
                     "manual": f"Your manual line{'s' if n != 1 else ''} ({', '.join(sorted(latest_by_book))}, vig removed)",
                     "espn": f"ESPN's single book ({next(iter(latest_by_book))}, vig removed; low confidence)"}[source]
            steps.append(f"Fair {teams[0]} = median of {n} devigged book price(s) = {fair:.4f}")
        newest = max(r["ts"] for r in latest_by_book.values())
        return {"source": source, "fair_first": fair, "books": n, "steps": steps, "plain": label,
                "as_of_ts": newest, "as_of": hst(newest),
                "confidence": "normal" if (sharp and source == "odds_api") or (source != "espn" and n >= 3) else "low",
                "pull_ids": sorted({r["pull_id"] for r in latest_by_book.values() if r["pull_id"]})}
    return None


def _mid(m: Dict[str, Any]) -> Optional[float]:
    if m.get("yes_bid") is not None and m.get("yes_ask") is not None:
        return round((m["yes_bid"] + m["yes_ask"]) / 2, 4)
    return m.get("last_price")


def _movement(ticker: str) -> Dict[str, Any]:
    now = now_ts()
    snaps = scanner.history(ticker, since_ts=now - 86400, limit=3000)
    pts = [(s["ts"], (s["yes_bid"] + s["yes_ask"]) / 2) for s in snaps
           if s.get("yes_bid") is not None and s.get("yes_ask") is not None]
    out: Dict[str, Any] = {"spark": [round(p, 3) for _, p in pts[-120:]]}
    if pts:
        cur = pts[-1][1]
        for label, secs in (("1h", 3600), ("24h", 86400)):
            older = [p for t, p in pts if t <= now - secs]
            base = older[-1] if older else pts[0][1]
            out[f"chg_{label}"] = round(100 * (cur - base), 1)
    return out


def _game_context(teams: Tuple[str, str]) -> Optional[Dict[str, Any]]:
    cache = _get_cache(_CACHE_KEY) or {}
    for g in cache.get("games", []):
        if {g["home_team"], g["away_team"]} == set(teams):
            return g
    return None


def board(include_all_sides: bool = False) -> Dict[str, Any]:
    settings = get_settings()
    bank = portfolio.summary()["bankroll"]
    gas_series = scanner.gas_series_set(settings)
    exposure = portfolio.exposure_by_key(gas_series)
    experts = db.fetch_all(select(db.experts))
    picks = db.fetch_all(select(db.expert_picks))
    stale_min = settings["staleness_minutes"]["kalshi_scan"]
    now = now_ts()

    by_event: Dict[str, List[Dict[str, Any]]] = {}
    for m in scanner.open_markets(settings["nfl"]["series"]):
        by_event.setdefault(m["event_ticker"], []).append(m)

    games, opps = [], []
    for ev, mkts in sorted(by_event.items(), key=lambda kv: min(m["close_ts"] or 0 for m in kv[1])):
        teamed = [(kalshi_market_team(m["ticker"], m.get("yes_sub_title")), m) for m in mkts]
        teamed = [(t, m) for t, m in teamed if t]
        if len(teamed) != 2:
            continue
        (ta, ma), (tb, mb) = teamed
        game_date = parse_ticker_date(ev)
        fair = fair_for_game((ta, tb), settings, game_date)
        ctx = _game_context((ta, tb))
        started = bool(ctx and ctx.get("commence_ts") and ctx["commence_ts"] <= now)
        pair = {ma["ticker"]: mb["ticker"], mb["ticker"]: ma["ticker"]}
        ev_row = scanner.event_row(ev)
        title = mkts[0].get("event_title") or mkts[0].get("title") or ev
        game = {"event_ticker": ev, "title": title, "date": game_date.isoformat() if game_date else None,
                "context": ctx, "fair": fair, "teams": [], "started": started}
        for team, m, p_team in ((ta, ma, fair["fair_first"] if fair else None),
                                (tb, mb, (1 - fair["fair_first"]) if fair else None)):
            game["teams"].append({"team": team, "name": team_name(team), "ticker": m["ticker"],
                                  "yes_bid": m["yes_bid"], "yes_ask": m["yes_ask"], "last": m["last_price"],
                                  "mid": _mid(m), "volume": m["volume"], "fair": p_team, "movement": _movement(m["ticker"]),
                                  "price_as_of": hst(m["updated_ts"]), "pull_id": m["pull_id"]})
            if p_team is None:
                continue
            warnings = []
            force = None
            age = (now - (m["updated_ts"] or 0)) / 60
            if age > stale_min:
                force = f"Pass: Kalshi price is stale ({age:.0f} min old)."
            if started:
                force = "Pass: game has started; the pregame fair price no longer applies."
            if fair["confidence"] == "low":
                warnings.append("Fair price is from fewer than 3 books" + (" (ESPN single book)" if fair["source"] == "espn" else "")
                                + "; treat as low confidence.")
            fair_info = {"steps": fair["steps"], "plain": fair["plain"], "source": fair["source"],
                         "as_of": fair["as_of"], "pull_ids": fair["pull_ids"]}
            for side, q in (("yes", p_team), ("no", 1 - p_team)):
                st, note = expert_status(m["ticker"], side, SPORT, experts, picks, pair)
                subj = team_name(team) + (" to win" if side == "yes" else " to lose")
                opp = evaluate(m, side, q, fair_info, settings, bank, scanner.series_row(m["series_ticker"]),
                               exposure.get(portfolio.corr_key(m["series_ticker"], ev, gas_series), 0.0), st, note,
                               "nfl", warnings, force_pass=force, plain_subject=subj, event_row=ev_row)
                opp["board"] = "Game winners"
                opp["sport"] = SPORT
                opp["game_title"] = title
                opp["outcome"] = f"{ev}:{team if side == 'yes' else (tb if team == ta else ta)}"
                opps.append(opp)
        games.append(game)
    opps.sort(key=sort_key)
    if not include_all_sides:
        # YES on one team and NO on the other are the same outcome: keep the better price, note the other.
        best: Dict[str, Dict[str, Any]] = {}
        for o in opps:
            if o["outcome"] not in best:
                best[o["outcome"]] = o
            elif "alt_note" not in best[o["outcome"]]:
                b = best[o["outcome"]]
                b["alt_note"] = (f"Same outcome is also {o['side'].upper()} on {o['ticker']} at maker "
                                 f"{100 * (o['maker_price'] or 0):.0f}¢ ({o['tier']}). Take one, not both.")
                b["warnings"] = b["warnings"] + [b["alt_note"]]
        opps = sorted(best.values(), key=sort_key)
    return {"games": games, "opportunities": opps, "odds_api_enabled": odds_api.enabled(),
            "odds_api_quota": _get_cache("cache:odds_api_quota")}
