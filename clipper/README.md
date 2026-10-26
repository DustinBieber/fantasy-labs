# Clipper

Turns long-form fantasy football episodes into subtitled vertical clips and posts them to the creator's own accounts.
Setup, accounts and daily use are covered in [`../sandbox/README.md`](../sandbox/README.md). This file is for working on the code.

## Layout

| Module | Job |
|---|---|
| `config.py` | `config.yaml` (settings, creators, shows) + `.env` (secrets) → typed config. AI tiers live here. |
| `db.py` | SQLite schema and helpers. One connection per thread, WAL mode. |
| `ytdlp.py` | yt-dlp wrapper: channel tabs, search, captions, downloads. |
| `pipeline.py` | Sync uploads → transcript → pick → download → render. Show routing by title. |
| `transcripts.py` | YouTube json3 captions → word timings; Whisper (MLX or faster-whisper) fallback. |
| `highlights/` | `heuristic.py` (free), `claude.py` (Haiku/Sonnet/Opus with structured outputs), `postprocess.py` (clamp, snap to words, de-overlap). |
| `render/` | ASS karaoke captions + hook + CTA (`subtitles.py`); ffmpeg 9:16 blur/crop graph, multi-segment montages (`__init__.py`). |
| `publish/` | Encrypted tokens, slot scheduling, review gate, YouTube/TikTok/Instagram uploaders. |
| `discovery.py`, `socials.py` | Find channels with long-form but no Shorts/TikTok/IG; score; draft pitches. |
| `worker.py` | Work-hours loop. `web/` is the FastAPI dashboard + public creator connect pages. |

Clip lifecycle: `pending` → `ready` (rendered) → `approved` / `rejected`. An approved clip becomes one `posts` row per
connected platform, and `publish_due` uploads each one when its slot arrives. Only `status: client` creators ever get posts rows.

## Develop

```bash
cd clipper
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest                         # needs ffmpeg with libass for the render tests
CLIPPER_DATA=/tmp/clipper-dev clipper init && CLIPPER_DATA=/tmp/clipper-dev clipper serve --no-worker
```

The tests fake every network call (YouTube, Claude, posting APIs), so they run offline. They render real clips from a
synthetic ffmpeg test video.
