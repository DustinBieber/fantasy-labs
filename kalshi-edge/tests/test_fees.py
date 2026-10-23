from app.config import DEFAULT_FEES
from app.engine.fees import fee_rates_for, kalshi_fee


def test_general_fee_matches_schedule_examples():
    # 0.07 x 100 x 0.50 x 0.50 = 1.75 exactly
    assert kalshi_fee(100, 0.50, 0.07) == 1.75
    # 0.07 x 1 x 0.50 x 0.50 = 0.0175 -> rounds UP to 0.02
    assert kalshi_fee(1, 0.50, 0.07) == 0.02
    # 0.07 x 10 x 0.30 x 0.70 = 0.147 -> 0.15
    assert kalshi_fee(10, 0.30, 0.07) == 0.15


def test_exact_cent_is_not_bumped_by_float_error():
    # 0.07 x 100 x 0.10 x 0.90 = 0.63 exactly; float math would give 0.6300000000000001
    assert kalshi_fee(100, 0.10, 0.07) == 0.63


def test_maker_and_index_rates():
    assert kalshi_fee(100, 0.50, 0.0175) == 0.44  # 0.4375 -> 0.44
    assert kalshi_fee(100, 0.50, 0.035) == 0.88  # 0.875 -> 0.88


def test_fee_is_symmetric_in_price_and_zero_at_edges():
    assert kalshi_fee(37, 0.23, 0.07) == kalshi_fee(37, 0.77, 0.07)
    assert kalshi_fee(0, 0.5, 0.07) == 0.0
    assert kalshi_fee(10, 0.0, 0.07) == 0.0
    assert kalshi_fee(10, 1.0, 0.07) == 0.0


def test_fee_rates_from_series_metadata():
    assert fee_rates_for("KXNFLGAME", {"fee_type": "quadratic", "fee_multiplier": 1}, DEFAULT_FEES).maker_rate == 0.0
    r = fee_rates_for("KXNFLGAME", {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1}, DEFAULT_FEES)
    assert (r.taker_rate, r.maker_rate) == (0.07, 0.0175)
    half = fee_rates_for("KXINXD", {"fee_type": "quadratic", "fee_multiplier": 0.5}, DEFAULT_FEES)
    assert half.taker_rate == 0.035


def test_unknown_series_is_conservative_and_index_prefix_applies():
    r = fee_rates_for("KXSOMETHING", None, DEFAULT_FEES)
    assert r.maker_rate == 0.0175 and "conservative" in r.basis
    assert fee_rates_for("KXINXU", None, DEFAULT_FEES).taker_rate == 0.035


def test_manual_override_wins():
    cfg = dict(DEFAULT_FEES, series_overrides={"KXAAAGASD": {"maker_rate": 0.0}})
    assert fee_rates_for("KXAAAGASD", None, cfg).maker_rate == 0.0
