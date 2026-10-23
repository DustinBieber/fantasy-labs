"""Named jobs (run on a schedule or on demand) and the background scheduler.

Interval: sync every SYNC_INTERVAL_MINUTES, then publish the calendar and the Sheets report.
Daily (business timezone): Muse export at MUSE_EXPORT_TIME, digest at DIGEST_TIME.
Every tick (~20 s): answer Telegram messages.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .. import db
from ..config import Settings, get_settings
from ..connectors import get_connector
from ..timeutil import iso, parse_iso, to_local, utcnow
from . import calendar, chatops, inventory, kv, muse, notify, sync

log = logging.getLogger(__name__)


def send_digest(session: Session, s: Settings, env=None) -> dict:
    text = notify.digest_text(session)
    from ..settings_store import effective_env

    channels = [c.strip() for c in ((env or effective_env()).get("DIGEST_CHANNELS") or "google_chat,telegram,gmail").split(",")]
    sent = [f"{n.channel}:{n.status}" for c in channels if (n := notify.deliver(session, s, c, text, env=env))]
    return {"sent": sent}


def publish_calendar(session: Session, s: Settings, env=None) -> dict:
    events = calendar.build_events(session, utcnow())
    conn = get_connector("google_calendar", env)
    mode = conn.mode(s.demo_mode)
    if mode != "live":
        return {"mode": mode, "would_publish": len(events)}
    previous = set(json.loads(kv.get("calendar_published_ids", "[]", session=session)))
    result = conn.push_events(events, previous)
    kv.set("calendar_published_ids", json.dumps(sorted(e["id"] for e in events)), session=session)
    return {"mode": mode, **result}


def report_tables(session: Session, s: Settings) -> dict[str, list[list]]:
    from sqlalchemy import select

    from ..models import Alert
    from .orders import list_view

    now = utcnow()
    m = inventory.stock_matrix(session)
    codes = [l["code"] for l in m["locations"]]
    st = inventory.sell_through(session, s.sell_through_days, now)
    lots = inventory.lots(session, now, s.sell_through_days, s.lot_policy)
    alerts = list(session.scalars(select(Alert).where(Alert.status.in_(("open", "acked")))))
    stamp = f"Updated {to_local(now, s.timezone):%Y-%m-%d %H:%M} ({s.timezone})"
    return {
        "Watchlist": [[stamp], ["Order", "Customer", "Status", "Type", "Needed by", "Flags", "Notes"]] + [
            [o["number"], o["customer"], o["status"], o["order_type"], (o["required_by"] or "")[:16],
             "; ".join(o["note_flags"]), (o["notes"] or "").replace("\n", " ")[:300]]
            for o in list_view(session, now, "watch")],
        "Stock": [[stamp], ["SKU", "Item", *codes, "Total", "Available"]] + [
            [r["sku"], r["name"], *[r["by_location"].get(c, {}).get("on_hand", "") for c in codes], r["on_hand"],
             r["available"]] for r in m["rows"]],
        "SellThrough": [[stamp], ["SKU", "Item", f"Sold {s.sell_through_days}d", "On hand", "Sell-through %",
                                  "Per day", "Forecast/day", "Days of supply"]] + [
            [r["sku"], r["name"], r["sold"], r["on_hand"], r["sell_through_pct"], r["per_day"],
             r.get("forecast_per_day"), r["days_of_supply"]] for r in st],
        "Lots": [[stamp], ["SKU", "Location", "Lot", "Received", "Best-by", "Days left", "Qty", "At risk"]] + [
            [l["sku"], l["location"], l["lot_number"], l["received_on"], l["best_by"], l["days_left"], l["qty"],
             l["at_risk_units"]] for l in lots],
        "Alerts": [[stamp], ["Severity", "Title", "Detail", "Status"]] + [
            [a.severity, a.title, a.detail, a.status] for a in sorted(alerts, key=lambda a: a.severity)],
    }


def export_sheets(session: Session, s: Settings, env=None) -> dict:
    conn = get_connector("google_sheets", env)
    if conn.mode(s.demo_mode) != "live" or not conn.setting("GOOGLE_SHEETS_REPORT_ID"):
        return {"skipped": "set GOOGLE_SHEETS_REPORT_ID (and connect Google) to write report tabs"}
    return {"rows": conn.write_reports(report_tables(session, s))}


def muse_export(session: Session, s: Settings, env=None) -> dict:
    return muse.export_daily(session, s, env)


def telegram_poll(session: Session, s: Settings, env=None) -> dict:
    return chatops.poll_telegram(session, s, env)


JOBS = {"digest": send_digest, "muse_export": muse_export, "calendar": publish_calendar, "sheets_report": export_sheets,
        "telegram_poll": telegram_poll}


def run_job(session: Session, name: str, s: Settings, env=None) -> dict:
    result = JOBS[name](session, s, env)
    kv.set(f"job:{name}:last", json.dumps({"at": iso(utcnow()), "result": result}, default=str), session=session)
    session.commit()
    return result


class Scheduler:
    def __init__(self, env: dict[str, str] | None = None):
        self.env = env
        self._lock = threading.Lock()

    def tick(self, now: datetime | None = None) -> list[str]:
        if not self._lock.acquire(blocking=False):
            return []
        try:
            return self._tick(now or utcnow())
        finally:
            self._lock.release()

    def _tick(self, now: datetime) -> list[str]:
        s = get_settings()
        ran = []
        with db.session_scope() as session:
            if get_connector("telegram", self.env).is_configured():
                self._safe(session, "telegram_poll", s, ran)
            last = parse_iso(kv.get("scheduler:last_sync", session=session))
            if s.sync_interval_minutes > 0 and (last is None or now - last >= timedelta(minutes=s.sync_interval_minutes)):
                try:
                    sync.sync_all(session, s, self.env)
                    ran.append("sync")
                except Exception:
                    log.exception("Scheduled sync failed")
                    session.rollback()
                kv.set("scheduler:last_sync", iso(now), session=session)
                session.commit()
                for name in ("calendar", "sheets_report"):
                    self._safe(session, name, s, ran)
            local = to_local(now, s.timezone)
            for name, at in (("muse_export", s.muse_export_time), ("digest", s.digest_time)):
                key = f"scheduler:daily:{name}"
                if at and local.strftime("%H:%M") >= at and kv.get(key, session=session) != local.date().isoformat():
                    self._safe(session, name, s, ran)
                    kv.set(key, local.date().isoformat(), session=session)
                    session.commit()
        return ran

    def _safe(self, session: Session, name: str, s: Settings, ran: list[str]) -> None:
        try:
            run_job(session, name, s, self.env)
            ran.append(name)
        except Exception:
            log.exception("Job %s failed", name)
            session.rollback()


def run_forever(stop: threading.Event, every_seconds: int = 20, env=None) -> None:
    sched = Scheduler(env)
    while not stop.is_set():
        try:
            sched.tick()
        except Exception:
            log.exception("Scheduler tick failed")
        stop.wait(every_seconds)
