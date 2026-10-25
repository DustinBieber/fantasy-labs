import pytest
from sqlalchemy import insert

from app import db
from app.engine.devig import devig_two_way
from app.services.nfl_service import fair_for_game
from app.settings_store import get_settings
from app.timeutil import now_ts


def _line(book, home_price, away_price, source="odds_api"):
    return {"sport": "NFL", "source": source, "book": book, "home_team": "DEN", "away_team": "DAL",
            "commence_ts": None, "home_price": home_price, "away_price": away_price, "ts": now_ts(), "pull_id": 1}


def test_sharp_book_beats_the_crowd(tmpdb):
    with db.get_engine().begin() as c:
        c.execute(insert(db.odds_lines), [_line("draftkings", -220, 180), _line("fanduel", -215, 175),
                                          _line("pinnacle", -190, 172)])
    f = fair_for_game(("DEN", "DAL"), get_settings())
    assert f["fair_first"] == pytest.approx(devig_two_way(-190, 172)["home"])
    assert "Sharp book" in f["plain"] and f["confidence"] == "normal"


def test_median_when_no_sharp_book(tmpdb):
    with db.get_engine().begin() as c:
        c.execute(insert(db.odds_lines), [_line("draftkings", -220, 180), _line("fanduel", -215, 175),
                                          _line("betmgm", -200, 170)])
    f = fair_for_game(("DAL", "DEN"), get_settings())  # ask from Dallas's side
    assert f["fair_first"] == pytest.approx(devig_two_way(-215, 175)["away"])
    assert "median of 3 books" in f["plain"]


def test_manual_line_is_used_without_odds_api(tmpdb):
    with db.get_engine().begin() as c:
        c.execute(insert(db.odds_lines), [_line("My screen", -150, 130, source="manual")])
    f = fair_for_game(("DEN", "DAL"), get_settings())
    assert f["source"] == "manual" and f["confidence"] == "low"
