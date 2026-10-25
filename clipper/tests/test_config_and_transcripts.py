import json

import pytest

from clipper.config import AI
from clipper.transcripts import Transcript, parse_json3

from .conftest import FIXTURES


@pytest.mark.parametrize("title,show", [
    ("DFS Picks Week 8: DraftKings & FanDuel Lineups", "dfs"),
    ("NFL Week 8 Showdown Strategy", "dfs"),
    ("Best Bets + Player Props for Week 8", "betting"),
    ("Week 8 Parlays and Picks Against the Spread", "betting"),
    ("Dynasty Rookie Rankings: Buy & Sell", "dynasty"),
    ("Week 8 Start/Sit + Waiver Wire Pickups", "main"),
    ("Biggest Busts of the Season So Far", "main"),        # "Busts" must not trip the betting pattern
    ("The Best RBs Rest of Season", "main"),               # "Best" is not "Bet"
])
def test_fantasy_footballers_show_routing(cfg, title, show):
    assert cfg.client("fantasy-footballers").classify(title).slug == show


def test_example_config_seeds_fantasy_footballers(cfg):
    c = cfg.client("fantasy-footballers")
    assert c.status == "prospect" and not c.can_post
    assert [s.slug for s in c.shows] == ["dfs", "betting", "dynasty", "main"]
    assert c.show("unknown").slug == "main"


def test_ai_tiers_default_to_cheapest_paid_model():
    assert AI().resolved() == ("claude-haiku-5-5", "medium")
    assert AI(tier="free").resolved() is None
    assert AI(tier="premium").resolved() == ("claude-opus-5-5", "high")
    assert AI(tier="budget", model="claude-sonnet-5-5", effort="low").resolved() == ("claude-sonnet-5-5", "low")


def test_parse_auto_captions_word_timing():
    t = parse_json3(json.loads((FIXTURES / "auto.json3").read_text()))
    assert [w.w for w in t.words] == ["bijan", "robinson", "is", "a", "league", "winner", "trust", "me", "on", "this"]
    assert t.words[0].s == pytest.approx(1.0)
    assert t.words[1].s == pytest.approx(1.4)
    # words never overlap the next one
    assert all(a.e <= b.s + 1e-6 for a, b in zip(t.words, t.words[1:]))
    assert t.markers == [(3.2, "[Laughter]")]


def test_parse_manual_captions_spreads_words():
    t = parse_json3(json.loads((FIXTURES / "manual.json3").read_text()), source="youtube-manual")
    assert " ".join(w.w for w in t.words) == "Welcome back to the show. Today we talk sleepers and busts."
    assert t.words[0].s == 0 and t.words[4].e <= 2.0 + 1e-6
    assert t.words[5].s == pytest.approx(2.0)


def test_transcript_lines_and_roundtrip(tmp_path, transcript):
    lines = transcript.lines()
    assert all(len(text.split()) <= 14 for _, _, text in lines)
    path = tmp_path / "t.json"
    transcript.save(path)
    again = Transcript.load(path)
    assert len(again.words) == len(transcript.words) and again.markers == transcript.markers
