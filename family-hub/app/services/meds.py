"""Medication reminders.

A medication health record with reminder times gets one MedDose per scheduled slot. At the slot the
person is reminded on their own phone (push, with a "Taken ✓" button on Android). If a kid's dose is
still not marked taken after `med_escalate_minutes` (default 30), parents are alerted. Doses not
handled within 6 hours count as missed.
"""
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import sign
from ..models import Family, HealthRecord, MedDose, Member
from ..timeutil import local_now, utcnow
from .notify import raise_alert

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
REMIND_WINDOW = timedelta(hours=2)  # don't fire old reminders after the hub was offline
MISSED_AFTER = timedelta(hours=6)


def parse_times(values: list[str]) -> list[str]:
    out = []
    for v in values:
        m = TIME_RE.match(v.strip())
        if not m:
            raise ValueError(f"'{v}' isn't a time like 08:00 or 20:30")
        out.append(f"{int(m[1]):02d}:{m[2]}")
    return sorted(set(out))


def slots_on(rec: HealthRecord, day: date, tz: str) -> list[datetime]:
    """UTC datetimes of a record's reminder slots on a local calendar day."""
    if rec.kind != "medication" or not rec.reminder_times:
        return []
    if rec.reminder_until and day > rec.reminder_until:
        return []
    if rec.date and day < rec.date:
        return []
    if rec.reminder_days and day.weekday() not in rec.reminder_days:
        return []
    zone = ZoneInfo(tz)
    out = []
    for t in rec.reminder_times:
        hh, mm = map(int, t.split(":"))
        local = datetime.combine(day, time(hh, mm), tzinfo=zone)
        out.append(local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None))
    return out


def _label(rec: HealthRecord) -> str:
    return rec.title + (f" ({rec.value} {rec.unit})".rstrip() if rec.value else "")


def taken_link(dose: MedDose) -> str:
    return f"{settings.base_url}/api/meds/doses/{dose.id}/taken-link?sig={sign(f'dose:{dose.id}')}"


def _local_time(dt_utc: datetime, tz: str) -> str:
    return local_now(tz, dt_utc).strftime("%-I:%M %p")


def tick(db: Session, family: Family, now: datetime | None = None) -> None:
    now = now or utcnow()
    today = local_now(family.timezone, now).date()
    escalate_after = timedelta(minutes=int((family.settings or {}).get("med_escalate_minutes", 30)))
    meds = db.query(HealthRecord).filter_by(family_id=family.id, kind="medication").all()
    for rec in meds:
        member = db.get(Member, rec.member_id)
        if member is None:
            continue
        for slot in slots_on(rec, today, family.timezone) + slots_on(rec, today - timedelta(days=1), family.timezone):
            if not (slot <= now < slot + REMIND_WINDOW):
                continue
            if db.query(MedDose).filter_by(record_id=rec.id, scheduled_for=slot).first():
                continue
            dose = MedDose(record_id=rec.id, member_id=member.id, scheduled_for=slot, status="due")
            db.add(dose)
            db.commit()
            raise_alert(db, family, "health", f"💊 Time for {_label(rec)}",
                        f"{member.name}, it's {_local_time(slot, family.timezone)}. Tap Taken when done.",
                        member_id=member.id, audience=["member"],
                        extra={"dose_id": dose.id, "taken_url": taken_link(dose), "url": "/#today"})

    for dose in db.query(MedDose).join(HealthRecord).filter(HealthRecord.family_id == family.id,
                                                            MedDose.status == "due"):
        if now - dose.scheduled_for >= MISSED_AFTER:
            dose.status = "missed"
            db.commit()
            continue
        if dose.escalated or now - dose.scheduled_for < escalate_after:
            continue
        dose.escalated = True
        db.commit()
        member, rec = db.get(Member, dose.member_id), dose.record
        when = _local_time(dose.scheduled_for, family.timezone)
        if member.role == "child":
            raise_alert(db, family, "health", f"{member.name} hasn't taken {_label(rec)}",
                        f"Due at {when}.", severity="warning", member_id=member.id, audience=["parent"])
        else:
            raise_alert(db, family, "health", f"💊 Reminder: {_label(rec)}", f"Due at {when}.",
                        member_id=member.id, audience=["member"],
                        extra={"dose_id": dose.id, "taken_url": taken_link(dose), "url": "/#today"})


def today_schedule(db: Session, family: Family, members: list[Member], now: datetime | None = None) -> list[dict]:
    """Every reminder slot today for these people, with its status (upcoming, due, taken, skipped, missed)."""
    now = now or utcnow()
    today = local_now(family.timezone, now).date()
    ids = {m.id: m for m in members}
    out = []
    for rec in db.query(HealthRecord).filter(HealthRecord.family_id == family.id, HealthRecord.kind == "medication",
                                             HealthRecord.member_id.in_(list(ids))):
        for slot in slots_on(rec, today, family.timezone):
            dose = db.query(MedDose).filter_by(record_id=rec.id, scheduled_for=slot).first()
            status = dose.status if dose else ("upcoming" if slot > now else "due")
            out.append({"record_id": rec.id, "member_id": rec.member_id, "title": _label(rec),
                        "slot": slot.isoformat() + "Z", "time": _local_time(slot, family.timezone),
                        "status": status, "dose_id": dose.id if dose else None})
    return sorted(out, key=lambda d: (d["slot"], d["title"]))


def mark(db: Session, family: Family, rec: HealthRecord, slot: datetime, status: str, by: Member | None) -> MedDose:
    if status not in ("taken", "skipped"):
        raise HTTPException(422, "status must be taken or skipped")
    valid = {s for d in (0, -1) for s in slots_on(rec, local_now(family.timezone).date() + timedelta(days=d),
                                                       family.timezone)}
    if slot not in valid:
        raise HTTPException(404, "No dose scheduled at that time")
    dose = db.query(MedDose).filter_by(record_id=rec.id, scheduled_for=slot).first()
    if dose is None:
        dose = MedDose(record_id=rec.id, member_id=rec.member_id, scheduled_for=slot)
        db.add(dose)
    was_escalated = dose.escalated and dose.status == "due"
    dose.status, dose.taken_at, dose.taken_by_id = status, utcnow(), by.id if by else None
    db.commit()
    member = db.get(Member, rec.member_id)
    if was_escalated and member.role == "child" and status == "taken":
        raise_alert(db, family, "health", f"{member.name} took {_label(rec)}", "Marked taken after the reminder.",
                    member_id=member.id, audience=["parent"])
    return dose
