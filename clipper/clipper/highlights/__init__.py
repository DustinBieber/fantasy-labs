"""Pick clip-worthy moments from a transcript."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Client, Config
from ..db import DB
from ..transcripts import Transcript
from ..util import log

CATEGORIES = ["hot_take", "prediction", "advice", "debate", "funny", "news", "story", "stat"]


@dataclass
class Pick:
    segments: list[tuple[float, float]]
    hook: str
    title: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    score: float = 50
    category: str = "advice"
    show: str | None = None
    reason: str = ""
    kind: str = "single"            # single | montage
    picker: str = "heuristic"

    @property
    def start(self) -> float:
        return self.segments[0][0]

    @property
    def end(self) -> float:
        return self.segments[-1][1]

    @property
    def length(self) -> float:
        return sum(e - s for s, e in self.segments)


class RetryLater(Exception):
    """Transient failure (network, rate limit). Leave the video queued."""


def pick_clips(
    title: str, show_slug: str, transcript: Transcript, client: Client, cfg: Config, db: DB, ref: str = ""
) -> list[Pick]:
    from . import claude, heuristic
    from .postprocess import clean

    picks: list[Pick] | None = None
    reason = claude.unavailable_reason(cfg, db)
    if reason:
        log.info("picking %s with free heuristics (%s)", ref, reason)
    else:
        try:
            picks = claude.pick(title, show_slug, transcript, client, cfg, db, ref)
        except claude.Unusable as e:
            log.warning("Claude picker unusable for %s, using heuristics: %s", ref, e)
            db.log("ai", f"{ref}: fell back to heuristics ({e})", "warn")
    if picks is None:
        picks = heuristic.pick(title, show_slug, transcript, client, cfg)
    return clean(picks, transcript, client, cfg)
