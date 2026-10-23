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


def fetch_h2h(sport: str = "NFL", regions: str = "us", bookmakers: str = "") -> Tuple[List[Dict[str, Any]], int, Dict[str, str]]:
    params: Dict[str, Any] = {"apiKey": config.ODDS_API_KEY, "markets": "h2h", "oddsFormat": "american"}
    if bookmakers:
        params["bookmakers"] = bookmakers
    else:
        params["regions"] = regions
    body, pull_id, resp = get(SOURCE, f"{config.ODDS_API_BASE}/sports/{SPORT_KEYS[sport]}/odds", params)
    quota = {k: resp.headers.get(k, "") for k in ("x-requests-remaining", "x-requests-used", "x-requests-last")}
    return body if isinstance(body, list) else [], pull_id, quota


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
