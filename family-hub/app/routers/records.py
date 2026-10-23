"""Homework, milestones and health records, plus Google Drive attachments for all three."""
import datetime as dt

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import HealthRecord, Homework, Integration, Member, Milestone
from ..security import current_member, ensure_self_or_parent, get_owned, require_parent
from ..services import files, google
from ..services.chores import award
from ..services.notify import raise_alert

router = APIRouter(prefix="/api", tags=["homework, milestones, health"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


# ---------------------------------------------------------------- homework

def homework_out(h: Homework) -> dict:
    return {"id": h.id, "member_id": h.member_id, "subject": h.subject, "title": h.title,
            "due_date": h.due_date.isoformat(), "status": h.status, "notes": h.notes, "points": h.points,
            "attachments": h.attachments or []}


class HomeworkIn(BaseModel):
    member_id: int
    subject: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    due_date: dt.date
    notes: str = ""
    points: int = Field(default=0, ge=0, le=10000)


class HomeworkPatch(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=80)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: dt.date | None = None
    status: str | None = Field(default=None, pattern="^(todo|in_progress|done)$")
    notes: str | None = None
    points: int | None = Field(default=None, ge=0, le=10000)


@router.get("/homework")
def list_homework(member_id: int | None = None, include_done: bool = False,
                  me: Member = Depends(current_member), db: Session = Depends(get_db)):
    if me.role != "parent":
        member_id = me.id
    q = db.query(Homework).filter_by(family_id=me.family_id)
    if member_id is not None:
        q = q.filter_by(member_id=member_id)
    if not include_done:
        q = q.filter(Homework.status != "done")
    return [homework_out(h) for h in q.order_by(Homework.due_date).limit(500)]


@router.post("/homework")
def create_homework(body: HomeworkIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    ensure_self_or_parent(me, body.member_id)
    get_owned(db, Member, body.member_id, me.family_id)
    if me.role != "parent":
        body.points = 0  # kids can't grant themselves bonus points
    h = Homework(family_id=me.family_id, attachments=[], **body.model_dump())
    db.add(h)
    db.commit()
    return homework_out(h)


@router.patch("/homework/{hw_id}")
def update_homework(hw_id: int, body: HomeworkPatch, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    h = get_owned(db, Homework, hw_id, me.family_id)
    ensure_self_or_parent(me, h.member_id)
    data = body.model_dump(exclude_unset=True)
    if me.role != "parent":
        data.pop("points", None)
    for k, v in data.items():
        setattr(h, k, v)
    if h.status == "done" and h.points and not h.points_awarded:
        kid = db.get(Member, h.member_id)
        award(db, kid, h.points, f"Homework: {h.subject} – {h.title}")
        h.points_awarded = True
    db.commit()
    if data.get("status") == "done" and me.role != "parent":
        raise_alert(db, me.family, "homework", f"{me.name} finished {h.subject}: {h.title}", member_id=me.id,
                    audience=["parent"])
    return homework_out(h)


@router.delete("/homework/{hw_id}")
def delete_homework(hw_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    h = get_owned(db, Homework, hw_id, me.family_id)
    ensure_self_or_parent(me, h.member_id)
    db.delete(h)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- milestones

def milestone_out(m: Milestone) -> dict:
    return {"id": m.id, "member_id": m.member_id, "title": m.title, "date": m.date.isoformat(),
            "category": m.category, "notes": m.notes, "attachments": m.attachments or []}


class MilestoneIn(BaseModel):
    member_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    date: dt.date
    category: str = Field(default="general", max_length=40)
    notes: str = ""


class MilestonePatch(BaseModel):
    member_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    date: dt.date | None = None
    category: str | None = Field(default=None, max_length=40)
    notes: str | None = None


@router.get("/milestones")
def list_milestones(member_id: int | None = None, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    q = db.query(Milestone).filter_by(family_id=me.family_id)
    if member_id is not None:
        q = q.filter_by(member_id=member_id)
    return [milestone_out(m) for m in q.order_by(Milestone.date.desc()).limit(1000)]


@router.post("/milestones")
def create_milestone(body: MilestoneIn, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    if body.member_id is not None:
        get_owned(db, Member, body.member_id, me.family_id)
    m = Milestone(family_id=me.family_id, attachments=[], **body.model_dump())
    db.add(m)
    db.commit()
    return milestone_out(m)


@router.patch("/milestones/{ms_id}")
def update_milestone(ms_id: int, body: MilestonePatch, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    m = get_owned(db, Milestone, ms_id, me.family_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("member_id") is not None:
        get_owned(db, Member, data["member_id"], me.family_id)
    for k, v in data.items():
        setattr(m, k, v)
    db.commit()
    return milestone_out(m)


@router.delete("/milestones/{ms_id}")
def delete_milestone(ms_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    db.delete(get_owned(db, Milestone, ms_id, me.family_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- health

HEALTH_KINDS = "^(appointment|medication|vital|allergy|immunization|note)$"


def health_out(r: HealthRecord) -> dict:
    return {"id": r.id, "member_id": r.member_id, "kind": r.kind, "title": r.title,
            "date": r.date.isoformat() if r.date else None, "value": r.value, "unit": r.unit, "notes": r.notes,
            "next_due": r.next_due.isoformat() if r.next_due else None, "attachments": r.attachments or []}


class HealthIn(BaseModel):
    member_id: int
    kind: str = Field(pattern=HEALTH_KINDS)
    title: str = Field(min_length=1, max_length=200)
    date: dt.date | None = None
    value: str = Field(default="", max_length=60)
    unit: str = Field(default="", max_length=20)
    notes: str = ""
    next_due: dt.date | None = None


class HealthPatch(BaseModel):
    kind: str | None = Field(default=None, pattern=HEALTH_KINDS)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    date: dt.date | None = None
    value: str | None = Field(default=None, max_length=60)
    unit: str | None = Field(default=None, max_length=20)
    notes: str | None = None
    next_due: dt.date | None = None


@router.get("/health")
def list_health(member_id: int | None = None, kind: str | None = None,
                me: Member = Depends(current_member), db: Session = Depends(get_db)):
    if me.role != "parent":
        member_id = me.id
    q = db.query(HealthRecord).filter_by(family_id=me.family_id)
    if member_id is not None:
        q = q.filter_by(member_id=member_id)
    if kind:
        q = q.filter_by(kind=kind)
    return [health_out(r) for r in q.order_by(HealthRecord.date.desc()).limit(1000)]


@router.post("/health")
def create_health(body: HealthIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    get_owned(db, Member, body.member_id, me.family_id)
    r = HealthRecord(family_id=me.family_id, attachments=[], **body.model_dump())
    db.add(r)
    db.commit()
    return health_out(r)


@router.patch("/health/{rec_id}")
def update_health(rec_id: int, body: HealthPatch, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    r = get_owned(db, HealthRecord, rec_id, me.family_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    db.commit()
    return health_out(r)


@router.delete("/health/{rec_id}")
def delete_health(rec_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    db.delete(get_owned(db, HealthRecord, rec_id, me.family_id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- attachments (Google Drive or the hub's disk)

ATTACHABLE = {"homework": (Homework, homework_out), "milestones": (Milestone, milestone_out),
              "health": (HealthRecord, health_out)}


@router.post("/attachments/{kind}/{obj_id}")
async def attach(kind: str, obj_id: int, file: UploadFile = File(...), me: Member = Depends(current_member),
                 db: Session = Depends(get_db)):
    if kind not in ATTACHABLE:
        raise HTTPException(404, "Unknown record type")
    model, out = ATTACHABLE[kind]
    obj = get_owned(db, model, obj_id, me.family_id)
    if kind == "health" and me.role != "parent":
        raise HTTPException(403, "Parents only")
    if kind == "homework":
        ensure_self_or_parent(me, obj.member_id)
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Files are limited to 25 MB")
    integ = db.query(Integration).filter_by(family_id=me.family_id, provider="google").first()
    if integ and (me.family.settings or {}).get("file_storage", "google") == "google":
        info = google.drive_upload(db, integ, file.filename or "upload", content,
                                   file.content_type or "application/octet-stream")
    else:
        info = files.save(kind, obj_id, file.filename or "upload", content)
    obj.attachments = [*(obj.attachments or []), info]
    db.commit()
    return out(obj)


@router.get("/files/{rel:path}", include_in_schema=False)
def get_file(rel: str, sig: str = ""):
    path = files.resolve(rel, sig)
    if path is None:
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=path.name.split("-", 1)[-1], content_disposition_type="inline")
