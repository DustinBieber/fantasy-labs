import secrets
import time
from collections import defaultdict
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Family, Member
from ..security import bearer_token, create_session, current_member, hash_secret, revoke_session, verify_secret
from .members import member_out

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Simple in-process brute-force guard for 4–6 digit PINs and passwords.
_FAILS: dict[str, list[float]] = defaultdict(list)
MAX_FAILS, WINDOW_S = 5, 15 * 60


def _check_lockout(key: str) -> None:
    now = time.time()
    _FAILS[key] = [t for t in _FAILS[key] if now - t < WINDOW_S]
    if len(_FAILS[key]) >= MAX_FAILS:
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")


def new_join_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


class SignupIn(BaseModel):
    family_name: str = Field(min_length=1, max_length=120)
    timezone: str = "America/New_York"
    parent_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


@router.post("/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if body.timezone not in available_timezones():
        raise HTTPException(422, "Unknown timezone")
    if db.query(Member).filter_by(email=body.email.lower()).first():
        raise HTTPException(409, "That email is already registered")
    family = Family(name=body.family_name, timezone=body.timezone, hook_token=secrets.token_urlsafe(24),
                    join_code=new_join_code(), settings={})
    db.add(family)
    db.flush()
    parent = Member(family_id=family.id, name=body.parent_name, role="parent", email=body.email.lower(),
                    password_hash=hash_secret(body.password), color="#4F7CFF")
    db.add(parent)
    db.commit()
    return {"token": create_session(db, parent), "member": member_out(db, parent)}


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    key = f"pw:{body.email.lower()}"
    _check_lockout(key)
    member = db.query(Member).filter_by(email=body.email.lower()).first()
    if not member or not verify_secret(body.password, member.password_hash):
        _FAILS[key].append(time.time())
        raise HTTPException(401, "Wrong email or password")
    return {"token": create_session(db, member), "member": member_out(db, member)}


@router.get("/join/{code}")
def join_info(code: str, db: Session = Depends(get_db)):
    family = db.query(Family).filter_by(join_code=code.strip().upper()).first()
    if not family:
        raise HTTPException(404, "No family with that code")
    people = [{"id": m.id, "name": m.name, "color": m.color, "role": m.role}
              for m in family.members if m.pin_hash]
    return {"family_name": family.name, "members": people}


class PinLoginIn(BaseModel):
    join_code: str
    member_id: int
    pin: str = Field(min_length=4, max_length=8)


@router.post("/pin-login")
def pin_login(body: PinLoginIn, db: Session = Depends(get_db)):
    key = f"pin:{body.member_id}"
    _check_lockout(key)
    family = db.query(Family).filter_by(join_code=body.join_code.strip().upper()).first()
    member = db.get(Member, body.member_id)
    if not family or not member or member.family_id != family.id or not verify_secret(body.pin, member.pin_hash):
        _FAILS[key].append(time.time())
        raise HTTPException(401, "Wrong PIN")
    return {"token": create_session(db, member), "member": member_out(db, member)}


@router.post("/logout")
def logout(token: str = Depends(bearer_token), db: Session = Depends(get_db)):
    revoke_session(db, token)
    return {"ok": True}


@router.get("/me")
def me(member: Member = Depends(current_member), db: Session = Depends(get_db)):
    fam = member.family
    return {"member": member_out(db, member),
            "family": {"id": fam.id, "name": fam.name, "timezone": fam.timezone}}
