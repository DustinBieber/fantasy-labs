"""Connections page API, the subscribable family calendar feed, and the inbound event webhook."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..connectors import BY_KEY, CATALOG, CATEGORIES, Ctx, NeedsInput
from ..db import get_db
from ..models import Event, Family, HealthRecord, Homework, Member
from ..security import get_owned, require_parent
from ..timeutil import to_utc_naive, utcnow
from .home import _family_by_hook

router = APIRouter(prefix="/api", tags=["connections"])


def _connector(key: str):
    c = BY_KEY.get(key)
    if c is None:
        raise HTTPException(404, "Unknown connection")
    return c


@router.get("/connectors")
def list_connectors(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    items = []
    for c in CATALOG:
        try:
            items.append(c.describe(Ctx(db, me.family, c.key)))
        except Exception as exc:  # a broken status check must not hide the whole page
            items.append({"key": c.key, "name": c.name, "category": c.category, "state": "attention",
                          "detail": str(exc)[:200], "fields": [], "steps": [], "summary": c.summary,
                          "availability": c.availability, "manage": c.manage, "connectable": False,
                          "subjects": [], "mapping": {}})
    hooks = f"{settings.base_url}/api/hooks/{me.family.hook_token}"
    done = sum(1 for i in items if i["state"] == "connected")
    return {"categories": CATEGORIES, "items": items, "connected": done, "total": len(items),
            "calendar_feed": f"{hooks}/calendar.ics",
            "google_configured": bool(settings.google_client_id and settings.google_client_secret),
            "google_redirect_uri": settings.google_redirect_uri}


class ConnectIn(BaseModel):
    values: dict = Field(default_factory=dict)


def _run(fn, ctx: Ctx, values: dict) -> dict:
    try:
        return {"ok": True, "message": fn(ctx, values)}
    except NeedsInput as need:
        return {"ok": False, "step": need.step, "message": need.message,
                "fields": [f.__dict__ for f in need.fields]}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        ctx.db.rollback()
        raise HTTPException(502, f"Couldn't connect: {str(exc)[:300] or exc.__class__.__name__}") from exc


@router.post("/connectors/{key}/connect")
def connect(key: str, body: ConnectIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    c = _connector(key)
    if not c.fields:
        raise HTTPException(400, f"{c.name} is set up from another page")
    missing = [f.label for f in c.fields if f.required and not str(body.values.get(f.name, "")).strip()]
    if missing:
        raise HTTPException(422, f"Missing: {', '.join(missing)}")
    return _run(c.connect, Ctx(db, me.family, key), body.values)


@router.post("/connectors/{key}/finish")
def finish(key: str, body: ConnectIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    return _run(_connector(key).finish, Ctx(db, me.family, key), body.values)


@router.post("/connectors/{key}/sync")
def sync(key: str, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    c = _connector(key)
    ctx = Ctx(db, me.family, key)
    if ctx.integration is None:
        raise HTTPException(400, "Not connected")
    try:
        msg = c.poll(ctx)
    except Exception as exc:
        db.rollback()
        integ = ctx.integration
        integ.last_error = str(exc)[:500]
        db.commit()
        raise HTTPException(502, integ.last_error) from exc
    integ = ctx.integration
    integ.last_sync_at, integ.last_error = utcnow(), ""
    db.commit()
    return {"ok": True, "message": msg}


class MapIn(BaseModel):
    map: dict[str, int | None]


@router.put("/connectors/{key}/map")
def set_mapping(key: str, body: MapIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    ctx = Ctx(db, me.family, _connector(key).key)
    if ctx.integration is None:
        raise HTTPException(400, "Not connected")
    for member_id in body.map.values():
        if member_id is not None:
            get_owned(db, Member, member_id, me.family_id)
    ctx.save(options={"map": body.map, "last_ts": {}})
    return {"ok": True}


@router.delete("/connectors/{key}")
def disconnect(key: str, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    _connector(key).disconnect(Ctx(db, me.family, key))
    return {"ok": True}


# ---------------------------------------------------------------- family calendar feed (.ics)

def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, raw = [], line.encode()
    while len(raw) > 74:
        cut = 74
        while (raw[cut] & 0xC0) == 0x80:  # don't split a UTF-8 character
            cut -= 1
        out.append(raw[:cut].decode())
        raw = raw[cut:]
    out.append(raw.decode())
    return "\r\n ".join(out)


def build_ics(db: Session, family: Family, member_id: int | None = None, only_hub: bool = False) -> str:
    now = utcnow()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    names = {m.id: m.name for m in family.members}
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Family Hub//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{_ics_escape(family.name)}" + (f" – {names.get(member_id, '')}" if member_id else ""),
             "X-PUBLISHED-TTL:PT1H", "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]

    def add(uid: str, title: str, start, end=None, all_day=False, location="", notes=""):
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@family-hub", f"DTSTAMP:{stamp}"])
        if all_day:
            d0 = start if isinstance(start, date) and not isinstance(start, datetime) else start.date()
            d1 = (end.date() if isinstance(end, datetime) else end) if end else d0 + timedelta(days=1)
            if d1 <= d0:
                d1 = d0 + timedelta(days=1)
            lines.extend([f"DTSTART;VALUE=DATE:{d0:%Y%m%d}", f"DTEND;VALUE=DATE:{d1:%Y%m%d}"])
        else:
            lines.extend([f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{(end or start + timedelta(hours=1)):%Y%m%dT%H%M%SZ}"])
        lines.append(f"SUMMARY:{_ics_escape(title)}")
        if location:
            lines.append(f"LOCATION:{_ics_escape(location)}")
        if notes:
            lines.append(f"DESCRIPTION:{_ics_escape(notes)}")
        lines.append("END:VEVENT")

    q = db.query(Event).filter(Event.family_id == family.id, Event.start >= now - timedelta(days=30),
                               Event.start <= now + timedelta(days=180))
    for e in q:
        if only_hub and e.source != "local":
            continue  # for people who already see the Google/iCloud calendars directly
        if member_id and e.member_ids and member_id not in e.member_ids:
            continue
        who = ", ".join(names[i] for i in (e.member_ids or []) if i in names)
        add(f"event-{e.id}", e.title + (f" ({who})" if who and not member_id else ""), e.start, e.end, e.all_day,
            e.location, e.notes)
    hw = db.query(Homework).filter(Homework.family_id == family.id, Homework.status != "done",
                                   Homework.due_date >= now.date() - timedelta(days=7))
    for h in hw:
        if member_id and h.member_id != member_id:
            continue
        add(f"homework-{h.id}", f"📚 {names.get(h.member_id, '')}: {h.subject} – {h.title}", h.due_date, all_day=True)
    for r in db.query(HealthRecord).filter(HealthRecord.family_id == family.id):
        if member_id and r.member_id != member_id:
            continue
        if r.kind == "appointment" and r.date and r.date >= now.date() - timedelta(days=30):
            add(f"health-{r.id}", f"🩺 {names.get(r.member_id, '')}: {r.title}", r.date, all_day=True, notes=r.notes)
        if r.next_due and r.next_due >= now.date():
            add(f"health-due-{r.id}", f"🩺 Due: {names.get(r.member_id, '')} – {r.title}", r.next_due, all_day=True)
    for m in family.members:
        if m.birthday and (not member_id or m.id == member_id):
            for year in (now.year, now.year + 1):
                try:
                    day = m.birthday.replace(year=year)
                except ValueError:  # Feb 29
                    day = date(year, 2, 28)
                add(f"birthday-{m.id}-{year}", f"🎂 {m.name}'s birthday", day, all_day=True)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


@router.get("/hooks/{token}/calendar.ics", include_in_schema=False)
def calendar_feed(token: str, member: int | None = None, only_hub: bool = False, db: Session = Depends(get_db)):
    fam = _family_by_hook(db, token)
    if member is not None:
        get_owned(db, Member, member, fam.id)
    return Response(build_ics(db, fam, member, only_hub), media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


class EventHookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    location: str = Field(default="", max_length=200)
    notes: str = ""
    member: str = ""  # a member's name, optional


@router.post("/hooks/{token}/event")
def event_hook(token: str, body: EventHookIn, db: Session = Depends(get_db)):
    """Add an event from any automation (iPhone Shortcuts "Add to Family Hub", email rules, IFTTT…)."""
    fam = _family_by_hook(db, token)
    who = [m.id for m in fam.members if body.member and m.name.lower() == body.member.strip().lower()]
    e = Event(family_id=fam.id, title=body.title, start=to_utc_naive(body.start),
              end=to_utc_naive(body.end) if body.end else None, all_day=body.all_day, location=body.location,
              notes=body.notes, member_ids=who, source="local", push_to=[], pushed={})
    db.add(e)
    db.commit()
    return {"id": e.id}
