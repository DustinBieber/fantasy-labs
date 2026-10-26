"""Claude clip picker. Tier picks the model: budget=Haiku 5.5, standard=Sonnet 5.5, premium=Opus 5.5."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import anthropic

from ..config import Client, Config
from ..db import DB, now_iso
from ..transcripts import Transcript
from ..util import fmt_ts, log
from . import CATEGORIES, Pick, RetryLater

# $ per million tokens: (input, output). Haiku 5.5 bills more above a 100K-token prompt.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
HAIKU_LONG_PROMPT = (0.50, 2.50)
# Models that support server-side refusal fallbacks (fallbacks="default").
SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}

SYSTEM = """You are the short-form editor for {client}, a fantasy football show. You turn full episodes into vertical clips for YouTube Shorts, TikTok and Instagram Reels that grow the show's following and send viewers to the full episode.

A great clip:
- Hooks in the first 3 seconds: a bold claim, a surprising stat, a question, or a disagreement.
- Delivers a hot take, a bold prediction, a start/sit or buy/sell call with a reason, a sleeper or bust, a heated debate, a funny moment, or a reaction to news.
- Stands alone: someone who never heard the episode gets it. No "like we said earlier".
- Starts at the beginning of a sentence and ends right after the payoff (the verdict, the answer, the punchline).

Skip sponsor reads, promo codes, ad breaks, housekeeping, and anything that needs visuals to make sense.

The transcript comes from automatic captions, so it has no punctuation and may misspell player names. Spell names correctly in the hook, title and caption, but take start and end times only from the transcript timestamps."""

PROMPT = """Episode: {title}
Show: {show_name}
Length: {length}
Show accounts a clip can go to: {shows}. Default: {show}.

Pick the {n} best clips, each {min_s:.0f}-{max_s:.0f} seconds long and not overlapping.
Also pick a montage: 3-5 very short moments (4-10 seconds each, {montage_s:.0f} seconds total at most) that together tease the full episode, in the order they should play.

For every clip:
- start, end: seconds, from the transcript timestamps
- hook: on-screen headline, at most 7 words, no hashtags
- title: YouTube Shorts title, at most 80 characters
- caption: 1-2 sentences for the post that make people want the full episode
- hashtags: 3-5 specific hashtags (players, teams, topics)
- score: 1-100, how well it should perform
- category, show, reason (one short sentence)

Transcript (each line starts at [seconds]):
{transcript}"""


class Unusable(Exception):
    """Claude can't be used for this request (refusal, bad request, no credentials)."""


def _schema(show_slugs: list[str]) -> dict:
    clip_props = {
        "start": {"type": "number"},
        "end": {"type": "number"},
        "hook": {"type": "string"},
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "integer"},
        "category": {"type": "string", "enum": CATEGORIES},
        "show": {"type": "string", "enum": show_slugs},
        "reason": {"type": "string"},
    }
    seg = {
        "type": "object",
        "properties": {"start": {"type": "number"}, "end": {"type": "number"}},
        "required": ["start", "end"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": clip_props,
                    "required": list(clip_props),
                    "additionalProperties": False,
                },
            },
            "montage": {
                "type": "object",
                "properties": {
                    "segments": {"type": "array", "items": seg},
                    "hook": {"type": "string"},
                    "title": {"type": "string"},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["segments", "hook", "title", "caption", "hashtags"],
                "additionalProperties": False,
            },
        },
        "required": ["clips", "montage"],
        "additionalProperties": False,
    }


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rate_in, rate_out = PRICES.get(model, PRICES["claude-opus-5-5"])
    if model == "claude-haiku-5-5" and input_tokens > 100_000:
        rate_in, rate_out = HAIKU_LONG_PROMPT
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


def month_spend(db: DB) -> float:
    start = datetime.now(timezone.utc).strftime("%Y-%m-01")
    return float(db.scalar("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_usage WHERE ts >= ?", (start,)) or 0)


