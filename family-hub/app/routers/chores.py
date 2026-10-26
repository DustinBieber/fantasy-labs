from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Chore, ChoreInstance, Member, PointsEntry, Redemption, Reward
from ..security import current_member, ensure_self_or_parent, get_owned, require_parent
from ..services import chores as svc
from ..services import files
from ..services.notify import raise_alert
from ..timeutil import iso, local_today

router = APIRouter(prefix="/api", tags=["chores & rewards"])
MAX_PHOTO_BYTES = 15 * 1024 * 1024


def chore_out(c: Chore) -> dict:
    return {"id": c.id, "title": c.title, "notes": c.notes, "points": c.points, "assignee_id": c.assignee_id,
            "recurrence": c.recurrence, "weekdays": c.weekdays, "start_date": c.start_date.isoformat(),
            "requires_approval": c.requires_approval, "requires_photo": bool(c.requires_photo), "active": c.active}


def instance_out(i: ChoreInstance) -> dict:
    return {"id": i.id, "chore_id": i.chore_id, "title": i.chore.title, "points": i.chore.points,
            "assignee_id": i.chore.assignee_id, "due_date": i.due_date.isoformat(), "status": i.status,
            "completed_by_id": i.completed_by_id, "completed_at": iso(i.completed_at),
            "requires_photo": bool(i.chore.requires_photo), "photo": i.photo}


class ChoreIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    notes: str = ""
    points: int = Field(default=10, ge=0, le=10000)
    assignee_id: int | None = None
    recurrence: str = Field(default="once", pattern="^(once|daily|weekly|monthly)$")
    weekdays: list[int] = Field(default_factory=list)
    start_date: date | None = None
    requires_approval: bool = True
    requires_photo: bool = False


class ChorePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str | None = None
    points: int | None = Field(default=None, ge=0, le=10000)
    assignee_id: int | None = None
    recurrence: str | None = Field(default=None, pattern="^(once|daily|weekly|monthly)$")
    weekdays: list[int] | None = None
    requires_approval: bool | None = None
    requires_photo: bool | None = None
    active: bool | None = None


def _check_assignee(db, me, assignee_id):
    if assignee_id is not None:
        get_owned(db, Member, assignee_id, me.family_id)


