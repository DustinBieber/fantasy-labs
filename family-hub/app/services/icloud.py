"""iCloud Calendar over CalDAV.

Apple has no OAuth API for iCloud Calendar, so the parent signs in with their Apple ID and an
app-specific password (appleid.apple.com > Sign-In and Security > App-Specific Passwords).
The same approach works for any CalDAV server (Fastmail, Nextcloud, ...) by changing the URL.
"""
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from ..crypto import decrypt_json
from ..models import Event, Integration
from ..timeutil import to_utc_naive, utcnow

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"


def _calendar(secret: dict, options: dict):
    import caldav

    client = caldav.DAVClient(url=secret.get("url") or ICLOUD_CALDAV_URL,
                              username=secret["username"], password=secret["password"])
    calendars = client.principal().calendars()
    if not calendars:
        raise RuntimeError("No calendars found on this account")
    wanted = (options.get("calendar_name") or "").lower()
    for cal in calendars:
        if wanted and (cal.name or "").lower() == wanted:
            return cal
    return calendars[0]


def _as_utc(value) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        return to_utc_naive(value), False
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()), True
    raise ValueError(f"Unsupported date value {value!r}")


def fetch_remote(secret: dict, options: dict, start: datetime, end: datetime) -> list[dict]:
    cal = _calendar(secret, options)
    out = []
    for obj in cal.search(start=start, end=end, event=True, expand=True):
        comp = obj.icalendar_component
        begin, all_day = _as_utc(comp.get("dtstart").dt)
        finish = _as_utc(comp.get("dtend").dt)[0] if comp.get("dtend") else None
        uid = str(comp.get("uid"))
        if comp.get("recurrence-id"):
            uid += "@" + begin.isoformat()
        out.append({"uid": uid, "title": str(comp.get("summary") or "(no title)"), "start": begin,
                    "end": finish, "all_day": all_day, "location": str(comp.get("location") or ""),
                    "notes": str(comp.get("description") or ""),
                    "from_hub": str(comp.get("x-family-hub-id") or "") != ""})
    return out


def push_remote(secret: dict, options: dict, ev: Event) -> str:
    uid = f"familyhub-{ev.id}-{int(utcnow().timestamp())}@familyhub"
    if ev.all_day:
        end = (ev.end or ev.start).date()
        if end <= ev.start.date():
            end = ev.start.date() + timedelta(days=1)
        times = f"DTSTART;VALUE=DATE:{ev.start:%Y%m%d}\r\nDTEND;VALUE=DATE:{end:%Y%m%d}"
    else:
        end = ev.end or ev.start + timedelta(hours=1)
        times = f"DTSTART:{ev.start:%Y%m%dT%H%M%SZ}\r\nDTEND:{end:%Y%m%dT%H%M%SZ}"

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Family Hub//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:{utcnow():%Y%m%dT%H%M%SZ}\r\n{times}\r\n"
        f"SUMMARY:{esc(ev.title)}\r\nLOCATION:{esc(ev.location)}\r\nDESCRIPTION:{esc(ev.notes)}\r\n"
        f"X-FAMILY-HUB-ID:{ev.id}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    _calendar(secret, options).add_event(ics)
    return uid


def sync_calendar(db: Session, integ: Integration, days: int = 60) -> dict:
    secret = decrypt_json(integ.secret_enc)
    now = utcnow()
    remote = [r for r in fetch_remote(secret, integ.options, now, now + timedelta(days=days)) if not r["from_hub"]]
    seen = set()
    for r in remote:
        seen.add(r["uid"])
        ev = db.query(Event).filter_by(family_id=integ.family_id, source="icloud", external_id=r["uid"]).first()
        if ev is None:
            ev = Event(family_id=integ.family_id, source="icloud", external_id=r["uid"], member_ids=[])
            db.add(ev)
        ev.title, ev.start, ev.end, ev.all_day = r["title"][:200], r["start"], r["end"], r["all_day"]
        ev.location, ev.notes = r["location"][:200], r["notes"]
    for ev in db.query(Event).filter(Event.family_id == integ.family_id, Event.source == "icloud",
                                     Event.start >= now, Event.start <= now + timedelta(days=days)):
        if ev.external_id not in seen:
            db.delete(ev)
    pushed = 0
    for ev in db.query(Event).filter(Event.family_id == integ.family_id, Event.source == "local"):
        if "icloud" in (ev.push_to or []) and not (ev.pushed or {}).get("icloud"):
            ev.pushed = {**(ev.pushed or {}), "icloud": push_remote(secret, integ.options, ev)}
            pushed += 1
    integ.last_sync_at = utcnow()
    integ.last_error = ""
    db.commit()
    return {"pulled": len(remote), "pushed": pushed}


def verify(secret: dict, options: dict) -> str:
    """Sign in once to fail fast on a bad app-specific password. Returns the calendar's name."""
    return _calendar(secret, options).name or "Calendar"
