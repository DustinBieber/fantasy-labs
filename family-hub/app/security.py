import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import AuthSession, Member
from .timeutil import utcnow

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_secret(raw: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(raw.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_secret(raw: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(raw.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, member: Member) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_token_hash(token), member_id=member.id,
                       expires_at=utcnow() + timedelta(days=settings.session_days)))
    db.commit()
    return token


def revoke_session(db: Session, token: str) -> None:
    db.query(AuthSession).filter_by(token_hash=_token_hash(token)).delete()
    db.commit()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not signed in")
    return authorization[7:].strip()


def current_member(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Member:
    session = db.get(AuthSession, _token_hash(_bearer(authorization)))
    if not session or session.expires_at < utcnow():
        raise HTTPException(401, "Session expired")
    member = db.get(Member, session.member_id)
    if not member:
        raise HTTPException(401, "Member no longer exists")
    return member


def require_parent(me: Member = Depends(current_member)) -> Member:
    if me.role != "parent":
        raise HTTPException(403, "Parents only")
    return me


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    return _bearer(authorization)


def get_owned(db: Session, model, obj_id: int, family_id: int):
    """Fetch a row by id, 404ing if it belongs to another family."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "family_id", None) != family_id:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def ensure_self_or_parent(me: Member, member_id: int | None) -> None:
    if me.role != "parent" and member_id != me.id:
        raise HTTPException(403, "You can only access your own records")
