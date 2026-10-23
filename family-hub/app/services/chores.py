import calendar
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Chore, ChoreInstance, Family, Member, PointsEntry
from ..timeutil import utcnow
from .notify import raise_alert

MAX_RANGE_DAYS = 62


def occurs_on(chore: Chore, day: date) -> bool:
    if not chore.active or day < chore.start_date:
        return False
    if chore.recurrence == "once":
        return day == chore.start_date
    if chore.recurrence == "daily":
        return True
    if chore.recurrence == "weekly":
        return day.weekday() in (chore.weekdays or [chore.start_date.weekday()])
    if chore.recurrence == "monthly":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day == min(chore.start_date.day, last)
    return False


def ensure_instances(db: Session, family_id: int, start: date, end: date) -> None:
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"Date range too large (max {MAX_RANGE_DAYS} days)")
    chores = db.query(Chore).filter_by(family_id=family_id, active=True).all()
    if not chores:
        return
    existing = {(i.chore_id, i.due_date) for i in db.query(ChoreInstance).join(Chore).filter(
        Chore.family_id == family_id, ChoreInstance.due_date >= start, ChoreInstance.due_date <= end)}
    day = start
    while day <= end:
        for chore in chores:
            if occurs_on(chore, day) and (chore.id, day) not in existing:
                db.add(ChoreInstance(chore_id=chore.id, due_date=day))
        day += timedelta(days=1)
    db.commit()


def balance(db: Session, member_id: int) -> int:
    return db.query(func.coalesce(func.sum(PointsEntry.amount), 0)).filter_by(member_id=member_id).scalar()


def award(db: Session, member: Member, amount: int, reason: str) -> None:
    if amount:
        db.add(PointsEntry(family_id=member.family_id, member_id=member.id, amount=amount, reason=reason[:200]))


def complete(db: Session, family: Family, inst: ChoreInstance, me: Member) -> ChoreInstance:
    chore = inst.chore
    if me.role != "parent" and chore.assignee_id not in (None, me.id):
        raise HTTPException(403, "This chore is assigned to someone else")
    if inst.status in ("submitted", "approved"):
        raise HTTPException(409, "Already done")
    inst.completed_by_id, inst.completed_at = me.id, utcnow()
    if chore.requires_approval and me.role != "parent":
        inst.status = "submitted"
        db.commit()
        raise_alert(db, family, "chore", f"{me.name} finished “{chore.title}”", "Waiting for your approval.",
                    member_id=me.id, audience=["parent"])
    else:
        _approve(db, inst, me, me)
    return inst


def review(db: Session, family: Family, inst: ChoreInstance, parent: Member, approve: bool) -> ChoreInstance:
    if inst.status != "submitted":
        raise HTTPException(409, "Only submitted chores can be reviewed")
    doer = db.get(Member, inst.completed_by_id)
    if approve:
        _approve(db, inst, doer, parent)
        raise_alert(db, family, "chore", f"“{inst.chore.title}” approved: +{inst.chore.points} points",
                    member_id=doer.id, audience=["child"])
    else:
        inst.status, inst.reviewed_by_id = "rejected", parent.id
        db.commit()
        raise_alert(db, family, "chore", f"“{inst.chore.title}” needs another try", member_id=doer.id,
                    audience=["child"])
    return inst


def _approve(db: Session, inst: ChoreInstance, doer: Member, reviewer: Member) -> None:
    inst.status, inst.reviewed_by_id = "approved", reviewer.id
    award(db, doer, inst.chore.points, f"Chore: {inst.chore.title} ({inst.due_date:%b %-d})")
    db.commit()