@router.get("/chores")
def list_chores(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    return [chore_out(c) for c in db.query(Chore).filter_by(family_id=me.family_id, active=True).order_by(Chore.title)]


@router.post("/chores")
def create_chore(body: ChoreIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    _check_assignee(db, me, body.assignee_id)
    if any(d < 0 or d > 6 for d in body.weekdays):
        raise HTTPException(422, "weekdays are 0 (Mon) to 6 (Sun)")
    c = Chore(family_id=me.family_id, **body.model_dump(exclude={"start_date"}),
              start_date=body.start_date or local_today(me.family.timezone))
    db.add(c)
    db.commit()
    return chore_out(c)


@router.patch("/chores/{chore_id}")
def update_chore(chore_id: int, body: ChorePatch, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    c = get_owned(db, Chore, chore_id, me.family_id)
    data = body.model_dump(exclude_unset=True)
    if "assignee_id" in data:
        _check_assignee(db, me, data["assignee_id"])
    for k, v in data.items():
        setattr(c, k, v)
    if c.active is False:
        # Remove not-yet-done future occurrences so they stop showing up.
        db.query(ChoreInstance).filter(ChoreInstance.chore_id == c.id, ChoreInstance.status == "todo",
                                       ChoreInstance.due_date >= local_today(me.family.timezone)).delete()
    db.commit()
    return chore_out(c)


@router.delete("/chores/{chore_id}")
def delete_chore(chore_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    return update_chore(chore_id, ChorePatch(active=False), me, db)


@router.get("/chores/instances")
def list_instances(start: date | None = None, end: date | None = None, member_id: int | None = None,
                   me: Member = Depends(current_member), db: Session = Depends(get_db)):
    today = local_today(me.family.timezone)
    start, end = start or today, end or start or today
    if end < start:
        raise HTTPException(422, "end is before start")
    svc.ensure_instances(db, me.family_id, start, end)
    q = db.query(ChoreInstance).join(Chore).filter(Chore.family_id == me.family_id,
                                                   ChoreInstance.due_date >= start, ChoreInstance.due_date <= end)
    if member_id is not None:
        q = q.filter((Chore.assignee_id == member_id) | (Chore.assignee_id.is_(None)))
    return [instance_out(i) for i in q.order_by(ChoreInstance.due_date, Chore.title)]


def _instance(db, me, inst_id) -> ChoreInstance:
    inst = db.get(ChoreInstance, inst_id)
    if not inst or inst.chore.family_id != me.family_id:
        raise HTTPException(404, "Chore not found")
    return inst


@router.post("/chores/instances/{inst_id}/complete")
def complete_instance(inst_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    return instance_out(svc.complete(db, me.family, _instance(db, me, inst_id), me))


@router.post("/chores/instances/{inst_id}/photo")
async def complete_with_photo(inst_id: int, file: UploadFile = File(...), me: Member = Depends(current_member),
                              db: Session = Depends(get_db)):
    """Finish a chore with photo proof (stored on the hub, shown to the approving parent)."""
    inst = _instance(db, me, inst_id)
    svc.check_can_complete(inst, me)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(415, "Please send a photo")
    content = await file.read(MAX_PHOTO_BYTES + 1)
    if len(content) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is too large (15 MB max)")
    photo = files.save("chores", inst.id, file.filename or "photo.jpg", content)
    return instance_out(svc.complete(db, me.family, inst, me, photo=photo))


class ReviewIn(BaseModel):
    approve: bool


@router.post("/chores/instances/{inst_id}/review")
def review_instance(inst_id: int, body: ReviewIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    return instance_out(svc.review(db, me.family, _instance(db, me, inst_id), me, body.approve))


# ---------------------------------------------------------------- rewards

def reward_out(r: Reward) -> dict:
    return {"id": r.id, "title": r.title, "cost": r.cost, "icon": r.icon, "active": r.active}


def redemption_out(r: Redemption) -> dict:
    return {"id": r.id, "reward_id": r.reward_id, "title": r.reward.title, "icon": r.reward.icon,
            "member_id": r.member_id, "cost": r.cost, "status": r.status, "created_at": iso(r.created_at)}


class RewardIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    cost: int = Field(ge=1, le=1_000_000)
    icon: str = Field(default="🎁", max_length=16)


class RewardPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    cost: int | None = Field(default=None, ge=1, le=1_000_000)
    icon: str | None = Field(default=None, max_length=16)
    active: bool | None = None


@router.get("/rewards")
def list_rewards(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    return [reward_out(r) for r in db.query(Reward).filter_by(family_id=me.family_id, active=True).order_by(Reward.cost)]


@router.post("/rewards")
def create_reward(body: RewardIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    r = Reward(family_id=me.family_id, **body.model_dump())
    db.add(r)
    db.commit()
    return reward_out(r)


@router.patch("/rewards/{reward_id}")
def update_reward(reward_id: int, body: RewardPatch, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    r = get_owned(db, Reward, reward_id, me.family_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    db.commit()
    return reward_out(r)


@router.delete("/rewards/{reward_id}")
def delete_reward(reward_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    return update_reward(reward_id, RewardPatch(active=False), me, db)


@router.post("/rewards/{reward_id}/redeem")
def redeem(reward_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    r = get_owned(db, Reward, reward_id, me.family_id)
    if not r.active:
        raise HTTPException(404, "Reward not available")
    have = svc.balance(db, me.id)
    if have < r.cost:
        raise HTTPException(409, f"You need {r.cost - have} more points")
    red = Redemption(family_id=me.family_id, reward_id=r.id, member_id=me.id, cost=r.cost)
    db.add(red)
    svc.award(db, me, -r.cost, f"Reward: {r.title}")  # held now, refunded if declined
    db.commit()
    raise_alert(db, me.family, "reward", f"{me.name} wants “{r.title}”", f"{r.cost} points", member_id=me.id,
                audience=["parent"])
    return redemption_out(red)


@router.get("/redemptions")
def list_redemptions(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    q = db.query(Redemption).filter_by(family_id=me.family_id)
    if me.role != "parent":
        q = q.filter_by(member_id=me.id)
    return [redemption_out(r) for r in q.order_by(Redemption.created_at.desc()).limit(100)]


class DecideIn(BaseModel):
    fulfill: bool


@router.post("/redemptions/{red_id}/decide")
def decide(red_id: int, body: DecideIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    red = get_owned(db, Redemption, red_id, me.family_id)
    if red.status != "pending":
        raise HTTPException(409, "Already decided")
    kid = db.get(Member, red.member_id)
    if body.fulfill:
        red.status = "fulfilled"
        msg = f"Enjoy your “{red.reward.title}”! 🎉"
    else:
        red.status = "declined"
        svc.award(db, kid, red.cost, f"Refund: {red.reward.title}")
        msg = f"“{red.reward.title}” wasn't approved this time; points refunded."
    db.commit()
    raise_alert(db, me.family, "reward", msg, member_id=kid.id, audience=["child"])
    return redemption_out(red)


@router.get("/points/{member_id}")
def ledger(member_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    ensure_self_or_parent(me, member_id)
    get_owned(db, Member, member_id, me.family_id)
    rows = db.query(PointsEntry).filter_by(member_id=member_id).order_by(PointsEntry.created_at.desc()).limit(200)
    return {"balance": svc.balance(db, member_id),
            "entries": [{"amount": e.amount, "reason": e.reason, "created_at": iso(e.created_at)} for e in rows]}


class AdjustIn(BaseModel):
    member_id: int
    amount: int = Field(ge=-100_000, le=100_000)
    reason: str = Field(min_length=1, max_length=200)


@router.post("/points/adjust")
def adjust(body: AdjustIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    kid = get_owned(db, Member, body.member_id, me.family_id)
    svc.award(db, kid, body.amount, body.reason)
    db.commit()
    sign = "+" if body.amount >= 0 else ""
    raise_alert(db, me.family, "reward", f"{sign}{body.amount} points: {body.reason}", member_id=kid.id,
                audience=["child"])
    return {"balance": svc.balance(db, kid.id)}
