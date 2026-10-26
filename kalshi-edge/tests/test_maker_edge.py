import copy

import pytest

from app.config import DEFAULT_SETTINGS
from app.engine.edge import evaluate
from app.engine.maker import maker_price, side_quotes


def test_maker_price_rules():
    assert maker_price(0.40, 0.45)[0] == 0.41
    assert maker_price(0.44, 0.45)[0] == 0.44  # one-tick spread: join
    assert maker_price(None, 0.30)[0] == 0.29  # empty bid
    assert maker_price(0.90, None)[0] == 0.91  # no ask
    assert maker_price(None, None)[0] is None
    assert maker_price(None, 0.01)[0] is None
    assert maker_price(0.40, 0.45, improve_ticks=3)[0] == 0.43
    assert maker_price(0.40, 0.45, improve_ticks=9)[0] == 0.40  # never cross: falls back to joining


def test_price_grid_steps():
    from app.engine.maker import tick_at
    deci = [[0.001, 0.999, 0.001]]
    tapered = [[0.0001, 0.01, 0.0001], [0.01, 0.99, 0.01], [0.99, 0.9999, 0.0001]]
    assert tick_at(deci, 0.40) == 0.001
    assert tick_at(tapered, 0.50) == 0.01
    assert tick_at(tapered, 0.005) == 0.0001
    assert tick_at(tapered, 0.01) == 0.0001  # band edge: the finer step applies
    assert tick_at(None, 0.5) == 0.01
    assert maker_price(0.40, 0.44, ranges=deci)[0] == 0.401
    assert maker_price(0.40, 0.401, ranges=deci)[0] == 0.40  # one-step spread: join
    assert maker_price(0.40, 0.44, ranges='[[0.001, 0.999, 0.001]]')[0] == 0.401  # stored JSON form


def test_no_side_quotes_derived_from_yes():
    q = side_quotes(0.42, 0.44, None, None)
    assert q["no"] == (0.56, 0.58)


def _market(**kw):
    m = {"ticker": "KXT-1", "event_ticker": "KXT", "series_ticker": "KXT", "yes_bid": 0.40, "yes_ask": 0.45,
         "no_bid": None, "no_ask": None, "tick_size": 0.01, "yes_sub_title": "Team A", "updated_ts": 0}
    m.update(kw)
    return m


def test_evaluate_tier_s_with_full_math():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    o = evaluate(_market(), "yes", 0.60, {"steps": ["fair step"], "plain": "Books"}, s, 1000.0)
    assert o["maker_price"] == 0.41
    assert o["raw_edge_pts"] == pytest.approx(19.0)
    assert o["tier"] == "S"
    sz = o["size"]
    assert sz["contracts"] > 0
    assert sz["max_loss"] == pytest.approx(sz["dollars"] + sz["fee"])
    assert sz["max_loss"] <= 25.0 + 1e-9
    # headline EV is net of the rounded fee at the recommended size
    expected = 100 * (sz["contracts"] * 0.60 - sz["contracts"] * 0.41 - sz["fee"]) / (sz["contracts"] * 0.41 + sz["fee"])
    assert o["net_ev_pct"] == pytest.approx(expected, abs=0.01)
    assert any("Raw edge" in line for line in o["math"])
    assert "Tier S" in o["reason"]


def test_evaluate_no_side_and_force_pass():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    o = evaluate(_market(), "no", 0.40, {"steps": [], "plain": "Books"}, s, 1000.0)
    assert o["maker_price"] == 0.56  # NO bid 0.55 (1 - 0.45) improved by a tick, under NO ask 0.60
    assert o["tier"] == "P"
    f = evaluate(_market(), "yes", 0.60, {"steps": [], "plain": "Books"}, s, 1000.0, force_pass="Pass: stale")
    assert f["tier"] == "P" and f["size"]["contracts"] == 0


def test_evaluate_without_price_never_invents_one():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    o = evaluate(_market(yes_bid=None, yes_ask=None), "yes", 0.6, {"steps": []}, s, 1000.0)
    assert o["maker_price"] is None and o["tier"] == "P"


def test_settlement_value_handles_ties():
    from app.services.portfolio import settlement_yes_value
    assert settlement_yes_value({"result": "yes"}) == 1.0
    assert settlement_yes_value({"result": "no"}) == 0.0
    assert settlement_yes_value({"result": None, "status": "finalized", "settlement_value": 0.5}) == 0.5
    assert settlement_yes_value({"result": None, "status": "active", "settlement_value": None}) is None
