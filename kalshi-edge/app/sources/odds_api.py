"""The Odds API (the-odds-api.com): multi-book NFL moneylines. Only used when ODDS_API_KEY is set.

Used as a fair-price reference only. Kalshi is where trades are placed; sportsbooks are never routed to.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import config
from ..engine.teams import team_code
from ..timeutil import parse_iso
from .http import get

SOURCE = "odds_api"
SPORT_KEYS = {"NFL": "americanfootball_nfl", "NCAAF": "americanfootball_ncaaf", "NBA": "basketball_nba"}


def enabled() -> bool:
    return bool(config.ODDS_API_KEY)


# Kalshi prop stat -> (main Odds API market, alternate milestone market)
PROP_MARKETS = {
    "rec_yds": ("player_reception_yds", "player_reception_yds_alternate"),
    "rush_yds": ("player_rush_yds", "player_rush_yds_alternate"),
    "pass_yds": ("player_pass_yds", "player_pass_yds_alternate"),
    "receptions": ("player_receptions", "player_receptions_alternate"),
    "pass_tds": ("player_pass_tds", "player_pass_tds_alternate"),
    "tds": ("player_anytime_td", None),
}
DEEP_GAME_MARKETS = ["alternate_spreads", "alternate_totals"]


def _quota(resp) -> Dict[str, str]:
    return {k: resp.headers.get(k, "") for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")}


def fetch_odds(sport: str = "NFL", regions: str = "us", bookmakers: str = "",
               markets: str = "h2h") -> Tuple[List[Dict[str, Any]], int, Dict[str, str]]:
    """Bulk odds for every upcoming game. Cost: 1 credit per market per region."""
    params: Dict[str, Any] = {"apiKey": config.ODDS_API_KEY, "markets": markets, "oddsFormat": "american"}
    if bookmakers:
        params["bookmakers"] = bookmakers
    else:
        params["regions"] = regions
    body, pull_id, resp = get(SOURCE, f"{config.ODDS_API_BASE}/sports/{SPORT_KEYS[sport]}/odds", params)
    return body if isinstance(body, list) else [], pull_id, _quota(resp)


def fetch_h2h(sport: str = "NFL", regions: str = "us", bookmakers: str = "") -> Tuple[List[Dict[str, Any]], int, Dict[str, str]]:
    return fetch_odds(sport, regions, bookmakers, "h2h")


def fetch_event_odds(event_id: str, markets: List[str], sport: str = "NFL", regions: str = "us",
                     bookmakers: str = "") -> Tuple[Dict[str, Any], int, Dict[str, str]]:
    """Alternate lines and player props, one game at a time. Cost: markets x regions credits."""
    params: Dict[str, Any] = {"apiKey": config.ODDS_API_KEY, "markets": ",".join(markets), "oddsFormat": "american"}
    if bookmakers:
        params["bookmakers"] = bookmakers
    else:
        params["regions"] = regions
    body, pull_id, resp = get(SOURCE, f"{config.ODDS_API_BASE}/sports/{SPORT_KEYS[sport]}/events/{event_id}/odds", params)
    return body if isinstance(body, dict) else {}, pull_id, _quota(resp)


def parse_outcomes(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten any Odds API response to one row per (game, book, market, outcome): spreads, totals,
    alternates, and player props (player name is in `description`)."""
    rows = []
    for ev in events:
        home, away = team_code(ev.get("home_team")), team_code(ev.get("away_team"))
        if not home or not away:
            continue
        commence = parse_iso(ev.get("commence_time"))
        for bk in ev.get("bookmakers") or []:
            for mk in bk.get("markets") or []:
                key = mk.get("key")
                if not key or key == "h2h":
                    continue
                for o in mk.get("outcomes") or []:
                    if o.get("price") is None:
                        continue
                    try:
                        price = int(o["price"])
                    except (TypeError, ValueError):
                        continue
                    point = o.get("point")
                    rows.append({"home_team": home, "away_team": away, "commence_ts": commence,
                                 "event_id": ev.get("id"), "book": bk.get("key") or bk.get("title"), "market_key": key,
                                 "outcome_name": str(o.get("name") or ""), "description": o.get("description"),
                                 "point": float(point) if point is not None else None, "price": price})
    return rows


def parse_h2h(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten to one row per (game, book): home/away canonical codes and American prices."""
    rows = []
    for ev in events:
        home, away = team_code(ev.get("home_team")), team_code(ev.get("away_team"))
        if not home or not away:
            continue
        commence = parse_iso(ev.get("commence_time"))
        for bk in ev.get("bookmakers") or []:
            for mk in bk.get("markets") or []:
                if mk.get("key") != "h2h":
                    continue
                prices: Dict[str, Optional[int]] = {}
                for o in mk.get("outcomes") or []:
                    code = team_code(o.get("name"))
                    if code and o.get("price") is not None:
                        prices[code] = int(o["price"])
                if home in prices and away in prices and len(prices) == 2:
                    rows.append({"home_team": home, "away_team": away, "commence_ts": commence,
                                 "book": bk.get("key") or bk.get("title"), "home_price": prices[home],
                                 "away_price": prices[away]})
    return rows
