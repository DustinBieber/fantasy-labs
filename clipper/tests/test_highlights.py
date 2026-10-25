import json
from types import SimpleNamespace

import anthropic
import pytest

from clipper.highlights import Pick, RetryLater, claude, heuristic, pick_clips
from clipper.highlights.postprocess import clean, snap


def test_heuristic_finds_the_hot_take_and_skips_the_sponsor(cfg, transcript):
    client = cfg.client("fantasy-footballers")
    picks = heuristic.pick("Week 8 Start/Sit", "main", transcript, client, cfg)
    singles = [p for p in picks if p.kind == "single"]
    assert singles, "expected at least one clip"
    best = max(singles, key=lambda p: p.score)
    assert best.start <= 300 + 30 and best.end >= 300, "best clip should cover the hot take at 5:00"
    assert all(not (p.start < 125 and p.end > 120) for p in singles), "sponsor read must not be clipped"


def test_clean_enforces_lengths_and_overlap(cfg, transcript):
    client = cfg.client("fantasy-footballers")
    picks = [
        Pick(segments=[(300, 340)], hook="a", title="t", caption="c", score=90),
        Pick(segments=[(305, 345)], hook="dup", title="t", caption="c", score=80),     # overlaps the first
        Pick(segments=[(400, 405)], hook="short", title="t", caption="c", score=70),   # too short
        Pick(segments=[(200, 400)], hook="long", title="t", caption="c", score=60),    # trimmed to max
        Pick(segments=[(100, 106), (300, 306), (450, 455)], hook="m", title="t", caption="c", kind="montage"),
    ]
    out = clean(picks, transcript, client, cfg)
    singles = [p for p in out if p.kind == "single"]
    assert [p.hook for p in singles] == ["long", "a"]   # sorted by time; "long" trimmed to 75s, "dup"/"short" dropped
    assert all(cfg.clips.min_seconds <= p.length <= cfg.clips.max_seconds + 0.01 for p in singles)
    montage = [p for p in out if p.kind == "montage"]
    assert len(montage) == 1 and len(montage[0].segments) == 3


def test_snap_lands_on_word_boundaries(transcript):
    s, e = snap(300.05, 320.0, transcript.words, max_len=75)
    starts = {round(w.s - 0.12, 3) for w in transcript.words}
    assert round(s, 3) in starts
    assert e - s <= 75


class FakeMessages:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


def fake_anthropic(monkeypatch, messages):
    beta = SimpleNamespace(messages=messages)
    monkeypatch.setattr(claude.anthropic, "Anthropic", lambda **kw: SimpleNamespace(messages=messages, beta=beta))


def claude_response(payload, stop="end_turn", model="claude-haiku-5-5"):
    return SimpleNamespace(
        stop_reason=stop, model=model,
        usage=SimpleNamespace(input_tokens=20_000, output_tokens=1_500),
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(payload))],
    )


PAYLOAD = {
    "clips": [{
        "start": 300.0, "end": 340.0, "hook": "Bijan is a league winner", "title": "Bijan Robinson: League Winner?",
        "caption": "The case for Bijan.", "hashtags": ["#BijanRobinson", "falcons"], "score": 88,
        "category": "hot_take", "show": "main", "reason": "bold claim with a reason",
    }],
    "montage": {"segments": [{"start": 300, "end": 306}, {"start": 420, "end": 427}],
                "hook": "Week 8 in 30 seconds", "title": "Week 8 teaser", "caption": "Full ep on YouTube", "hashtags": []},
}


def test_claude_picker_budget_tier(cfg, db, transcript, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    msgs = FakeMessages(claude_response(PAYLOAD))
    fake_anthropic(monkeypatch, msgs)
    picks = pick_clips("Week 8", "main", transcript, cfg.client("fantasy-footballers"), cfg, db, ref="t")
    call = msgs.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in call and "temperature" not in call       # Haiku 5.5 has no server-side fallback
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert [p.kind for p in picks] == ["single", "montage"]
    assert picks[0].hashtags == ["#BijanRobinson", "#falcons"]
    cost = db.scalar("SELECT cost_usd FROM ai_usage")
    assert cost == pytest.approx((20_000 * 0.10 + 1_500 * 0.50) / 1e6)


def test_claude_premium_uses_server_fallbacks(cfg, db, transcript, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg.ai.tier = "premium"
    msgs = FakeMessages(claude_response(PAYLOAD, model="claude-opus-5-5"))
    fake_anthropic(monkeypatch, msgs)
    claude.pick("Week 8", "main", transcript, cfg.client("fantasy-footballers"), cfg, db, ref="t")
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]


def test_refusal_falls_back_to_free_heuristics(cfg, db, transcript, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    fake_anthropic(monkeypatch, FakeMessages(claude_response({}, stop="refusal")))
    picks = pick_clips("Week 8", "main", transcript, cfg.client("fantasy-footballers"), cfg, db, ref="t")
    assert picks and all(p.picker == "heuristic" for p in picks)


def test_rate_limit_means_retry_later(cfg, db, transcript, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    err = anthropic.APIConnectionError(request=SimpleNamespace(method="POST", url="https://api.anthropic.com"))
    fake_anthropic(monkeypatch, FakeMessages(exc=err))
    with pytest.raises(RetryLater):
        pick_clips("Week 8", "main", transcript, cfg.client("fantasy-footballers"), cfg, db, ref="t")


def test_no_key_or_budget_spent_uses_heuristics(cfg, db, monkeypatch):
    assert claude.unavailable_reason(cfg, db) == "no ANTHROPIC_API_KEY set"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert claude.unavailable_reason(cfg, db) is None
    from clipper.db import now_iso
    db.insert("ai_usage", {"ts": now_iso(), "model": "x", "purpose": "pick", "cost_usd": 99.0,
                           "input_tokens": 0, "output_tokens": 0, "ref": ""})
    assert "budget" in claude.unavailable_reason(cfg, db)


def test_haiku_long_prompt_pricing():
    assert claude.cost_usd("claude-haiku-5-5", 150_000, 0) == pytest.approx(150_000 * 0.50 / 1e6)
