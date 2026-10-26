from datetime import date

import pytest

from opshub.services.signals import find_deadline, keyword_hits, parse_date_value, read_order

FRI = date(2026, 10, 23)


@pytest.mark.parametrize("text,expected", [
    ("Need by Oct 25 for a birthday", date(2026, 10, 25)),
    ("RUSH - must arrive by Saturday", date(2026, 10, 24)),
    ("please deliver before 11/3", date(2026, 11, 3)),
    ("deadline: 2026-11-04", date(2026, 11, 4)),
    ("In-hands date: 3rd of November", date(2026, 11, 3)),
    ("need it by tomorrow", date(2026, 10, 24)),
    ("ship by Nov. 2nd, 2026", date(2026, 11, 2)),
    ("need by 10/20", date(2026, 10, 20)),          # recent past reads as overdue, not next year
    ("needed by Jan 5", date(2027, 1, 5)),           # no year: next occurrence
    ("deliver by 1/5/27", date(2027, 1, 5)),
])
def test_find_deadline(text, expected):
    assert find_deadline(text, FRI)[0] == expected


@pytest.mark.parametrize("text", ["handled by monitor team", "due to weather", "call before delivery", "", None])
def test_no_false_deadlines(text):
    assert find_deadline(text, FRI) is None


def test_earliest_deadline_wins():
    assert find_deadline("ship by Nov 10, but need by Nov 5 at the latest", FRI)[0] == date(2026, 11, 5)


def test_parse_date_value_formats():
    for v in ("2026-10-25", "10/25", "Oct 25", "2026-10-25T00:00:00Z"):
        assert parse_date_value(v, FRI) == date(2026, 10, 25)
    assert parse_date_value("ASAP", FRI) is None


def test_keyword_hits_are_word_bounded():
    assert keyword_hits("Please HOLD - don’t ship", ["hold", "don't", "old"]) == ["hold", "don't"]


def test_read_order_sources_and_type():
    sig = read_order(notes="gift wrap please", tags=["need-by:2026-10-30", "Rush"],
                     attributes={"Delivery Date": "2026-10-28"}, ref=FRI, keywords=["gift"],
                     deadline_keys=["delivery date"])
    assert sig.deadline == date(2026, 10, 28) and sig.deadline_source == "attribute"
    assert sig.order_type == "priority"
    assert sig.flags == ["gift"]
    assert read_order(notes=None, tags=["Subscription Recurring Order"], attributes={}, ref=FRI, keywords=[],
                      deadline_keys=[]).order_type == "subscription"
    assert read_order(notes=None, tags=[], attributes={}, ref=FRI, keywords=[], deadline_keys=[],
                      has_seasonal_items=True).order_type == "seasonal"


def test_dotenv_inline_comments(tmp_path, monkeypatch):
    from opshub.config import load_dotenv

    f = tmp_path / ".env"
    f.write_text('A=1   # one\nB=   # blank with comment\nC="x # not a comment"\n# D=4\nE=url#frag\n')
    for k in "ABCDE":
        monkeypatch.delenv(k, raising=False)
    load_dotenv(f)
    import os

    assert (os.environ["A"], os.environ["B"], os.environ["C"], os.environ["E"]) == ("1", "", "x # not a comment", "url#frag")
    assert "D" not in os.environ
