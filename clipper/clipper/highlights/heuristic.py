"""Free clip picker: fantasy-football keyword density, laughter, pace. No API calls."""
from __future__ import annotations

import re
from statistics import median

from ..config import Client, Config
from ..transcripts import Transcript
from ..util import truncate
from . import Pick

# phrase -> weight. Matched against lowercased, unpunctuated text.
LEXICON: dict[str, float] = {
    "hot take": 4, "league winner": 4, "league winners": 4, "bold prediction": 4,
    "i'm telling you": 3, "trust me": 3, "mark my words": 4, "guarantee": 3,
    "do not draft": 3, "don't draft": 3, "must start": 3, "must add": 3, "must have": 2,
    "buy low": 3, "sell high": 3, "smash": 3, "fade": 2, "lock": 2, "bust": 3, "busts": 3,
    "sleeper": 3, "sleepers": 3, "breakout": 3, "overrated": 3, "underrated": 3,
    "start him": 2, "sit him": 2, "bench him": 2, "drop him": 3, "cut him": 3, "trade for": 2,
    "trade away": 2, "pick him up": 2, "waiver": 1, "top five": 2, "top 5": 2, "top 12": 2,
    "rb1": 2, "wr1": 2, "te1": 2, "qb1": 2, "overall": 1, "elite": 2, "panic": 2,
    "never": 1, "worst": 2, "best": 1, "insane": 2, "crazy": 2, "ridiculous": 2, "unbelievable": 2,
    "no way": 2, "come on": 2, "are you kidding": 3, "i disagree": 3, "you're wrong": 3,
    "disagree": 2, "mistake": 2, "regret": 2, "injury": 1, "injured": 1, "touchdowns": 1,
    "prediction": 2, "predict": 2, "winner": 1, "loser": 1, "volume": 1, "target share": 2,
    "snap share": 2, "red zone": 1, "ceiling": 1, "floor": 1, "value": 1, "steal": 2,
}
SPONSOR = [
    "promo code", "use code", "offer code", "brought to you by", "sponsored by", "our sponsor",
    "first deposit", "deposit match", "free trial", "percent off", "link in the description",
    "sign up today", "sign up now", "patreon", "merch", "like and subscribe", "hit subscribe",
    "ultimate draft kit", "download the app",
]
HOOK_OPENERS = ("here's", "i think", "the biggest", "my ", "if you", "don't", "do not", "stop",
                "this is", "nobody", "everyone", "the problem", "listen", "hot take", "what if")

_norm = re.compile(r"[^a-z0-9' ]+")


def _norm_text(text: str) -> str:
    return " " + _norm.sub(" ", text.lower()) + " "


def _keyword_score(text: str) -> float:
    t = _norm_text(text)
    return sum(w * t.count(f" {k} ") for k, w in LEXICON.items())


def _sponsor_hit(text: str) -> bool:
    t = _norm_text(text)
    return any(f" {k} " in t for k in SPONSOR)


def _hashtags(client: Client, show_slug: str) -> list[str]:
    return (client.show(show_slug).hashtags + client.hashtags)[:5]


def pick(title: str, show_slug: str, transcript: Transcript, client: Client, cfg: Config) -> list[Pick]:
    c = cfg.clips
    lines = transcript.lines()
    if not lines:
        return []
    lo, hi = c.skip_intro_seconds, transcript.duration - c.skip_outro_seconds
    lines = [ln for ln in lines if lo <= ln[0] and ln[1] <= hi] or lines
    paces = [len(t.split()) / max(0.5, e - s) for s, e, t in lines]
    base_pace = median(paces) or 2.5
    laughs = [t for t, m in transcript.markers if "laugh" in m.lower()]
    target = (c.min_seconds + c.max_seconds) / 2

    windows: list[tuple[float, int, int]] = []
    for i, (s0, _, first) in enumerate(lines):
        j, text = i, []
        while j < len(lines) and lines[j][1] - s0 < target:
            text.append(lines[j][2])
            j += 1
        if j >= len(lines) or lines[j - 1][1] - s0 < c.min_seconds:
            continue
        joined = " ".join(text)
        if _sponsor_hit(joined):
            continue
        e0 = lines[j - 1][1]
        nwords = len(joined.split())
        score = _keyword_score(joined) / max(1, nwords) * 100
        score += 4 * sum(1 for t in laughs if s0 <= t <= e0)
        score += 6 * max(0.0, nwords / (e0 - s0) / base_pace - 1)
        gap_before = s0 - lines[i - 1][1] if i else 1
        score += 3 if gap_before > 0.4 else 0
        score += 4 if first.lower().startswith(HOOK_OPENERS) else 0
        windows.append((score, i, j))

    windows.sort(reverse=True)
    chosen: list[tuple[float, int, int]] = []
    for w in windows:
        s, e = lines[w[1]][0], lines[w[2] - 1][1]
        if all(e <= lines[x[1]][0] or s >= lines[x[2] - 1][1] for x in chosen):
            chosen.append(w)
        if len(chosen) >= c.per_video:
            break

    tags = _hashtags(client, show_slug)
    picks: list[Pick] = []
    for score, i, j in chosen:
        seg = lines[i:j]
        best = max(seg, key=lambda ln: _keyword_score(ln[2]))
        hook = " ".join(best[2].split()[:7])
        body = " ".join(ln[2] for ln in seg)
        picks.append(Pick(
            segments=[(seg[0][0], seg[-1][1])],
            hook=hook,
            title=truncate(f"{hook} | {client.show(show_slug).name}", 95),
            caption=truncate(body, 140),
            hashtags=tags,
            score=min(95.0, 30 + score),
            category="hot_take" if _keyword_score(body) > 8 else "advice",
            reason="keyword density and pace",
        ))

    if c.montage and len(lines) > 8:
        ranked = sorted(lines, key=lambda ln: -_keyword_score(ln[2]))
        segs: list[tuple[float, float]] = []
        for s, e, t in ranked:
            if e - s < 2.5 or _sponsor_hit(t) or any(abs(s - x[0]) < 60 for x in segs):
                continue
            segs.append((s, min(e, s + 10)))
            if len(segs) == 4:
                break
        if len(segs) >= 2:
            picks.append(Pick(
                segments=sorted(segs),
                hook=truncate(title, 50),
                title=truncate(f"Best moments: {title}", 95),
                caption=truncate(f"The best moments from {title}.", 200),
                hashtags=tags,
                score=50,
                category="story",
                reason="top keyword moments across the episode",
                kind="montage",
            ))
    return picks
