"""Scheduled data pulls (APScheduler, in-process) and the per-source freshness the top bar shows."""
from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from .. import config, db
from ..settings_store import get_settings
from ..sources import odds_api
from ..timeutil import age_minutes, hst, now_ts
from . import edges, follow_service, gas_service, nfl_service, portfolio, scanner

log = logging.getLogger("kalshi_edge.jobs")


def _settle_all() -> Dict[str, Any]:
    out = portfolio.process_fills()
    out.update(portfolio.settle_positions())
    out.update(gas_service.mark_from_snapshots())
    out.update(follow_service.grade_picks())
    return out


# name -> (label, function, staleness key or None, enabled check)
JOBS: Dict[str, Dict[str, Any]] = {
    "kalshi_scan": {"label": "Kalshi full scan", "fn": scanner.full_scan, "stale": "kalshi_scan"},
    "kalshi_active": {"label": "Kalshi 1-min snapshots", "fn": scanner.active_snapshot, "stale": "kalshi_active"},
    "kalshi_series": {"label": "Kalshi series fees", "fn": scanner.refresh_series, "stale": None},
    "espn_nfl": {"label": "ESPN NFL", "fn": nfl_service.pull_espn, "stale": "espn_nfl"},
    "odds_api": {"label": "The Odds API", "fn": nfl_service.pull_odds_api, "stale": "odds_api",
                 "enabled": odds_api.enabled},
    "aaa": {"label": "AAA gas", "fn": gas_service.pull_aaa, "stale": "aaa"},
    "gas_backfill": {"label": "Gas settlements + calibration", "fn": gas_service.backfill, "stale": None},
    "follow_discover": {"label": "Grade bettors (Polymarket)", "fn": follow_service.discover, "stale": None},
    "follow_picks": {"label": "Followed bettors' picks", "fn": follow_service.refresh_picks, "stale": "follow_picks"},
    "settle": {"label": "Paper fills + settlement", "fn": _settle_all, "stale": None},
    "edge_record": {"label": "Edge history", "fn": edges.record, "stale": None},
}

_scheduler: Optional[BackgroundScheduler] = None


def run_job(name: str) -> Dict[str, Any]:
    job = JOBS[name]
    if job.get("enabled") and not job["enabled"]():
        return {"skipped": "disabled (no API key)"}
    start = time.time()
    db.upsert(db.job_runs, ["name"], {"name": name, "last_start_ts": now_ts()})
    try:
        result = job["fn"]() or {}
        rows = next((v for k, v in result.items() if isinstance(v, int)), None) if isinstance(result, dict) else None
        db.upsert(db.job_runs, ["name"], {"name": name, "last_ok_ts": now_ts(), "last_rows": rows,
                                          "last_duration_ms": int((time.time() - start) * 1000),
                                          "detail": db.dumps(result)})
        if name in ("kalshi_scan", "kalshi_active", "espn_nfl", "odds_api", "aaa", "follow_picks"):
            edges.compute(force=True)
        return result
    except Exception as e:  # noqa: BLE001 - every failure is recorded and shown, never swallowed
        msg = f"{type(e).__name__}: {e}"
        log.warning("job %s failed: %s", name, msg)
        log.debug(traceback.format_exc())
        db.upsert(db.job_runs, ["name"], {"name": name, "last_error": msg[:500], "last_error_ts": now_ts(),
                                          "last_duration_ms": int((time.time() - start) * 1000)})
        return {"error": msg}


def freshness() -> List[Dict[str, Any]]:
    settings = get_settings()
    stale_cfg = settings["staleness_minutes"]
    runs = {r["name"]: r for r in db.fetch_all(select(db.job_runs))}
    out = []
    for name, job in JOBS.items():
        r = runs.get(name, {})
        enabled = not job.get("enabled") or job["enabled"]()
        ok_ts = r.get("last_ok_ts")
        limit = stale_cfg.get(job["stale"]) if job.get("stale") else None
        if not enabled:
            state = "off"
        elif ok_ts is None:
            state = "error" if r.get("last_error_ts") else "never"
        elif limit is not None and age_minutes(ok_ts) > limit:
            state = "stale"
        elif r.get("last_error_ts") and r["last_error_ts"] > ok_ts:
            state = "error"
        else:
            state = "ok"
        out.append({"name": name, "label": job["label"], "state": state, "enabled": enabled,
                    "last_ok": hst(ok_ts), "last_ok_ts": ok_ts, "age_min": age_minutes(ok_ts),
                    "stale_after_min": limit, "last_error": r.get("last_error"), "last_error_at": hst(r.get("last_error_ts")),
                    "detail": r.get("detail"), "headline": job.get("stale") is not None})
    return out


def start() -> Optional[BackgroundScheduler]:
    global _scheduler
    if not config.SCHEDULER_ENABLED or _scheduler is not None:
        return _scheduler
    iv = get_settings()["intervals"]
    sch = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                            "misfire_grace_time": 120})
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    def add(name: str, **trigger):
        first = trigger.pop("first", 0)
        sch.add_job(run_job, "interval", args=[name], id=name, next_run_time=now + timedelta(seconds=first), **trigger)

    add("kalshi_scan", minutes=iv["full_scan_minutes"], first=2)
    add("kalshi_series", hours=12, first=5)
    add("kalshi_active", seconds=iv["active_snapshot_seconds"], first=20)
    add("espn_nfl", minutes=iv["espn_minutes"], first=8)
    add("odds_api", minutes=iv["odds_api_minutes"], first=12)
    add("aaa", minutes=iv["aaa_minutes"], first=10)
    add("gas_backfill", hours=24, first=60)
    add("follow_discover", hours=iv["follow_discover_hours"], first=120)
    add("follow_picks", minutes=iv["follow_picks_minutes"], first=240)
    add("settle", minutes=iv["settle_minutes"], first=90)
    add("edge_record", minutes=iv["edge_record_minutes"], first=150)
    sch.start()
    _scheduler = sch
    log.info("scheduler started with %d jobs", len(sch.get_jobs()))
    return sch


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
