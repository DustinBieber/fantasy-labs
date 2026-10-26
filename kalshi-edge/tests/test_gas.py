from datetime import date, timedelta

import pytest

from app.config import DEFAULT_SETTINGS
from app.engine import gas as g

CFG = DEFAULT_SETTINGS["gas"]


def test_parse_ticker_date():
    assert g.parse_ticker_date("KXAAAGASD-26OCT24") == date(2026, 10, 24)
    assert g.parse_ticker_date("KXAAAGASD-26OCT24-T3.10") == date(2026, 10, 24)
    assert g.parse_ticker_date("NODATE") is None


def test_pava_makes_survival_nonincreasing():
    fit = g.pava_nonincreasing([0.9, 0.95, 0.5, 0.55, 0.1])
    assert all(a >= b for a, b in zip(fit, fit[1:]))
    assert fit[0] == pytest.approx(0.925) and fit[2] == pytest.approx(0.525)


def test_implied_median_interpolates():
    med, how = g.implied_median([3.00, 3.10, 3.20], [0.8, 0.4, 0.1])
    assert med == pytest.approx(3.075)
    assert how == "interpolated"
    assert g.implied_median([3.0, 3.1], [0.9, 0.7])[0] is None
    assert "above the top strike" in g.implied_median([3.0, 3.1], [0.9, 0.7])[1]


def _thr(k, bid, ask, t="greater"):
    return {"ticker": f"G-T{k}", "strike_type": t, "floor_strike": k, "cap_strike": None, "yes_bid": bid, "yes_ask": ask}


def test_ladder_points_from_threshold_markets():
    lad = g.ladder_points([_thr(3.10, 0.40, 0.44), _thr(3.00, 0.80, 0.84), _thr(3.20, 0.08, 0.12)])
    assert lad["kind"] == "threshold"
    assert lad["strikes"] == [3.00, 3.10, 3.20]
    assert lad["survival"] == pytest.approx([0.82, 0.42, 0.10])
    med, _ = g.implied_median(lad["strikes"], lad["survival"])
    assert med == pytest.approx(3.00 + (0.82 - 0.5) * 0.10 / (0.82 - 0.42))


def test_bucket_ladder_normalizes():
    mk = [{"ticker": "a", "strike_type": "between", "floor_strike": 3.0, "cap_strike": 3.05, "yes_bid": 0.18, "yes_ask": 0.22},
          {"ticker": "b", "strike_type": "between", "floor_strike": 3.05, "cap_strike": 3.10, "yes_bid": 0.58, "yes_ask": 0.62},
          {"ticker": "c", "strike_type": "between", "floor_strike": 3.10, "cap_strike": 3.15, "yes_bid": 0.18, "yes_ask": 0.22}]
    lad = g.ladder_points(mk)
    assert lad["kind"] == "bucket"
    assert lad["survival"][0] == pytest.approx(0.8)
    med, _ = g.implied_median(lad["strikes"], lad["survival"])
    assert 3.05 < med < 3.10


def _hist(start, values):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_model_needs_enough_history():
    out = g.model_distribution(_hist(date(2026, 10, 1), [3.0, 3.01, 3.02]), date(2026, 10, 5), CFG)
    assert out["ok"] is False and "need" in out["why"]


def test_model_drift_and_sigma():
    vals = [3.000 + 0.01 * i for i in range(12)]
    h = _hist(date(2026, 10, 1), vals)
    res = date(2026, 10, 13)  # 1 day after the last reading (Oct 12)
    out = g.model_distribution(h, res, CFG)
    assert out["ok"]
    assert out["horizon"] == 1
    assert out["mu"] == pytest.approx(vals[-1] + 0.7 * 0.01)
    assert out["sigma"] == pytest.approx(CFG["sigma_floor"])  # constant changes -> stdev 0 -> floor
    # drift 0.007 over one day, sigma 0.004, strictly-above continuity half tick 0.00005:
    # P(above today's value) = Phi((0.007 - 0.00005) / 0.004)
    p = g.model_prob({"strike_type": "greater", "floor_strike": vals[-1]}, out)
    assert p == pytest.approx(g.normal_cdf((0.007 - 0.00005) / 0.004), abs=1e-6)
    # >= includes the strike itself, so it is slightly more likely than strictly >
    assert g.model_prob({"strike_type": "greater_or_equal", "floor_strike": vals[-1]}, out) > p


def test_sigma_comes_from_forecast_errors():
    vals = [3.0]
    for c in [0.01, -0.01] * 8:
        vals.append(vals[-1] + c)
    out = g.model_distribution(_hist(date(2026, 10, 1), vals), date(2026, 10, 1) + timedelta(days=len(vals)), CFG)
    errs = g.forecast_errors([0.01, -0.01] * 8, CFG["drift_weight"])
    rms = (sum(e * e for e in errs[-CFG["sigma_lookback_days"]:]) / len(errs[-CFG["sigma_lookback_days"]:])) ** 0.5
    assert out["sd_daily"] == pytest.approx(rms)
    assert out["sd_daily"] > CFG["sigma_floor"]


