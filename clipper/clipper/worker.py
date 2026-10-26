"""The background loop: heavy work inside your work-hours window, posting on schedule."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import discovery, pipeline, publish
from .config import Config
from .db import DB, now_iso, utcnow
from .util import log

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WORK_LOCK = threading.Lock()      # one tick (or dashboard "run now") at a time


def in_window(cfg: Config, now: datetime | None = None) -> bool:
    w = cfg.worker
    local = (now or utcnow()).astimezone(ZoneInfo(w.timezone))
    start, end = w.window
    if DAYS[local.weekday()] not in [d.lower()[:3] for d in w.active_days]:
        return False
    t = local.time()
    return start <= t < end if start <= end else (t >= start or t < end)


def _due(db: DB, key: str, every: timedelta) -> bool:
    last = db.get_kv(key)
    if last and utcnow() - datetime.fromisoformat(last) < every:
        return False
    db.set_kv(key, now_iso())
    return True


def tick(cfg: Config, db: DB, force: bool = False) -> dict:
    """One pass. Safe to call repeatedly; each job tracks its own last-run time."""
    stats: dict = {"window": in_window(cfg) or force}
    db.set_kv("worker.last_tick", now_iso())

    if cfg.worker.publish_anytime or stats["window"]:
        publish.schedule_approved(cfg, db)
        stats["posted"] = publish.publish_due(cfg, db)
    if _due(db, "job.refresh_tokens", timedelta(hours=12)):
        publish.refresh_all(cfg, db)

    if not stats["window"]:
        return stats

    if force or _due(db, "job.sync", timedelta(minutes=cfg.worker.sync_every_minutes)):
        for client in cfg.clients:
            try:
                stats.setdefault("queued", 0)
                stats["queued"] += pipeline.sync_client(client, cfg, db)
            except Exception as e:
                log.warning("sync %s failed: %s", client.slug, e)
                db.log("sync", f"{client.name}: sync failed: {e}", "error")

    deadline = time.time() + max(60, cfg.worker.tick_seconds * 3)
    for vid in pipeline.next_videos(db, cfg, cfg.worker.max_videos_per_tick):
        if time.time() > deadline:
            break
        stats.setdefault("processed", []).append((vid, pipeline.process_video(vid, cfg, db)))

    if _due(db, "job.discover", timedelta(hours=cfg.worker.discover_every_hours)):
        try:
            stats["discovered"] = discovery.discover(cfg, db)
        except Exception as e:
            log.warning("discovery failed: %s", e)
            db.log("discover", f"discovery failed: {e}", "error")

    if _due(db, "job.cleanup", timedelta(hours=6)):
        stats["cleaned"] = pipeline.cleanup(cfg, db)
    return stats


RUN_NOW_KEY = "worker.run_now"


def request_run(db: DB) -> None:
    """Ask the running service for an immediate pass (used by `clipper run-now`)."""
    db.set_kv(RUN_NOW_KEY, "1")


def run_forever(cfg: Config, db: DB, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    publish.recover_interrupted(db)
    db.log("worker", "worker started")
    log.info("worker started; active %s %s (%s)", cfg.worker.active_hours, ",".join(cfg.worker.active_days), cfg.worker.timezone)
    while not stop.is_set():
        force = db.get_kv(RUN_NOW_KEY) == "1"
        if force:
            db.set_kv(RUN_NOW_KEY, "0")
            db.log("worker", "run-now requested: starting a pass outside the normal schedule")
        try:
            with WORK_LOCK:
                stats = tick(cfg, db, force=force)
            if stats.get("processed") or stats.get("posted") or stats.get("queued"):
                log.info("tick: %s", stats)
        except Exception:
            log.exception("worker tick crashed")
        # Sleep until the next tick, but wake early for a run-now request.
        deadline = time.time() + cfg.worker.tick_seconds
        while not stop.is_set() and time.time() < deadline and db.get_kv(RUN_NOW_KEY) != "1":
            stop.wait(min(5, cfg.worker.tick_seconds))
