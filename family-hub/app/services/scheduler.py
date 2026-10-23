"""Background loop: recurring chores, allowance, door checks, calendar sync and the daily digest."""
import asyncio
import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import Chore, ChoreInstance, Event, Family, HealthRecord, Homework, Integration, Member, Redemption, Transaction
from ..timeutil import local_now, utcnow
from . import chores, google, icloud, locks, presence
from .notify import raise_alert

log = logging.getLogger(__name__)
CALENDAR_SYNC_EVERY = timedelta(minutes=15)


def tick(db: Session, now: datetime | None = None) -> None:
    now = now or utcnow()
    try:
        backup(now)
    except Exception:
        log.exception("Backup failed")
    for family in db.query(Family).all():
        for step in (_chores, _allowance, _presence, _locks, _calendars, _digest):
            try:
                step(db, family, now)
            except Exception:
                db.rollback()
                log.exception("Scheduler step %s failed for family %s", step.__name__, family.id)


def _chores(db: Session, family: Family, now: datetime) -> None:
    today = local_now(family.timezone, now).date()
    chores.ensure_instances(db, family.id, today, today + timedelta(days=1))


def _allowance(db: Session, family: Family, now: datetime) -> None:
    """Credit each child's weekly allowance once per ISO week, on or after the family's payday."""
    local = local_now(family.timezone, now)
    payday = (family.settings or {}).get("allowance_weekday", 5)  # Saturday
    if local.weekday() < payday:
        return
    year, week, _ = local.isocalendar()
    for kid in db.query(Member).filter_by(family_id=family.id, role="child"):
        if kid.weekly_allowance_cents <= 0:
            continue
        key = f"allowance:{kid.id}:{year}-W{week:02d}"
        if db.query(Transaction).filter_by(dedupe_key=key).first():
            continue
        db.add(Transaction(family_id=family.id, member_id=kid.id, amount_cents=kid.weekly_allowance_cents,
                           category="allowance", description="Weekly allowance", date=local.date(), dedupe_key=key))
        db.commit()
        raise_alert(db, family, "finance", f"Allowance paid: ${kid.weekly_allowance_cents / 100:.2f}",
                    member_id=kid.id, audience=["child"])


def _presence(db: Session, family: Family, now: datetime) -> None:
    if (family.settings or {}).get("wifi_presence"):
        seen = presence.seen_macs()
        if seen is not None:  # a failed scan must not mark everyone as away
            presence.update(db, family, seen, now)


def _locks(db: Session, family: Family, now: datetime) -> None:
    try:
        locks.poll(db, family)
    except Exception as exc:
        log.warning("Lock poll failed for family %s: %s", family.id, exc)
    locks.check_stale(db, family, now)


def _calendars(db: Session, family: Family, now: datetime) -> None:
    for integ in db.query(Integration).filter(Integration.family_id == family.id,
                                              Integration.provider.in_(("google", "icloud"))):
        if integ.last_sync_at and now - integ.last_sync_at < CALENDAR_SYNC_EVERY:
            continue
        try:
            (google if integ.provider == "google" else icloud).sync_calendar(db, integ)
        except Exception as exc:
            db.rollback()
            integ.last_error, integ.last_sync_at = str(exc)[:500], now
            db.commit()


def _digest(db: Session, family: Family, now: datetime) -> None:
    """Once a day at digest_hour (local), summarize tomorrow and anything waiting on parents."""
    local = local_now(family.timezone, now)
    if local.hour < (family.settings or {}).get("digest_hour", 17):
        return
    if (family.settings or {}).get("last_digest") == local.date().isoformat():
        return
    family.settings = {**(family.settings or {}), "last_digest": local.date().isoformat()}
    db.commit()
    body = build_digest(db, family, local.date())
    if body:
        raise_alert(db, family, "digest", f"Tomorrow for the {family.name}", body, audience=["parent"])


def build_digest(db: Session, family: Family, today: date) -> str:
    tomorrow = today + timedelta(days=1)
    names = {m.id: m.name for m in db.query(Member).filter_by(family_id=family.id)}
    lines = []
    tz_offset = local_now(family.timezone).utcoffset() or timedelta()
    day_start = datetime.combine(tomorrow, datetime.min.time()) - tz_offset
    events = db.query(Event).filter(Event.family_id == family.id, Event.start >= day_start,
                                    Event.start < day_start + timedelta(days=1)).order_by(Event.start).all()
    for ev in events:
        when = "All day" if ev.all_day else f"{(ev.start + tz_offset):%-I:%M %p}"
        lines.append(f"📅 {when} – {ev.title}")
    for hw in db.query(Homework).filter(Homework.family_id == family.id, Homework.due_date == tomorrow,
                                        Homework.status != "done"):
        lines.append(f"📚 {names.get(hw.member_id, '?')}: {hw.subject} – {hw.title}")
    for rec in db.query(HealthRecord).filter(HealthRecord.family_id == family.id, HealthRecord.next_due.isnot(None),
                                             HealthRecord.next_due <= today + timedelta(days=7)):
        lines.append(f"🩺 {names.get(rec.member_id, '?')}: {rec.title} due {rec.next_due:%a %b %-d}")
    waiting = db.query(ChoreInstance).join(Chore).filter(Chore.family_id == family.id,
                                                         ChoreInstance.status == "submitted").count()
    if waiting:
        lines.append(f"✅ {waiting} chore(s) waiting for approval")
    pending = db.query(Redemption).filter_by(family_id=family.id, status="pending").count()
    if pending:
        lines.append(f"🎁 {pending} reward request(s) waiting")
    return "\n".join(lines)


def backup(now: datetime, keep: int = 14) -> Path | None:
    """Once a day, copy the SQLite database into FAMILY_HUB_BACKUP_DIR using SQLite's online backup."""
    url = settings.database_url
    if not settings.backup_dir or not url.startswith("sqlite:///") or url == "sqlite:///:memory:":
        return None
    folder = Path(settings.backup_dir).expanduser()
    target = folder / f"family_hub-{now:%Y-%m-%d}.db"
    if target.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(url.removeprefix("sqlite:///"))
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(folder.glob("family_hub-*.db"))[:-keep]:
        old.unlink()
    log.info("Backed up database to %s", target)
    return target


async def run_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(_tick_once)
        except Exception:
            log.exception("Scheduler tick failed")
        await asyncio.sleep(settings.scheduler_interval_s)


def _tick_once() -> None:
    with SessionLocal() as db:
        tick(db)