def test_blend_clamps_and_shrinks_toward_kalshi():
    assert g.blend_fair(1.0, 0.70, 0.5) == pytest.approx(0.5 * 0.99 + 0.5 * 0.70)
    assert g.blend_fair(0.0, None, 0.5) == pytest.approx(0.01)
    assert g.blend_fair(None, 0.5, 0.5) is None
    probs = g.market_yes_probs([_thr(3.10, 0.40, 0.44), _thr(3.00, 0.80, 0.84)])
    assert probs == {"G-T3.1": pytest.approx(0.42), "G-T3.0": pytest.approx(0.82)}


def test_model_known_value_is_deterministic():
    h = _hist(date(2026, 10, 1), [3.0 + 0.001 * i for i in range(10)])
    out = g.model_distribution(h, date(2026, 10, 10), CFG)
    assert out["known"] == pytest.approx(3.009)
    assert g.model_prob({"strike_type": "greater", "floor_strike": 3.0}, out) == 1.0
    assert g.model_prob({"strike_type": "greater", "floor_strike": 3.01}, out) == 0.0


def test_trends_and_deceleration_flag():
    vals = [3.00, 3.01, 3.02, 3.03, 3.04, 3.045]  # rise slowing on the last day
    tr = g.trends(_hist(date(2026, 10, 1), vals))
    assert tr["chg_1d"] == pytest.approx(0.005)
    assert tr["chg_3d"] == pytest.approx(0.025)
    assert tr["decelerating"] is True


def test_deceleration_trap_blocks_bets_that_need_a_drop():
    tr = {"chg_3d": 0.025, "decelerating": True}
    below_now = {"strike_type": "greater", "floor_strike": 3.03}
    msg = g.deceleration_trap(below_now, "no", 3.045, tr)  # NO on 'above 3.03' needs a fall from 3.045
    assert msg and "DECELERATION TRAP" in msg and "slowing rise is not a fall" in msg
    assert g.deceleration_trap(below_now, "yes", 3.045, tr) is None
    # NO on 'above 3.06' wins at <= 3.06, which includes 'no change': not a drop-only bet
    assert g.deceleration_trap({"strike_type": "greater", "floor_strike": 3.06}, "no", 3.045, tr) is None
    # mirror rule on a falling trend
    fall = {"chg_3d": -0.02}
    assert "REVERSAL TRAP" in g.deceleration_trap({"strike_type": "greater", "floor_strike": 3.10}, "yes", 3.05, fall)


def test_calibration_stats():
    recs = [{"implied_median": 3.00, "actual": 3.005}, {"implied_median": 3.10, "actual": 3.09},
            {"implied_median": 3.20, "actual": 3.23}, {"implied_median": 3.30, "actual": 3.30},
            {"implied_median": None, "actual": 3.1}]
    s = g.calibration_stats(recs)
    assert s["n"] == 4
    assert s["bias"] == pytest.approx((0.005 - 0.01 + 0.03 + 0.0) / 4, abs=1e-4)
    assert s["mae"] == pytest.approx((0.005 + 0.01 + 0.03 + 0.0) / 4, abs=1e-4)
    assert s["pct_above"] == pytest.approx(100 * (2 + 0.5) / 4)
    assert s["within_1c"] == pytest.approx(75.0)
    assert g.calibration_stats([])["n"] == 0


def test_outcome_is_strictly_above():
    m = {"strike_type": "greater", "floor_strike": 4.27}
    assert g.outcome_yes(m, 4.2701) == 1
    assert g.outcome_yes(m, 4.27) == 0  # an exact match resolves NO
    assert g.outcome_yes({"strike_type": "between", "floor_strike": 4.2, "cap_strike": 4.3}, 4.3) == 1


def test_blend_scores_and_guard():
    # Kalshi perfect, model noisy: Kalshi alone must win and the model must not be trusted.
    recs = [(0.9, 1.0, 1, f"E{i}") for i in range(12)] + [(0.2, 0.0, 0, f"F{i}") for i in range(12)]
    s = g.blend_scores(recs, 0.5, 10)
    assert s["market_only"] == 0.0 and s["best_weight"] == 0.0
    assert s["adds_value"] is False and "NOT beaten" in s["verdict"]
    # Model sharper than Kalshi on enough days: the blend adds value.
    recs = [(0.99, 0.7, 1, f"E{i}") for i in range(12)] + [(0.01, 0.3, 0, f"F{i}") for i in range(12)]
    s = g.blend_scores(recs, 0.5, 10)
    assert s["adds_value"] is True and s["current"] < s["market_only"]
    # Too few days: never trusted, whatever the score.
    assert g.blend_scores(recs[:4], 0.5, 10)["adds_value"] is False
    assert g.blend_scores([], 0.5, 10)["adds_value"] is False


def test_history_known_at_has_no_look_ahead():
    from datetime import datetime, timezone

    from app.services.gas_service import history_known_at
    hist = [(date(2026, 10, d), 4.2 + d / 1000) for d in range(1, 25)]
    # 6am ET Oct 20: AAA has not published Oct 20 yet
    early = int(datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc).timestamp())
    assert max(d for d, _ in history_known_at(hist, early, date(2026, 10, 21))) == date(2026, 10, 19)
    # noon ET Oct 20: Oct 20 is out; the resolution date (Oct 21) never is
    noon = int(datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc).timestamp())
    assert max(d for d, _ in history_known_at(hist, noon, date(2026, 10, 21))) == date(2026, 10, 20)
    assert max(d for d, _ in history_known_at(hist, noon, date(2026, 10, 20))) == date(2026, 10, 19)
