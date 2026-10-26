import secrets
from datetime import date
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Member, Transaction
from ..security import current_member, get_owned, hash_secret, require_parent
from ..services.chores import balance
from ..services import presence
from ..services.presence import normalize_mac
from ..timeutil import iso

router = APIRouter(prefix="/api", tags=["members"])

PIN_PATTERN = r"^\d{4,8}$"


def wallet_cents(db: Session, member_id: int) -> int:
    return db.query(func.coalesce(func.sum(Transaction.amount_cents), 0)).filter_by(member_id=member_id).scalar()


def member_out(db: Session, m: Member) -> dict:
    return {
        "id": m.id, "name": m.name, "role": m.role, "email": m.email, "phone": m.phone, "color": m.color,
        "birthday": m.birthday.isoformat() if m.birthday else None,
        "weekly_allowance_cents": m.weekly_allowance_cents, "share_location": m.share_location,
        "has_pin": bool(m.pin_hash), "points": balance(db, m.id), "wallet_cents": wallet_cents(db, m.id),
        "wifi_macs": m.wifi_macs or [], "wifi_last_seen": iso(m.wifi_last_seen),
    }


@router.get("/members")
def list_members(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    members = db.query(Member).filter_by(family_id=me.family_id).order_by(Member.role.desc(), Member.id).all()
    return [member_out(db, m) for m in members]


class MemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(pattern="^(parent|child)$")
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8)
    pin: str | None = Field(default=None, pattern=PIN_PATTERN)
    phone: str | None = None
    color: str = Field(default="#4F7CFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    birthday: date | None = None
    weekly_allowance_cents: int = Field(default=0, ge=0)
    share_location: bool = True


@router.post("/members")
def add_member(body: MemberIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    if body.role == "parent" and not (body.email and body.password):
        raise HTTPException(422, "Parents need an email and password")
    if body.role == "child" and not body.pin:
        raise HTTPException(422, "Kids need a PIN to sign in")
    if body.email and db.query(Member).filter_by(email=body.email.lower()).first():
        raise HTTPException(409, "That email is already registered")
    m = Member(family_id=me.family_id, name=body.name, role=body.role,
               email=body.email.lower() if body.email else None,
               password_hash=hash_secret(body.password) if body.password else None,
               pin_hash=hash_secret(body.pin) if body.pin else None, phone=body.phone, color=body.color,
               birthday=body.birthday, weekly_allowance_cents=body.weekly_allowance_cents,
               share_location=body.share_location)
    db.add(m)
    db.commit()
    return member_out(db, m)


class MemberPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    password: str | None = Field(default=None, min_length=8)
    pin: str | None = Field(default=None, pattern=PIN_PATTERN)
    phone: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    birthday: date | None = None
    weekly_allowance_cents: int | None = Field(default=None, ge=0)
    share_location: bool | None = None
    wifi_macs: list[str] | None = None


@router.patch("/members/{member_id}")
def update_member(member_id: int, body: MemberPatch, me: Member = Depends(current_member),
                  db: Session = Depends(get_db)):
    m = get_owned(db, Member, member_id, me.family_id)
    data = body.model_dump(exclude_unset=True)
    if me.role != "parent":
        # Kids may only change their own color and location sharing.
        if m.id != me.id or set(data) - {"color", "share_location"}:
            raise HTTPException(403, "Ask a parent to change that")
    if "password" in data:
        m.password_hash = hash_secret(data.pop("password"))
    if "pin" in data:
        m.pin_hash = hash_secret(data.pop("pin"))
    if "wifi_macs" in data:
        try:
            data["wifi_macs"] = sorted({normalize_mac(x) for x in data["wifi_macs"] if x.strip()})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    for k, v in data.items():
        setattr(m, k, v)
    db.commit()
    return member_out(db, m)


@router.delete("/members/{member_id}")
def remove_member(member_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    m = get_owned(db, Member, member_id, me.family_id)
    if m.id == me.id:
        raise HTTPException(400, "You can't remove yourself")
    db.delete(m)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- family settings

def family_out(fam) -> dict:
    hooks = f"{settings.base_url}/api/hooks/{fam.hook_token}"
    return {
        "id": fam.id, "name": fam.name, "timezone": fam.timezone, "join_code": fam.join_code,
        "settings": fam.settings or {},
        "webhooks": {"lock": f"{hooks}/lock", "location": f"{hooks}/location?member=<member id>",
                     "presence": f"{hooks}/presence?member=<member id>&place=Home&event=arrive",
                     "alert": f"{hooks}/alert"},
        "hook_base": hooks,
        "wifi_scan": {**presence.status, "last_scan": iso(presence.status["last_scan"])},
    }


@router.get("/family")
def get_family(me: Member = Depends(require_parent)):
    return family_out(me.family)


class FamilyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = None
    settings: dict | None = None  # merged into existing settings


ALLOWED_SETTINGS = {"unlocked_minutes", "door_open_minutes", "quiet_start", "quiet_end", "notify_all_unlocks",
                    "battery_low_percent", "allowance_weekday", "digest_hour", "push_min_severity",
                    "wifi_presence", "wifi_away_minutes", "file_storage",
                    "med_escalate_minutes"}


@router.patch("/family")
def update_family(body: FamilyPatch, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    fam = me.family
    if body.name:
        fam.name = body.name
    if body.timezone:
        if body.timezone not in available_timezones():
            raise HTTPException(422, "Unknown timezone")
        fam.timezone = body.timezone
    if body.settings:
        unknown = set(body.settings) - ALLOWED_SETTINGS
        if unknown:
            raise HTTPException(422, f"Unknown settings: {', '.join(sorted(unknown))}")
        fam.settings = {**(fam.settings or {}), **body.settings}
    db.commit()
    return family_out(fam)


@router.post("/family/rotate-hook-token")
def rotate_hook_token(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    me.family.hook_token = secrets.token_urlsafe(24)
    db.commit()
    return family_out(me.family)
