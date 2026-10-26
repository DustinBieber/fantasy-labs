"""Parser tests. Inputs are hand-written fixtures shaped like each source's documented responses;
they test parsing only and are never shown in the app."""
from app.engine.teams import kalshi_market_team, team_code
from app.sources import aaa, espn, kalshi, odds_api


def test_kalshi_parse_market_dollar_and_cent_fields_agree():
    cents = {"ticker": "KXNFLGAME-26OCT25DALDEN-DEN", "event_ticker": "KXNFLGAME-26OCT25DALDEN", "yes_bid": 55,
             "yes_ask": 57, "no_bid": 43, "no_ask": 45, "last_price": 56, "volume": 1200, "open_interest": 800,
             "status": "active", "close_time": "2026-10-25T20:25:00Z", "tick_size": 1}
    dollars = dict(cents, yes_bid=None, yes_ask=None, no_bid=None, no_ask=None, last_price=None, volume=None,
                   yes_bid_dollars="0.5500", yes_ask_dollars="0.5700", no_bid_dollars="0.4300",
                   no_ask_dollars="0.4500", last_price_dollars="0.5600", volume_fp="1200.00")
    a, b = kalshi.parse_market(cents), kalshi.parse_market(dollars)
    for f in ("yes_bid", "yes_ask", "no_bid", "no_ask", "last_price", "volume"):
        assert a[f] == b[f], f
    assert a["yes_bid"] == 0.55 and a["tick_size"] == 0.01
    assert a["series_ticker"] == "KXNFLGAME"
    assert a["close_ts"] == 1792959900


def test_kalshi_current_shape_grid_and_settlement():
    m = kalshi.parse_market({
        "ticker": "KXAAAGASD-26OCT24-4.270", "event_ticker": "KXAAAGASD-26OCT24", "yes_bid_dollars": "0.4010",
        "yes_ask_dollars": "0.4350", "last_price_dollars": "0.0000", "volume_fp": "12.50", "open_interest_fp": "3.00",
        "price_level_structure": "deci_cent", "price_ranges": [{"start": "0.0010", "end": "0.9990", "step": "0.0010"}],
        "status": "finalized", "result": "", "settlement_value_dollars": "0.5000", "strike_type": "greater",
        "floor_strike": 4.27})
    assert (m["yes_bid"], m["yes_ask"], m["last_price"]) == (0.401, 0.435, None)
    assert m["volume"] == 12.5 and m["tick_size"] == 0.001
    assert m["price_ranges"] == "[[0.001, 0.999, 0.001]]"
    assert m["settlement_value"] == 0.5 and m["result"] is None


def test_kalshi_event_fee_override():
    e = kalshi.parse_event({"event_ticker": "KXNFLGAME-X", "fee_type_override": "quadratic_with_maker_fees",
                            "fee_multiplier_override": "1"})
    assert e["fee_type_override"] == "quadratic_with_maker_fees" and e["fee_multiplier_override"] == 1.0


def test_kalshi_empty_sides_become_none():
    m = kalshi.parse_market({"ticker": "X-1", "event_ticker": "X", "yes_bid": 0, "yes_ask": 100, "last_price": 0})
    assert m["yes_bid"] is None and m["yes_ask"] is None and m["last_price"] is None


def test_kalshi_candle_mid():
    assert kalshi.candle_mid({"yes_bid": {"close": 40}, "yes_ask": {"close": 44}}) == 0.42
    assert kalshi.candle_mid({"yes_bid": {"close_dollars": "0.40"}, "yes_ask": {"close_dollars": "0.44"}}) == 0.42
    assert kalshi.candle_mid({"yes_bid": {"close": 0}, "yes_ask": {"close": 44}, "price": {"close": 30}}) is None
    # /historical candles: no _dollars suffix, but values are still dollars
    assert kalshi.candle_mid({"yes_bid": {"close": "0.4000"}, "yes_ask": {"close": "0.4400"}}) == 0.42


def test_team_matching():
    assert kalshi_market_team("KXNFLGAME-26OCT25DALDEN-DAL", None) == "DAL"
    assert kalshi_market_team("KXNFLGAME-26OCT25JACTEN-JAC", None) == "JAX"
    assert kalshi_market_team("KXNFLGAME-X-ZZZ", "Los Angeles R") == "LAR"
    assert team_code("Kansas City Chiefs") == "KC"
    assert team_code("WSH") == "WAS"
    assert team_code("New York Jets") == "NYJ"
    assert team_code("Nowhere") is None


def test_espn_parse_both_odds_shapes():
    def ev(odds):
        return {"date": "2026-10-25T20:25Z", "name": "Dallas Cowboys at Denver Broncos",
                "status": {"type": {"description": "Scheduled", "completed": False}},
                "competitions": [{"competitors": [
                    {"homeAway": "home", "team": {"abbreviation": "DEN", "displayName": "Denver Broncos"}},
                    {"homeAway": "away", "team": {"abbreviation": "DAL", "displayName": "Dallas Cowboys"}}],
                    "venue": {"fullName": "Empower Field", "indoor": False}, "odds": [odds]}]}
    old = {"provider": {"name": "ESPN BET"}, "homeTeamOdds": {"moneyLine": -200}, "awayTeamOdds": {"moneyLine": 170}}
    new = {"provider": {"name": "DraftKings"}, "moneyline": {"home": {"close": {"odds": "-200"}},
                                                             "away": {"close": {"odds": "+170"}}}}
    for odds in (old, new):
        gm = espn.parse_games({"events": [ev(odds)]})[0]
        assert (gm["home_team"], gm["away_team"], gm["home_price"], gm["away_price"]) == ("DEN", "DAL", -200, 170)
    assert espn.parse_games({"events": [ev({})]})[0]["home_price"] is None


def test_odds_api_parse():
    rows = odds_api.parse_h2h([{"home_team": "Denver Broncos", "away_team": "Dallas Cowboys",
                                "commence_time": "2026-10-25T20:25:00Z", "bookmakers": [
                                    {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
                                        {"name": "Denver Broncos", "price": -195}, {"name": "Dallas Cowboys", "price": 165}]}]},
                                    {"key": "broken", "markets": [{"key": "h2h", "outcomes": [
                                        {"name": "Denver Broncos", "price": -195}]}]}]}])
    assert rows == [{"home_team": "DEN", "away_team": "DAL", "commence_ts": 1792959900, "book": "fanduel",
                     "home_price": -195, "away_price": 165}]


def test_aaa_parse():
    page = """<div><p>Today&#039;s AAA National Average</p><p class="numb">$3.123</p>
    <p>Price as of 10/23/26</p><table><tr><td>Current Avg.</td><td>$3.123</td></tr>
    <tr><td>Yesterday Avg.</td><td>$3.118</td></tr><tr><td>Week Ago Avg.</td><td>$3.090</td></tr></table></div>"""
    out = aaa.parse_national_average(page)
    assert out["value"] == 3.123
    assert out["date"] == "2026-10-23"
    assert out["table"]["Yesterday Avg."] == 3.118


def test_aaa_parse_four_decimals():
    page = "<p>Today’s AAA National Average $4.2926</p><p>Price as of 10/23/26</p>"
    assert aaa.parse_national_average(page)["value"] == 4.2926


def test_aaa_parse_fails_loudly():
    import pytest
    with pytest.raises(ValueError):
        aaa.parse_national_average("<html>Access denied</html>")
