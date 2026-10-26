"""Word-timed transcripts: free YouTube captions first, local Whisper as the fallback."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import ytdlp
from .config import Config
from .util import log

MARKER = re.compile(r"^\[[^\]]+\]$")      # [Music], [Laughter], [Applause]


@dataclass
class Word:
    w: str
    s: float
    e: float


@dataclass
class Transcript:
    source: str
    words: list[Word]
    markers: list[tuple[float, str]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.words[-1].e if self.words else 0.0

    def words_between(self, start: float, end: float) -> list[Word]:
        return [w for w in self.words if start <= (w.s + w.e) / 2 <= end]

    def text_between(self, start: float, end: float) -> str:
        return " ".join(w.w for w in self.words_between(start, end))

    def lines(self, max_words: int = 14, pause: float = 0.7) -> list[tuple[float, float, str]]:
        """Group words into short timestamped lines for prompts and heuristics."""
        out: list[tuple[float, float, str]] = []
        cur: list[Word] = []
        for w in self.words:
            if cur and (len(cur) >= max_words or w.s - cur[-1].e > pause or cur[-1].w[-1:] in ".?!"):
                out.append((cur[0].s, cur[-1].e, " ".join(x.w for x in cur)))
                cur = []
            cur.append(w)
        if cur:
            out.append((cur[0].s, cur[-1].e, " ".join(x.w for x in cur)))
        return out

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"source": self.source, "words": [asdict(w) for w in self.words], "markers": self.markers}
        path.write_text(json.dumps(data, separators=(",", ":")))

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        data = json.loads(path.read_text())
        return cls(
            source=data["source"],
            words=[Word(**w) for w in data["words"]],
            markers=[tuple(m) for m in data.get("markers", [])],
        )


def parse_json3(data: dict, source: str = "youtube-auto") -> Transcript:
    """Parse YouTube's json3 caption format into word timings.

    Auto captions give each word its own seg with an offset. Uploaded captions give one
    seg per cue; those words get spread across the cue in proportion to their length.
    """
    pieces: list[tuple[float, float, str]] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs or ev.get("aAppend"):
            continue
        ev_start = ev.get("tStartMs", 0) / 1000
        ev_end = ev_start + ev.get("dDurationMs", 0) / 1000
        starts = [ev_start + s.get("tOffsetMs", 0) / 1000 for s in segs]
        for i, seg in enumerate(segs):
            text = (seg.get("utf8") or "").replace("\n", " ").strip()
            if not text:
                continue
            seg_end = starts[i + 1] if i + 1 < len(segs) else ev_end
            pieces.append((starts[i], max(seg_end, starts[i] + 0.05), text))

    pieces.sort(key=lambda p: p[0])
    words: list[Word] = []
    markers: list[tuple[float, str]] = []
    for start, end, text in pieces:
        tokens = text.split()
        if len(tokens) == 1 and MARKER.match(tokens[0]):
            markers.append((start, tokens[0]))
            continue
        tokens = [t for t in tokens if not MARKER.match(t)]
        total = sum(len(t) for t in tokens) or 1
        t = start
        for tok in tokens:
            span = (end - start) * len(tok) / total
            words.append(Word(tok, round(t, 3), round(t + span, 3)))
            t += span

    # Rolling auto captions overlap; a word ends no later than the next one starts.
    for a, b in zip(words, words[1:]):
        a.e = round(max(a.s + 0.05, min(a.e, b.s, a.s + 1.5)), 3)
    if words:
        words[-1].e = min(words[-1].e, words[-1].s + 1.5)
    return Transcript(source=source, words=words, markers=markers)


def _whisper(audio: Path, cfg: Config) -> Transcript:
    model = cfg.transcribe.whisper_model
    try:
        import mlx_whisper  # Apple Silicon, native installs only

        repo = model if "/" in model else f"mlx-community/whisper-{model}-mlx"
        result = mlx_whisper.transcribe(
            str(audio), path_or_hf_repo=repo, word_timestamps=True, language=cfg.transcribe.language
        )
        words = [
            Word(w["word"].strip(), float(w["start"]), float(w["end"]))
            for seg in result.get("segments", [])
            for w in seg.get("words", [])
            if w["word"].strip()
        ]
        return Transcript(source="mlx-whisper", words=words)
    except ImportError:
        pass
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "No YouTube captions and no local Whisper installed. "
            "Install one: pip install 'clipper[whisper]' (or [mlx] on Apple Silicon)."
        ) from e
    wm = WhisperModel(model, device="auto", compute_type="int8")
    segments, _ = wm.transcribe(
        str(audio), language=cfg.transcribe.language, word_timestamps=True, vad_filter=True
    )
    words = [
        Word(w.word.strip(), float(w.start), float(w.end))
        for seg in segments
        for w in (seg.words or [])
        if w.word.strip()
    ]
    return Transcript(source="faster-whisper", words=words)


def get_transcript(video_id: str, cfg: Config) -> Transcript:
    """Return a cached transcript or build one. Captions are free; Whisper is local."""
    cache = cfg.dir("transcripts") / f"{video_id}.json"
    if cache.exists():
        return Transcript.load(cache)

    work = cfg.dir("work") / video_id
    transcript: Transcript | None = None
    if cfg.transcribe.engine in ("auto", "youtube"):
        try:
            got = ytdlp.fetch_captions(video_id, work, cfg.transcribe.language)
            if got:
                path, kind = got
                transcript = parse_json3(json.loads(path.read_text()), source=kind)
        except Exception as e:  # captions are best-effort; Whisper is the fallback
            log.warning("captions failed for %s: %s", video_id, e)
        if transcript is None and cfg.transcribe.engine == "youtube":
            raise RuntimeError(f"no YouTube captions for {video_id}")

    if transcript is None or len(transcript.words) < 50:
        audio = ytdlp.download_audio(video_id, work)
        transcript = _whisper(audio, cfg)
        audio.unlink(missing_ok=True)

    transcript.save(cache)
    return transcript
