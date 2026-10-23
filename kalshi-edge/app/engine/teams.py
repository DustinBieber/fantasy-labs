"""NFL team table and matching across Kalshi, ESPN, and The Odds API naming."""
from __future__ import annotations

import re
from typing import Dict, Optional

# code: (full name, city label, nickname, extra aliases)
NFL_TEAMS: Dict[str, tuple] = {
    "ARI": ("Arizona Cardinals", "Arizona", "Cardinals", []),
    "ATL": ("Atlanta Falcons", "Atlanta", "Falcons", []),
    "BAL": ("Baltimore Ravens", "Baltimore", "Ravens", []),
    "BUF": ("Buffalo Bills", "Buffalo", "Bills", []),
    "CAR": ("Carolina Panthers", "Carolina", "Panthers", []),
    "CHI": ("Chicago Bears", "Chicago", "Bears", []),
    "CIN": ("Cincinnati Bengals", "Cincinnati", "Bengals", []),
    "CLE": ("Cleveland Browns", "Cleveland", "Browns", []),
    "DAL": ("Dallas Cowboys", "Dallas", "Cowboys", []),
    "DEN": ("Denver Broncos", "Denver", "Broncos", []),
    "DET": ("Detroit Lions", "Detroit", "Lions", []),
    "GB": ("Green Bay Packers", "Green Bay", "Packers", ["GNB"]),
    "HOU": ("Houston Texans", "Houston", "Texans", []),
    "IND": ("Indianapolis Colts", "Indianapolis", "Colts", []),
    "JAX": ("Jacksonville Jaguars", "Jacksonville", "Jaguars", ["JAC"]),
    "KC": ("Kansas City Chiefs", "Kansas City", "Chiefs", ["KAN"]),
    "LV": ("Las Vegas Raiders", "Las Vegas", "Raiders", ["LVR", "OAK"]),
    "LAC": ("Los Angeles Chargers", "Los Angeles C", "Chargers", ["SD"]),
    "LAR": ("Los Angeles Rams", "Los Angeles R", "Rams", ["LA", "STL"]),
    "MIA": ("Miami Dolphins", "Miami", "Dolphins", []),
    "MIN": ("Minnesota Vikings", "Minnesota", "Vikings", []),
    "NE": ("New England Patriots", "New England", "Patriots", ["NWE"]),
    "NO": ("New Orleans Saints", "New Orleans", "Saints", ["NOR"]),
    "NYG": ("New York Giants", "New York G", "Giants", []),
    "NYJ": ("New York Jets", "New York J", "Jets", []),
    "PHI": ("Philadelphia Eagles", "Philadelphia", "Eagles", []),
    "PIT": ("Pittsburgh Steelers", "Pittsburgh", "Steelers", []),
    "SF": ("San Francisco 49ers", "San Francisco", "49ers", ["SFO"]),
    "SEA": ("Seattle Seahawks", "Seattle", "Seahawks", []),
    "TB": ("Tampa Bay Buccaneers", "Tampa Bay", "Buccaneers", ["TAM"]),
    "TEN": ("Tennessee Titans", "Tennessee", "Titans", []),
    "WAS": ("Washington Commanders", "Washington", "Commanders", ["WSH"]),
}

_ALIAS: Dict[str, str] = {}
for _code, (_full, _city, _nick, _extra) in NFL_TEAMS.items():
    for a in [_code, _full, _nick, *_extra]:
        _ALIAS[a.upper()] = _code
    # City labels are unique here ("Los Angeles R", "New York G" are Kalshi's own labels).
    _ALIAS[_city.upper()] = _code


def team_code(name: Optional[str]) -> Optional[str]:
    """Map any reasonable team label (code, city, nickname, full name) to our canonical code."""
    if not name:
        return None
    s = re.sub(r"\s+", " ", name.strip()).upper()
    if s in _ALIAS:
        return _ALIAS[s]
    for full_upper, code in ((v[0].upper(), k) for k, v in NFL_TEAMS.items()):
        if s == full_upper:
            return code
    # Contains a nickname (e.g. "Denver Broncos ML")
    for code, (_full, _city, nick, _extra) in NFL_TEAMS.items():
        if re.search(rf"\b{re.escape(nick.upper())}\b", s):
            return code
    return None


def team_name(code: str) -> str:
    return NFL_TEAMS.get(code, (code,))[0]


def kalshi_market_team(ticker: str, yes_sub_title: Optional[str]) -> Optional[str]:
    """Kalshi NFL game markets end in the team code (e.g. ...-26OCT25DALDEN-DAL)."""
    suffix = ticker.rsplit("-", 1)[-1].upper() if ticker else ""
    code = _ALIAS.get(suffix)
    if code:
        return code
    return team_code(yes_sub_title)