def unavailable_reason(cfg: Config, db: DB) -> str | None:
    if cfg.ai.resolved() is None:
        return "ai.tier is free"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return "no ANTHROPIC_API_KEY set"
    spent = month_spend(db)
    if spent >= cfg.ai.monthly_budget_usd:
        return f"monthly AI budget reached (${spent:.2f} of ${cfg.ai.monthly_budget_usd:.2f})"
    return None


def _format_transcript(transcript: Transcript, cfg: Config) -> str:
    lo = cfg.clips.skip_intro_seconds
    hi = transcript.duration - cfg.clips.skip_outro_seconds
    return "\n".join(f"[{s:.1f}] {t}" for s, e, t in transcript.lines() if lo <= s and e <= hi)


def create(client: anthropic.Anthropic, model: str, **kwargs):
    if model in SERVER_FALLBACK_MODELS:
        # On a safety decline, the API retries on Anthropic's recommended fallback model.
        return client.beta.messages.create(
            model=model, betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    return client.messages.create(model=model, **kwargs)


def pick(title: str, show_slug: str, transcript: Transcript, client_cfg: Client, cfg: Config, db: DB, ref: str) -> list[Pick]:
    model, effort = cfg.ai.resolved()  # type: ignore[misc]
    shows = ", ".join(f"{s.slug} ({s.name})" for s in client_cfg.shows)
    prompt = PROMPT.format(
        title=title,
        show_name=client_cfg.show(show_slug).name,
        length=fmt_ts(transcript.duration),
        shows=shows,
        show=show_slug,
        n=cfg.clips.per_video + 2,      # extras give post-processing room to drop bad cuts
        min_s=cfg.clips.min_seconds,
        max_s=cfg.clips.max_seconds,
        montage_s=cfg.clips.montage_max_seconds,
        transcript=_format_transcript(transcript, cfg),
    )
    try:
        api = anthropic.Anthropic(max_retries=3, timeout=600)
        response = create(
            api,
            model,
            max_tokens=16000,
            system=SYSTEM.format(client=client_cfg.name),
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": _schema([s.slug for s in client_cfg.shows])},
            },
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.BadRequestError, anthropic.NotFoundError) as e:
        raise Unusable(f"{type(e).__name__}: {e}") from e
    except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
        raise RetryLater(str(e)) from e
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:
            raise RetryLater(str(e)) from e
        raise Unusable(f"{e.status_code}: {e}") from e
    except anthropic.AnthropicError as e:   # e.g. no credentials configured
        raise Unusable(str(e)) from e

    usage = response.usage
    served_by = getattr(response, "model", model) or model
    cost = cost_usd(served_by, usage.input_tokens, usage.output_tokens)
    db.insert("ai_usage", {
        "ts": now_iso(), "model": served_by, "purpose": "pick", "ref": ref,
        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "cost_usd": cost,
    })
    log.info("%s picked by %s: %d in / %d out tokens, $%.4f", ref, served_by, usage.input_tokens, usage.output_tokens, cost)

    if response.stop_reason == "refusal":
        raise Unusable("model declined the request")
    if response.stop_reason == "max_tokens":
        raise Unusable("response hit max_tokens")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise Unusable(f"unparseable JSON: {e}") from e

    picks = [
        Pick(
            segments=[(float(c["start"]), float(c["end"]))],
            hook=c["hook"], title=c["title"], caption=c["caption"], hashtags=c["hashtags"],
            score=c["score"], category=c["category"], show=c["show"], reason=c["reason"],
            picker=served_by,
        )
        for c in data.get("clips", [])
        if float(c["end"]) > float(c["start"])
    ]
    m = data.get("montage") or {}
    if m.get("segments"):
        picks.append(Pick(
            segments=[(float(s["start"]), float(s["end"])) for s in m["segments"]],
            hook=m["hook"], title=m["title"], caption=m["caption"], hashtags=m["hashtags"],
            score=60, category="story", reason="episode teaser", kind="montage", picker=served_by,
        ))
    return picks
