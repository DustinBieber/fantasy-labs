import pytest

from app.config import DEFAULT_SETTINGS
from app.engine.tiers import AGREE, DISAGREE, NONE, assign_tier, size_multiplier

T = DEFAULT_SETTINGS["tiers"]


@pytest.mark.parametrize("edge,ev,expert,tier", [
    (10.5, 25.0, NONE, "S"),        # both gates cleared
    (10.0, 25.0, NONE, "W"),        # edge must be ABOVE 10; G numbers but no expert
    (10.0, 25.0, AGREE, "G"),
    (12.0, 20.0, NONE, "W"),        # EV must be ABOVE 20 for S; falls to G check
    (12.0, 15.0, AGREE, "G"),
    (7.0, 12.0, AGREE, "G"),
    (7.0, 12.0, NONE, "W"),
    (6.0, 8.0, AGREE, "W"),         # EV below G gate
    (4.0, 9.0, NONE, "W"),          # 3 to 5 points
    (3.0, 5.0, NONE, "W"),
    (2.9, 5.0, NONE, "P"),
    (1.0, 2.0, AGREE, "W"),         # proven expert at a fair price
    (-1.0, -2.0, AGREE, "P"),       # not a fair price
    (15.0, 40.0, DISAGREE, "P"),    # expert disagreement always passes
])
def test_tier_table(edge, ev, expert, tier):
    assert assign_tier(edge, ev, expert, T)[0] == tier


def test_reasons_are_plain_english():
    tier, why = assign_tier(7.0, 12.0, NONE, T)
    assert tier == "W" and "no proven expert agreement" in why


def test_size_multipliers():
    assert size_multiplier("S", T) == 1.0
    assert size_multiplier("G", T) == 0.5
    assert size_multiplier("W", T) == 0.0
    assert size_multiplier("P", T) == 0.0
