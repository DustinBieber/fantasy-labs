"""Make picks safe to render: clamp, snap to word boundaries, de-overlap, limit."""
from __future__ import annotations

from ..config import Client, Config
from ..transcripts import Transcript, Word
from ..util import truncate
from . import CATEGORIES, Pick

LEAD_IN = 0.12     # seconds before the first word
TAIL = 0.30        # seconds after the last word


def snap(start: float, end: float, words: list[Word], max_len: float | None = None) -> tuple[float, float] | None:
    inside = [w for w in words if w.e > start - 0.25 and w.s < end + 0.25]
    if not inside:
        return None
    if max_len:
        while len(inside) > 1 and inside[-1].e - inside[0].s > max_len - LEAD_IN - TAIL:
            inside.pop()
    return max(0.0, inside[0].s - LEAD_IN), inside[-1].e + TAIL


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return max(0.0, inter) / max(1e-6, min(a[1] - a[0], b[1] - b[0]))


def clean(picks: list[Pick], transcript: Transcript, client: Client, cfg: Config) -> list[Pick]:
    c = cfg.clips
    words = transcript.words
    show_slugs = {s.slug for s in client.shows}
    singles: list[Pick] = []
    montage: Pick | None = None

    for p in sorted(picks, key=lambda p: -p.score):
        p.hook = truncate(p.hook, 60)
        p.title = truncate(p.title, 95)
        p.caption = truncate(p.caption, 400)
        p.hashtags = [h if h.startswith("#") else f"#{h}" for h in (t.replace(" ", "") for t in p.hashtags) if h][:6]
        p.category = p.category if p.category in CATEGORIES else "advice"
        p.show = p.show if p.show in show_slugs else None
        p.score = max(1.0, min(100.0, float(p.score)))

        if p.kind == "montage":
            if montage or not c.montage:
                continue
            segs: list[tuple[float, float]] = []
            total = 0.0
            for s, e in p.segments:
                snapped = snap(s, min(e, s + 15), words, max_len=15)
                if not snapped or snapped[1] - snapped[0] < 2.5:
                    continue
                if any(overlap(snapped, x) > 0 for x in segs):
                    continue
                if total + snapped[1] - snapped[0] > c.montage_max_seconds:
                    break
                segs.append(snapped)
                total += snapped[1] - snapped[0]
            if len(segs) >= 2:
                p.segments = segs
                montage = p
            continue

        if len(singles) >= c.per_video:
            continue
        s, e = p.segments[0]
        s, e = max(0.0, s), min(transcript.duration, e)
        snapped = snap(s, e, words, max_len=c.max_seconds)
        if not snapped or snapped[1] - snapped[0] < c.min_seconds:
            continue
        if any(overlap(snapped, x.segments[0]) > 0.25 for x in singles):
            continue
        p.segments = [snapped]
        singles.append(p)

    singles.sort(key=lambda p: p.start)
    return singles + ([montage] if montage else [])
