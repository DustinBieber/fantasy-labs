import pytest

from app.engine.devig import american_to_implied, consensus, devig_two_way, implied_to_american


def test_american_to_implied():
    assert american_to_implied(-110) == pytest.approx(110 / 210)
    assert american_to_implied(150) == pytest.approx(0.4)
    assert american_to_implied(100) == pytest.approx(0.5)
    assert american_to_implied(-100) == pytest.approx(0.5)


@pytest.mark.parametrize("bad", [0, 50, -99])
def test_invalid_odds_raise(bad):
    with pytest.raises(ValueError):
        american_to_implied(bad)


def test_proportional_devig_symmetric_line():
    d = devig_two_way(-110, -110)
    assert d["home"] == pytest.approx(0.5)
    assert d["away"] == pytest.approx(0.5)
    assert d["overround"] == pytest.approx(220 / 210)


def test_proportional_devig_favorite():
    d = devig_two_way(-200, 170)
    ih, ia = 200 / 300, 100 / 270
    assert d["home"] == pytest.approx(ih / (ih + ia))
    assert d["away"] == pytest.approx(ia / (ih + ia))
    assert d["home"] + d["away"] == pytest.approx(1.0)
    assert d["home"] == pytest.approx(0.6429, abs=1e-4)
    assert any("vig" in s for s in d["steps"])


def test_consensus_and_roundtrip():
    assert consensus([0.60, 0.62, 0.70]) == pytest.approx(0.62)
    assert consensus([0.60, 0.70], "mean") == pytest.approx(0.65)
    assert implied_to_american(0.6) == -150
    assert implied_to_american(0.4) == 150
