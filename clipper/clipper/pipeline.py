"""Creator pipeline: sync uploads -> transcript -> pick -> download -> render -> review queue."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import ytdlp
from .config import Client, Config
from .db import DB, dumps, loads, now_iso
from .highlights import Pick, RetryLater, pick_clips
from .render import render_clip
from .transcripts import Transcript, get_transcript
from .util import log, video_url

MAX_ATTEMPTS = 3


def sync_client(client: Client, cfg: Config, db: DB, limit: int = 30) -> int:
    """Record new uploads for a creator. Returns how many were queued."""
    if client.status == "paused":
        return 0
    first_sync = db.scalar("SELECT COUNT(*) FROM videos WHERE client=?", (client.slug,)) == 0
    sources = [(client.youtube, None)] + [(s.youtube, s.slug) for s in client.shows if s.youtube]
    queued = 0
    for url, forced_show in sources:
        info = ytdlp.flat_tab(ytdlp.channel_url(url) + "/videos", limit=limit)
        quota = client.sample_videos if client.status == "prospect" else client.backfill
        eligible = 0
        for entry in info["entries"]:
            vid = entry.get("id")
            if not vid or db.scalar("SELECT 1 FROM videos WHERE id=?", (vid,)):
                continue
            if entry.get("live_status") in ("is_upcoming", "is_live"):
                continue                      # pick it up after the stream ends
            title = entry.get("title") or ""
            duration = float(entry.get("duration") or 0)
            show = forced_show or client.classify(title).slug
            status, note = "new", None
            if duration and duration < client.min_source_minutes * 60:
                status, note = "skipped", "shorter than min_source_minutes"
            else:
                eligible += 1
                if first_sync and eligible > quota:
                    status, note = "skipped", "older than the backfill window"
                elif client.status == "prospect" and _active_count(db, client.slug) >= client.sample_videos:
                    status, note = "skipped", "prospect sample limit reached"
            db.insert("videos", {
                "id": vid, "client": client.slug, "show": show, "title": title,
                "url": video_url(vid), "duration": duration or None, "status": status, "error": note,
                "created_at": now_iso(), "updated_at": now_iso(),
            }, or_ignore=True)
            if status == "new":
                queued += 1
                db.log("sync", f"{client.name}: queued [{show}] {title}")
    return queued


def _active_count(db: DB, client: str) -> int:
    return db.scalar("SELECT COUNT(*) FROM videos WHERE client=? AND status != 'skipped'", (client,)) or 0


def add_video(url: str, client: Client, cfg: Config, db: DB, show: str | None = None) -> str:
    info = ytdlp.video_info(url)
    vid = info["id"]
    show = show or client.classify(info.get("title") or "").slug
    db.upsert("videos", {
        "id": vid, "client": client.slug, "show": show, "title": info.get("title"),
        "url": video_url(vid), "duration": info.get("duration"), "status": "new", "error": None,
        "attempts": 0, "created_at": now_iso(), "updated_at": now_iso(),
    }, ["id"])
    return vid


def save_picks(video: dict, picks: list[Pick], db: DB) -> int:
    for p in picks:
        db.insert("clips", {
            "video_id": video["id"], "client": video["client"], "show": p.show or video["show"],
            "kind": p.kind, "segments": dumps([list(s) for s in p.segments]),
            "hook": p.hook, "title": p.title, "caption": p.caption, "hashtags": dumps(p.hashtags),
            "category": p.category, "score": p.score, "reason": p.reason, "picker": p.picker,
            "status": "pending", "created_at": now_iso(), "updated_at": now_iso(),
        })
    return len(picks)


def render_pending(video: dict, transcript: Transcript, cfg: Config, db: DB) -> int:
    pending = db.all("SELECT * FROM clips WHERE video_id=? AND status='pending'", (video["id"],))
    if not pending:
        return 0
    source = Path(video["source_path"]) if video["source_path"] else None
    if not source or not source.exists():
        source = ytdlp.download_video(video["id"], cfg.dir("videos"))
        db.update("videos", {"id": video["id"]}, source_path=str(source))
    client = cfg.client(video["client"])
    cta = cfg.render.cta.format(client=client.name)
    done = 0
    for clip in pending:
        out = cfg.dir("clips") / video["client"] / f"{video['id']}_{clip['id']}.mp4"
        try:
            render_clip(
                source, [tuple(s) for s in loads(clip["segments"])], transcript.words, out, cfg.render,
                hook=clip["hook"] or "", cta=cta, fonts_dir=cfg.data_dir / "fonts",
            )
            db.update("clips", {"id": clip["id"]}, status="ready", file_path=str(out), error=None)
            done += 1
        except Exception as e:
            log.exception("render failed for clip %s", clip["id"])
            db.update("clips", {"id": clip["id"]}, status="failed", error=str(e)[:1000])
    return done


def process_video(video_id: str, cfg: Config, db: DB) -> str:
    """Run one video through every stage it still needs. Returns its new status."""
    row = db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not row:
        raise KeyError(video_id)
    video = dict(row)
    client = cfg.client(video["client"])
    ref = f"{client.slug}/{video_id}"
    try:
        transcript = get_transcript(video_id, cfg)
        if video["status"] == "new":
            picks = pick_clips(video["title"] or "", video["show"], transcript, client, cfg, db, ref=ref)
            if not picks:
                db.update("videos", {"id": video_id}, status="skipped", error="no clip-worthy moments found",
                          transcript_source=transcript.source)
                return "skipped"
            save_picks(video, picks, db)
            db.update("videos", {"id": video_id}, status="picked", transcript_source=transcript.source)
            db.log("pick", f"{ref}: {len(picks)} clips picked ({picks[0].picker})")
            video = dict(db.one("SELECT * FROM videos WHERE id=?", (video_id,)))
        rendered = render_pending(video, transcript, cfg, db)
        db.update("videos", {"id": video_id}, status="done", error=None)
        db.log("render", f"{ref}: {rendered} clips ready for review")
        return "done"
    except RetryLater as e:
        db.update("videos", {"id": video_id}, error=f"retrying: {e}"[:1000])
        return video["status"]
    except Exception as e:
        log.exception("processing %s failed", ref)
        attempts = (video["attempts"] or 0) + 1
        status = "failed" if attempts >= MAX_ATTEMPTS else video["status"]
        db.update("videos", {"id": video_id}, attempts=attempts, status=status, error=str(e)[:1000])
        db.log("error", f"{ref}: {e}", "error")
        return status


def next_videos(db: DB, cfg: Config, limit: int) -> list[str]:
    active = [c.slug for c in cfg.clients if c.status != "paused"]
    if not active:
        return []
    marks = ",".join("?" for _ in active)
    rows = db.all(
        f"SELECT id FROM videos WHERE status IN ('new','picked') AND client IN ({marks}) "
        f"ORDER BY attempts, created_at LIMIT ?",
        (*active, limit),
    )
    return [r["id"] for r in rows]


def cleanup(cfg: Config, db: DB) -> int:
    """Delete downloaded episodes older than keep_source_days. Rendered clips stay."""
    cutoff = time.time() - cfg.render.keep_source_days * 86400
    removed = 0
    for path in cfg.dir("videos").glob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            busy = db.scalar(
                "SELECT 1 FROM videos WHERE source_path=? AND status IN ('new','picked')", (str(path),)
            )
            if not busy:
                path.unlink()
                db.execute("UPDATE videos SET source_path=NULL WHERE source_path=?", (str(path),))
                removed += 1
    for work in cfg.dir("work").glob("*"):
        if work.is_dir() and work.stat().st_mtime < cutoff:
            shutil.rmtree(work, ignore_errors=True)
    return removed
