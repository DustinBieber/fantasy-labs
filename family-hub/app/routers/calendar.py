from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Event, Integration, Member
from ..security import current_member, get_owned, require_parent
from ..services import google, icloud
from ..timeutil import iso, to_utc_naive, utcnow

router = APIRouter(prefix="/api/events", tags=["calendar"])


def event_out(e: Event) -> dict:
    return {"id": e.id, "title": e.title, "start": iso(e.start), "end": iso(e.end), "all_day": e.all_day,
            "location": e.location, "notes": e.notes, "member_ids": e.member_ids or [], "source": e.source,
            "push_to": e.push_to or [], "pushed": sorted((e.pushed or {}).keys())}


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    location: str = Field(default="", max_length=200)
    notes: str = ""
    member_ids: list[int] = Field(default_factory=list)
    push_to: list[str] = Field(default_factory=list)


class EventPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool | None = None
    location: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    member_ids: list[int] | None = None


def _validate(db, me, member_ids, push_to=()):
    for mid in member_ids:
        get_owned(db, Member, mid, me.family_id)
    bad = set(push_to) - {"google", "icloud"}
    if bad:
        raise HTTPException(422, f"Unknown calendar provider(s): {', '.join(bad)}")


@router.get("")
def list_events(start: datetime | None = None, end: datetime | None = None,
                me: Member = Depends(current_member), db: Session = Depends(get_db)):
    start = to_utc_naive(start) if start else utcnow() - timedelta(days=1)
    end = to_utc_naive(end) if end else start + timedelta(days=31)
    q = db.query(Event).filter(Event.family_id == me.family_id, Event.start < end,
                               ((Event.end.is_(None)) & (Event.start >= start)) | (Event.end > start))
    return [event_out(e) for e in q.order_by(Event.start).limit(1000)]


@router.post("")
def create_event(body: EventIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    _validate(db, me, body.member_ids, body.push_to)
    data = body.model_dump()
    data["start"] = to_utc_naive(body.start)
    data["end"] = to_utc_naive(body.end) if body.end else None
    if data["end"] and data["end"] < data["start"]:
        raise HTTPException(422, "Event ends before it starts")
    e = Event(family_id=me.family_id, source="local", pushed={}, **data)
    db.add(e)
    db.commit()
    return event_out(e)


@router.patch("/{event_id}")
def update_event(event_id: int, body: EventPatch, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    e = get_owned(db, Event, event_id, me.family_id)
    data = body.model_dump(exclude_unset=True)
    if e.source != "local" and set(data) - {"member_ids"}:
        raise HTTPException(400, f"Edit this event in {e.source.title()} Calendar; it syncs back here.")
    if "member_ids" in data:
        _validate(db, me, data["member_ids"])
    for k in ("start", "end"):
        if data.get(k):
            data[k] = to_utc_naive(data[k])
    for k, v in data.items():
        setattr(e, k, v)
    db.commit()
    return event_out(e)


@router.delete("/{event_id}")
def delete_event(event_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    e = get_owned(db, Event, event_id, me.family_id)
    if e.source != "local":
        raise HTTPException(400, f"Delete this event in {e.source.title()} Calendar; it syncs back here.")
    db.delete(e)
    db.commit()
    return {"ok": True}


@router.post("/sync")
def sync_now(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    results = {}
    for integ in db.query(Integration).filter(Integration.family_id == me.family_id,
                                              Integration.provider.in_(("google", "icloud"))):
        try:
            results[integ.provider] = (google if integ.provider == "google" else icloud).sync_calendar(db, integ)
        except Exception as exc:
            db.rollback()
            integ.last_error = str(exc)[:500]
            db.commit()
            results[integ.provider] = {"error": integ.last_error}
    if not results:
        raise HTTPException(400, "Connect Google or iCloud in Settings to sync calendars")
    return results
