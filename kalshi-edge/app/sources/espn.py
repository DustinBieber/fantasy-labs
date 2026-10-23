"""ESPN public NFL scoreboard: schedule, venue, weather, and one sportsbook's moneyline.

This is an unofficial public endpoint. Its odds come from a single book, so the app labels any
fair price built from it as single-book / low confidence. Parsing is defensive; if ESPN changes
its shape, rows are skipped and the pull is marked with zero rows rather than guessed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import config
from ..engine.teams import team_code
from ..timeutil import parse_iso
from .http import get

SOURCE = "espn"


def fetch_scoreboard(dates: Optional[str] = None) -> Tuple[Dict[str, Any], int]:
    params = {"dates": dates} if dates else None
    body, pull_id, _ = get(SOURCE, config.ESPN_NFL_SCOREBOARD, params)
    return body, pull_id


def _american(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if abs(v) >= 100 else None
    s = str(v).strip().upper()
    if s in ("EVEN", "EV"):
        return 100
    try:
        n = int(float(s.replace("+", "")))
        return n if abs(n) >= 100 else None
    except ValueError:
        return None


def _moneylines(odds: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    home = _american((odds.get("homeTeamOdds") or {}).get("moneyLine"))
    away = _american((odds.get("awayTeamOdds") or {}).get("moneyLine"))
    if home is None or away is None:
        ml = odds.get("moneyline") or {}
        for key, slot in (("home", "home"), ("away", "away")):
            node = ml.get(slot) or {}
            val = None
            for stage in ("current", "close", "open"):
                val = _american((node.get(stage) or {}).get("odds"))
                if val is not None:
                    break
            if key == "home" and home is None:
                home = val
            if key == "away" and away is None:
                away = val
    return home, away


def parse_games(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    games = []
    for ev in body.get("events") or []:
        comps = ev.get("competitions") or [{}]
        comp = comps[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        h, a = teams.get("home") or {}, teams.get("away") or {}
        home = team_code((h.get("team") or {}).get("abbreviation")) or team_code((h.get("team") or {}).get("displayName"))
        away = team_code((a.get("team") or {}).get("abbreviation")) or team_code((a.get("team") or {}).get("displayName"))
        if not home or not away:
            continue
        odds_list = comp.get("odds") or []
        odds = odds_list[0] if odds_list else {}
        hml, aml = _moneylines(odds) if odds else (None, None)
        venue = comp.get("venue") or {}
        weather = ev.get("weather") or {}
        status = ((ev.get("status") or {}).get("type") or {})
        games.append({
            "home_team": home, "away_team": away,
            "commence_ts": parse_iso(ev.get("date")),
            "name": ev.get("name"),
            "status": status.get("description") or status.get("name"),
            "completed": bool(status.get("completed")),
            "venue": venue.get("fullName"),
            "indoor": venue.get("indoor"),
            "weather": weather.get("displayValue"),
            "temperature": weather.get("temperature"),
            "book": ((odds.get("provider") or {}).get("name")) if odds else None,
            "details": odds.get("details") if odds else None,
            "home_price": hml, "away_price": aml,
            "home_score": h.get("score"), "away_score": a.get("score"),
        })
    return games
