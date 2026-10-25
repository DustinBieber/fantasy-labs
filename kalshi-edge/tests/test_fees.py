from app.config import DEFAULT_FEES
from app.engine.fees import fee_rates_for, kalshi_fee


def test_general_fee_formula():
    # 0.07 x 100 x 0.50 x 0.50 = 1.75 exactly
    assert kalshi_fee(100, 0.50, 0.07) == 1.75
    # Direct members settle to $0.0001: one 50c contract costs exactly 0.0175
    assert kalshi_fee(1, 0.50, 0.07) == 0.0175
    # 0.07 x 1 x 0.33 x 0.67 = 0.015477 -> rounds UP to 0.0155
    assert kalshi_fee(1, 0.33, 0.07) == 0.0155


def test_non_direct_cent_rounding():
    assert kalshi_fee(1, 0.50, 0.07, precision=0.01) == 0.02
    assert kalshi_fee(10, 0.30, 0.07, precision=0.01) == 0.15  # 0.147 -> 0.15


def test_exact_values_are_not_bumped_by_float_error():
    # 0.07 x 100 x 0.10 x 0.90 = 0.63 exactly; float math would give 0.6300000000000001
    assert kalshi_fee(100, 0.10, 0.07) == 0.63
    assert kalshi_fee(100, 0.10, 0.07, precision=0.01) == 0.63


def test_subpenny_prices():
    # 0.0175 x 10 x 0.565 x 0.435 = 0.04300... -> 0.0431
    assert kalshi_fee(10, 0.565, 0.0175) == 0.0431


def test_fee_is_symmetric_in_price_and_zero_at_edges():
    assert kalshi_fee(37, 0.23, 0.07) == kalshi_fee(37, 0.77, 0.07)
    assert kalshi_fee(0, 0.5, 0.07) == 0.0
    assert kalshi_fee(10, 0.0, 0.07) == 0.0
    assert kalshi_fee(10, 1.0, 0.07) == 0.0


def test_maker_ratio_follows_fee_type():
    q = fee_rates_for("KXNFLGAME", {"fee_type": "quadratic", "fee_multiplier": 1}, DEFAULT_FEES)
    assert (q.taker_rate, q.maker_rate) == (0.07, 0.0)
    m = fee_rates_for("KXNFLGAME", {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1}, DEFAULT_FEES)
    assert (m.taker_rate, m.maker_rate) == (0.07, 0.0175)
    c = fee_rates_for("KXMVE", {"fee_type": "quadratic_with_combo_maker_fees", "fee_multiplier": 1}, DEFAULT_FEES)
    assert c.maker_rate == 0.035


def test_multiplier_scales_and_zero_means_fee_free():
    half = fee_rates_for("KXINXD", {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 0.5}, DEFAULT_FEES)
    assert (half.taker_rate, half.maker_rate) == (0.035, 0.00875)
    free = fee_rates_for("KXPROMO", {"fee_type": "quadratic", "fee_multiplier": 0}, DEFAULT_FEES)
    assert (free.taker_rate, free.maker_rate) == (0.0, 0.0)


def test_event_override_beats_series():
    r = fee_rates_for("KXNFLGAME", {"fee_type": "quadratic", "fee_multiplier": 1}, DEFAULT_FEES,
                      {"fee_type_override": "quadratic_with_maker_fees", "fee_multiplier_override": 1})
    assert r.maker_rate == 0.0175 and "event override" in r.basis


def test_unknown_series_is_conservative_and_index_prefix_applies():
    r = fee_rates_for("KXSOMETHING", None, DEFAULT_FEES)
    assert r.maker_rate == 0.0175 and "conservative" in r.basis
    assert fee_rates_for("KXINXU", None, DEFAULT_FEES).taker_rate == 0.035
    flat = fee_rates_for("KXODD", {"fee_type": "flat", "fee_multiplier": 1}, DEFAULT_FEES)
    assert flat.maker_rate == 0.0175 and "conservative" in flat.basis


def test_manual_override_wins():
    cfg = dict(DEFAULT_FEES, series_overrides={"KXAAAGASD": {"maker_rate": 0.0}})
    assert fee_rates_for("KXAAAGASD", None, cfg).maker_rate == 0.0
