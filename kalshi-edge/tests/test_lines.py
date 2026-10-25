import pytest

from app.engine import lines as ln
from app.engine.devig import american_to_implied, devig_two_way
from app.engine.gas import normal_cdf

REC = ("player_reception_yds", "player_reception_yds_alternate")
TD = ("player_anytime_td", None)


def o(book, key, name, price, point=None, desc=None, ts=100):
    return {"book": book, "market_key": key, "outcome_name": name, "price": price, "point": point,
            "description": desc, "ts": ts}


def test_parse_kalshi_markets():
    assert ln.split_matchup("KXNFLSPREAD-26OCT22NECHI") == ("NE", "CHI")
    assert ln.split_matchup("KXNFLTOTAL-26NOV01LACKC") == ("LAC", "KC")
    assert ln.split_matchup("BAD") is None
    assert ln.parse_spread({"ticker": "KXNFLSPREAD-26OCT22NECHI-CHI4", "floor_strike": 3.5,
                            "title": "Chicago wins by over 3.5 points?"}) == ("CHI", 3.5)
    assert ln.parse_spread({"ticker": "X-Y-Z", "title": "Chicago wins by over 6.5 points?"}) == ("CHI", 6.5)
    assert ln.parse_spread({"ticker": "KXNFLSPREAD-X-CHI7", "floor_strike": 7, "strike_type": "greater_or_equal"}) == ("CHI", 6.5)
    assert ln.parse_total({"floor_strike": 44.5, "title": "Over 44.5 points scored?"}) == 44.5
    assert ln.parse_prop({"title": "Colston Loveland: 25+ receiving yards"}) == ("Colston Loveland", "rec_yds", 25)
    assert ln.parse_prop({"title": "Jahmyr Gibbs: 1+ touchdowns"}) == ("Jahmyr Gibbs", "tds", 1)
    assert ln.parse_prop({"title": "Drake Maye: 2+ passing touchdowns"}) == ("Drake Maye", "pass_tds", 2)
    assert ln.parse_prop({"title": "Chicago wins by over 3.5 points?"}) is None


def test_names_match_across_sources():
    assert ln.norm_name("Kenneth Walker III") == ln.norm_name("Kenneth Walker")
    assert ln.norm_name("A.J. Brown") == ln.norm_name("AJ Brown")
    assert ln.norm_name("Amon-Ra St. Brown") == ln.norm_name("Amon Ra St Brown")


def test_spread_exact_and_sharp_preference():
    rows = [o("draftkings", "alternate_spreads", "Denver Broncos", -150, -3.5), o("draftkings", "alternate_spreads", "Dallas Cowboys", 130, 3.5),
            o("pinnacle", "alternate_spreads", "Denver Broncos", -140, -3.5), o("pinnacle", "alternate_spreads", "Dallas Cowboys", 125, 3.5)]
    f = ln.spread_fair(rows, "DEN", 3.5, sharp=["pinnacle"])
    assert f["kind"] == "exact" and f["p"] == pytest.approx(devig_two_way(-140, 125)["home"])
    # Dallas +3.5 covering ("Dallas margin > -3.5") is exactly the other side of Denver -3.5.
    g = ln.spread_fair(rows, "DAL", -3.5, sharp=["pinnacle"])
    assert g["kind"] == "exact" and g["p"] == pytest.approx(1 - f["p"], abs=1e-12)


def test_spread_model_fallback_is_labeled_and_optional():
    rows = [o("fanduel", "spreads", "Denver Broncos", -110, -2.5), o("fanduel", "spreads", "Dallas Cowboys", -110, 2.5)]
    f = ln.spread_fair(rows, "DEN", 6.5, sd=13.5)
    p0 = 0.5
    mu = 2.5 + 13.5 * ln._inv_norm(p0)
    assert f["kind"] == "model" and f["p"] == pytest.approx(1 - normal_cdf((6.5 - mu) / 13.5))
    assert f["confidence"] == "low"
    assert ln.spread_fair(rows, "DEN", 6.5, allow_model=False) is None
    assert ln.spread_fair(rows, "DEN", 2.5)["kind"] == "exact"


def test_total_exact_and_model():
    rows = [o("draftkings", "totals", "Over", -115, 44.5), o("draftkings", "totals", "Under", -105, 44.5),
            o("draftkings", "alternate_totals", "Over", 150, 48.5), o("draftkings", "alternate_totals", "Under", -180, 48.5)]
    assert ln.total_fair(rows, 44.5)["p"] == pytest.approx(devig_two_way(-115, -105)["home"])
    assert ln.total_fair(rows, 48.5)["p"] == pytest.approx(devig_two_way(150, -180)["home"])
    m = ln.total_fair(rows, 51.5)
    assert m["kind"] == "model" and m["p"] < ln.total_fair(rows, 48.5)["p"]


def test_prop_exact_scaled_and_unrated():
    rows = [o("dk", "player_reception_yds", "Over", -115, 59.5, "A.J. Brown"),
            o("dk", "player_reception_yds", "Under", -115, 59.5, "A.J. Brown"),
            o("dk", "player_reception_yds_alternate", "Over", 200, 79.5, "A.J. Brown")]
    exact = ln.prop_fair(rows, "AJ Brown", "rec_yds", 60, REC)
    assert exact["kind"] == "exact" and exact["p"] == pytest.approx(0.5)
    scaled = ln.prop_fair(rows, "A.J. Brown", "rec_yds", 80, REC)
    factor = 0.5 / american_to_implied(-115)
    assert scaled["kind"] == "scaled" and scaled["p"] == pytest.approx(american_to_implied(200) * factor)
    assert ln.prop_fair(rows, "A.J. Brown", "rec_yds", 100, REC) is None  # no line at 99.5: unrated
    assert ln.prop_fair(rows, "DeVonta Smith", "rec_yds", 60, REC) is None


def test_anytime_td_needs_both_sides():
    two = [o("fd", "player_anytime_td", "Yes", 120, None, "Jahmyr Gibbs"), o("fd", "player_anytime_td", "No", -150, None, "Jahmyr Gibbs")]
    assert ln.prop_fair(two, "Jahmyr Gibbs", "tds", 1, TD)["p"] == pytest.approx(devig_two_way(120, -150)["home"])
    assert ln.prop_fair(two[:1], "Jahmyr Gibbs", "tds", 1, TD) is None  # one-sided Yes: can't remove the vig honestly
    assert ln.prop_fair(two, "Jahmyr Gibbs", "tds", 2, TD) is None


def test_only_latest_pull_per_book_counts():
    rows = [o("dk", "totals", "Over", -200, 44.5, ts=1), o("dk", "totals", "Under", 170, 44.5, ts=1),
            o("dk", "totals", "Over", -110, 44.5, ts=2), o("dk", "totals", "Under", -110, 44.5, ts=2)]
    assert ln.total_fair(rows, 44.5)["p"] == pytest.approx(0.5)


def test_inverse_normal():
    for p in (0.001, 0.02, 0.3, 0.5, 0.77, 0.99, 0.9995):
        assert normal_cdf(ln._inv_norm(p)) == pytest.approx(p, abs=1e-7)
