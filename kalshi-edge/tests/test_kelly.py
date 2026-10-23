import pytest

from app.engine.fees import kalshi_fee
from app.engine.kelly import kelly_binary, size_position


def test_kelly_binary():
    assert kelly_binary(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_binary(0.5, 0.5) == 0.0
    assert kelly_binary(0.4, 0.5) == 0.0
    assert kelly_binary(0.9, 1.0) == 0.0


def _size(**kw):
    base = dict(q=0.65, price=0.50, fee_rate=0.0175, bankroll=1000.0, kelly_mult=0.25, tier_mult=1.0,
                per_strike_cap_pct=2.5, correlated_room=None, fee_fn=kalshi_fee)
    base.update(kw)
    return size_position(**base)


def test_per_strike_cap_binds_and_fee_fits_inside_stake():
    s = _size()
    # quarter Kelly here is ~$73; the 2.5% cap ($25) binds
    assert s["binding"] == "per-strike cap"
    assert s["contracts"] == 49
    assert s["fee"] == 0.22  # 0.0175 x 49 x 0.25 = 0.214 -> 0.22
    assert s["max_loss"] == pytest.approx(24.72)
    assert s["max_loss"] <= 25.0


def test_quarter_kelly_binds_on_small_edges():
    s = _size(q=0.53, per_strike_cap_pct=10)
    cost = 0.5 + 0.0175 * 0.25
    expected_f = 0.25 * (0.53 - cost) / (1 - cost)
    assert s["binding"] == "kelly"
    assert s["kelly_used"] == pytest.approx(expected_f)
    assert s["contracts"] * 0.5 + s["fee"] <= expected_f * 1000 + 1e-9


def test_correlated_room_and_half_size():
    assert _size(correlated_room=5.0)["binding"] == "correlated cap room"
    assert _size(correlated_room=5.0)["max_loss"] <= 5.0
    assert _size(correlated_room=-3.0)["contracts"] == 0
    full = _size(per_strike_cap_pct=50)
    half = _size(per_strike_cap_pct=50, tier_mult=0.5)
    assert half["kelly_used"] == pytest.approx(full["kelly_used"] / 2)


def test_no_edge_no_size():
    s = _size(q=0.50)
    assert s["contracts"] == 0 and s["max_loss"] == 0
