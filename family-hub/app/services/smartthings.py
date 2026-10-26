"""Samsung SmartThings: lock status and control through the official SmartThings API.

New personal access tokens only last 24 hours, so the hub uses OAuth: you create a free "OAuth-In" app
once with the SmartThings CLI, sign in from the Connections page, and the hub keeps the connection alive
with refresh tokens. SmartThings refresh tokens are single-use, so every refresh saves the new one.
"""
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import decrypt_json, encrypt_json
from ..models import Family, Integration, Lock, OAuthState
from ..timeutil import utcnow
from . import http

API = "https://api.smartthings.com/v1"
AUTHORIZE_URL = "https://api.smartthings.com/oauth/authorize"
TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"
SCOPES = "r:devices:* x:devices:* r:locations:*"


def redirect_uri() -> str:
    return f"{settings.base_url}/api/connectors/smartthings/callback"


def _integration(db: Session, family_id: int) -> Integration | None:
    return db.query(Integration).filter_by(family_id=family_id, provider="smartthings").first()


def start(db: Session, family: Family) -> str:
    integ = _integration(db, family.id)
    secret = decrypt_json(integ.secret_enc)
    state = "st-" + secrets.token_urlsafe(20)
    db.add(OAuthState(state=state, family_id=family.id))
    db.commit()
    return AUTHORIZE_URL + "?" + urlencode({"client_id": secret["client_id"], "response_type": "code",
                                            "redirect_uri": redirect_uri(), "scope": SCOPES, "state": state})


def _token_request(secret: dict, data: dict) -> dict:
    with http.client() as c:
        resp = c.post(TOKEN_URL, data=data, auth=(secret["client_id"], secret["client_secret"]))
        if resp.status_code >= 400:
            raise RuntimeError(f"SmartThings sign-in failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()


def _store(db: Session, integ: Integration, secret: dict, tokens: dict) -> None:
    secret = {**secret, "access_token": tokens["access_token"],
              "refresh_token": tokens.get("refresh_token", secret.get("refresh_token")),
              "expires_at": (utcnow() + timedelta(seconds=int(tokens.get("expires_in", 86399)))).isoformat()}
    integ.secret_enc = encrypt_json(secret)
    integ.last_error = ""
    db.commit()


def finish(db: Session, state: str, code: str) -> Integration:
    row = db.get(OAuthState, state)
    if not row or row.created_at < utcnow() - timedelta(minutes=15):
        raise ValueError("Sign-in expired; start again from the Connections page.")
    integ = _integration(db, row.family_id)
    db.delete(row)
    secret = decrypt_json(integ.secret_enc)
    tokens = _token_request(secret, {"grant_type": "authorization_code", "code": code,
                                     "redirect_uri": redirect_uri(), "client_id": secret["client_id"]})
    _store(db, integ, secret, tokens)
    return integ


def access_token(db: Session, integ: Integration) -> str:
    secret = decrypt_json(integ.secret_enc)
    if not secret.get("refresh_token"):
        raise RuntimeError("Not signed in to SmartThings yet")
    if secret.get("access_token") and datetime.fromisoformat(secret["expires_at"]) > utcnow() + timedelta(minutes=5):
        return secret["access_token"]
    tokens = _token_request(secret, {"grant_type": "refresh_token", "refresh_token": secret["refresh_token"],
                                     "client_id": secret["client_id"]})
    _store(db, integ, secret, tokens)
    return tokens["access_token"]


def _get(db: Session, integ: Integration, path: str, **params) -> dict:
    with http.client() as c:
        resp = c.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {access_token(db, integ)}"})
        resp.raise_for_status()
        return resp.json()


def discover_locks(db: Session, family: Family) -> list[Lock]:
    """Add every SmartThings device with the lock capability to the Doors tab (once)."""
    integ = _integration(db, family.id)
    found = _get(db, integ, "/devices", capability="lock").get("items", [])
    locks = []
    for dev in found:
        lock = db.query(Lock).filter_by(family_id=family.id, provider="smartthings", external_id=dev["deviceId"]).first()
        if lock is None:
            lock = Lock(family_id=family.id, provider="smartthings", external_id=dev["deviceId"],
                        name=(dev.get("label") or dev.get("name") or "Door lock")[:80], state="unknown", door="unknown")
            db.add(lock)
        locks.append(lock)
    db.commit()
    return locks


def read_state(db: Session, family: Family, lock: Lock) -> dict:
    """Returns {"state": locked|unlocked|jammed|None, "battery": int|None, "door": open|closed|None}."""
    main = _get(db, _integration(db, family.id), f"/devices/{lock.external_id}/status") \
        .get("components", {}).get("main", {})
    raw = str(main.get("lock", {}).get("lock", {}).get("value") or "").lower()
    state = {"locked": "locked", "unlocked": "unlocked", "unlocked with timeout": "unlocked",
             "unknown": None}.get(raw, "jammed" if "jam" in raw else None)
    battery = main.get("battery", {}).get("battery", {}).get("value")
    door = main.get("contactSensor", {}).get("contact", {}).get("value")
    return {"state": state, "battery": int(battery) if isinstance(battery, (int, float)) else None,
            "door": door if door in ("open", "closed") else None}


def command(db: Session, family: Family, lock: Lock, action: str) -> None:
    integ = _integration(db, family.id)
    with http.client() as c:
        resp = c.post(f"{API}/devices/{lock.external_id}/commands",
                      headers={"Authorization": f"Bearer {access_token(db, integ)}"},
                      json={"commands": [{"component": "main", "capability": "lock", "command": action}]})
        resp.raise_for_status()
