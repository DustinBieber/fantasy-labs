"""Alerts feed, notification channels, external account connections and the dashboard."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import decrypt_json, encrypt_json
from ..db import get_db
from ..models import Alert, Chore, ChoreInstance, Event, Homework, Integration, Lock, Member, NotificationChannel, Redemption
from ..security import current_member, get_owned, require_parent
from ..services import google, http, icloud
from ..services.chores import ensure_instances
from ..services.notify import CHANNEL_KINDS, REQUIRED_FIELDS, send
from ..timeutil import iso, local_now, utcnow
from .calendar import event_out
from .chores import instance_out
from .home import lock_out
from .members import member_out
from .records import homework_out

router = APIRouter(prefix="/api", tags=["alerts, integrations, dashboard"])


# ---------------------------------------------------------------- alerts

def _visible(q, me: Member):
    alerts = q.all()
    return [a for a in alerts if (not a.audience or me.role in a.audience)
            and (me.role == "parent" or a.member_id in (None, me.id))]


def alert_out(a: Alert, me: Member) -> dict:
    return {"id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title, "body": a.body,
            "member_id": a.member_id, "created_at": iso(a.created_at), "read": me.id in (a.read_by or []),
            "delivery": a.delivery if me.role == "parent" else {}}


@router.get("/alerts")
def list_alerts(unread_only: bool = False, limit: int = 100, me: Member = Depends(current_member),
                db: Session = Depends(get_db)):
    q = db.query(Alert).filter_by(family_id=me.family_id).order_by(Alert.created_at.desc()).limit(min(limit, 500))
    out = [alert_out(a, me) for a in _visible(q, me)]
    return [a for a in out if not a["read"]] if unread_only else out


@router.post("/alerts/{alert_id}/read")
def mark_read(alert_id: int, me: Member = Depends(current_member), db: Session = Depends(get_db)):
    a = get_owned(db, Alert, alert_id, me.family_id)
    if me.id not in (a.read_by or []):
        a.read_by = [*(a.read_by or []), me.id]
        db.commit()
    return {"ok": True}


@router.post("/alerts/read-all")
def mark_all_read(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    q = db.query(Alert).filter(Alert.family_id == me.family_id, Alert.created_at >= utcnow() - timedelta(days=60))
    for a in _visible(q, me):
        if me.id not in (a.read_by or []):
            a.read_by = [*(a.read_by or []), me.id]
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- notification channels

SECRET_FIELDS = {"token", "auth_token", "webhook_url", "password"}


def channel_out(ch: NotificationChannel) -> dict:
    cfg = decrypt_json(ch.config_enc)
    shown = {k: ("••••" + str(v)[-4:] if k in SECRET_FIELDS and v else v) for k, v in cfg.items()}
    return {"id": ch.id, "kind": ch.kind, "label": ch.label, "config": shown, "min_severity": ch.min_severity,
            "kinds": ch.kinds or [], "enabled": ch.enabled}


class ChannelIn(BaseModel):
    kind: str
    label: str = Field(min_length=1, max_length=80)
    config: dict
    min_severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    kinds: list[str] = Field(default_factory=list)
    enabled: bool = True


def _validate_channel(kind: str, config: dict) -> None:
    if kind not in CHANNEL_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(CHANNEL_KINDS)}")
    missing = [f for f in REQUIRED_FIELDS[kind] if not config.get(f)]
    if missing:
        raise HTTPException(422, f"Missing: {', '.join(missing)}")
    for list_field in ("to_numbers", "to"):
        if list_field in config and isinstance(config[list_field], str):
            config[list_field] = [x.strip() for x in config[list_field].split(",") if x.strip()]


@router.get("/notify/channels")
def list_channels(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    return [channel_out(c) for c in db.query(NotificationChannel).filter_by(family_id=me.family_id)]


@router.post("/notify/channels")
def add_channel(body: ChannelIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    _validate_channel(body.kind, body.config)
    ch = NotificationChannel(family_id=me.family_id, kind=body.kind, label=body.label,
                             config_enc=encrypt_json(body.config), min_severity=body.min_severity,
                             kinds=body.kinds, enabled=body.enabled)
    db.add(ch)
    db.commit()
    return channel_out(ch)


class ChannelPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    min_severity: str | None = Field(default=None, pattern="^(info|warning|critical)$")
    kinds: list[str] | None = None
    enabled: bool | None = None


@router.patch("/notify/channels/{ch_id}")
def update_channel(ch_id: int, body: ChannelPatch, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    ch = get_owned(db, NotificationChannel, ch_id, me.family_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ch, k, v)
    db.commit()
    return channel_out(ch)


@router.delete("/notify/channels/{ch_id}")
def delete_channel(ch_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    db.delete(get_owned(db, NotificationChannel, ch_id, me.family_id))
    db.commit()
    return {"ok": True}


@router.post("/notify/channels/{ch_id}/test")
def test_channel(ch_id: int, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    ch = get_owned(db, NotificationChannel, ch_id, me.family_id)
    probe = Alert(family_id=me.family_id, kind="system", severity="info", title="Test from Family Hub",
                  body=f"{me.name} sent a test to “{ch.label}”. It works!", audience=[])
    try:
        send(db, me.family, ch.kind, decrypt_json(ch.config_enc), probe)
    except Exception as exc:
        raise HTTPException(502, f"Couldn't deliver: {exc}") from exc
    return {"ok": True}


# ---------------------------------------------------------------- integrations

def integration_out(i: Integration | None) -> dict | None:
    if i is None:
        return None
    return {"provider": i.provider, "account": i.account, "options": i.options or {},
            "last_sync_at": iso(i.last_sync_at), "last_error": i.last_error}


@router.get("/integrations")
def list_integrations(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    rows = {i.provider: i for i in db.query(Integration).filter_by(family_id=me.family_id)}
    return {"google_configured": bool(settings.google_client_id and settings.google_client_secret),
            "google": integration_out(rows.get("google")), "icloud": integration_out(rows.get("icloud")),
            "homeassistant": integration_out(rows.get("homeassistant"))}


@router.get("/integrations/google/start")
def google_start(me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    try:
        return {"url": google.start_auth(db, me.family)}
    except google.GoogleNotConfigured as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/integrations/google/callback", include_in_schema=False)
def google_callback(state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    if error or not code:
        return RedirectResponse("/#connections/google?google=denied")
    try:
        integ = google.finish_auth(db, state, code)
    except Exception:
        return RedirectResponse("/#connections/google?google=failed")
    try:
        google.sync_calendar(db, integ)
    except Exception as exc:
        integ.last_error = str(exc)[:500]
        db.commit()
    return RedirectResponse("/#connections/google?google=connected")


class ICloudIn(BaseModel):
    username: str = Field(min_length=3, description="Apple ID email")
    password: str = Field(min_length=8, description="App-specific password, not your Apple ID password")
    calendar_name: str = ""
    url: str = ""  # leave blank for iCloud; set for other CalDAV servers


@router.post("/integrations/icloud")
def connect_icloud(body: ICloudIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    secret = {"username": body.username, "password": body.password, "url": body.url}
    options = {"calendar_name": body.calendar_name}
    try:
        name = icloud.verify(secret, options)
    except Exception as exc:
        raise HTTPException(400, f"Couldn't sign in to iCloud Calendar: {exc}") from exc
    integ = _upsert(db, me.family_id, "icloud", body.username, secret, {**options, "resolved_calendar": name})
    return integration_out(integ)


class HomeAssistantIn(BaseModel):
    url: str = Field(pattern=r"^https?://", description="e.g. http://homeassistant.local:8123")
    token: str = Field(min_length=20, description="Long-lived access token from your HA profile")


@router.post("/integrations/homeassistant")
def connect_homeassistant(body: HomeAssistantIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    url = body.url.rstrip("/")
    try:
        with http.client() as c:
            c.get(f"{url}/api/", headers={"Authorization": f"Bearer {body.token}"}).raise_for_status()
    except Exception as exc:
        raise HTTPException(400, f"Couldn't reach Home Assistant: {exc}") from exc
    integ = _upsert(db, me.family_id, "homeassistant", url, {"url": url, "token": body.token}, {})
    return integration_out(integ)


def _upsert(db: Session, family_id: int, provider: str, account: str, secret: dict, options: dict) -> Integration:
    integ = db.query(Integration).filter_by(family_id=family_id, provider=provider).first()
    if integ is None:
        integ = Integration(family_id=family_id, provider=provider)
        db.add(integ)
    integ.account, integ.secret_enc, integ.options, integ.last_error = account, encrypt_json(secret), options, ""
    db.commit()
    return integ


class OptionsIn(BaseModel):
    calendar_id: str | None = None
    calendar_name: str | None = None


@router.patch("/integrations/{provider}")
def update_options(provider: str, body: OptionsIn, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    integ = db.query(Integration).filter_by(family_id=me.family_id, provider=provider).first()
    if not integ:
        raise HTTPException(404, "Not connected")
    integ.options = {**(integ.options or {}), **body.model_dump(exclude_none=True)}
    db.commit()
    return integration_out(integ)


@router.delete("/integrations/{provider}")
def disconnect(provider: str, me: Member = Depends(require_parent), db: Session = Depends(get_db)):
    integ = db.query(Integration).filter_by(family_id=me.family_id, provider=provider).first()
    if integ:
        if provider == "google":
            try:
                token = decrypt_json(integ.secret_enc).get("refresh_token")
                with http.client() as c:
                    c.post("https://oauth2.googleapis.com/revoke", data={"token": token})
            except Exception:
                pass  # best effort; the local credentials are deleted either way
        if provider in ("google", "icloud"):
            db.query(Event).filter_by(family_id=me.family_id, source=provider).delete()
        db.delete(integ)
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- dashboard

@router.get("/dashboard")
def dashboard(me: Member = Depends(current_member), db: Session = Depends(get_db)):
    fam = me.family
    today = local_now(fam.timezone).date()
    ensure_instances(db, fam.id, today, today)
    chores_q = db.query(ChoreInstance).join(Chore).filter(Chore.family_id == fam.id, ChoreInstance.due_date == today)
    if me.role != "parent":
        chores_q = chores_q.filter((Chore.assignee_id == me.id) | Chore.assignee_id.is_(None))
    offset = local_now(fam.timezone).utcoffset() or timedelta()
    day_start = datetime.combine(today, datetime.min.time()) - offset
    events = db.query(Event).filter(Event.family_id == fam.id, Event.start >= day_start,
                                    Event.start < day_start + timedelta(days=2)).order_by(Event.start).all()
    hw_q = db.query(Homework).filter(Homework.family_id == fam.id, Homework.status != "done",
                                     Homework.due_date <= today + timedelta(days=3))
    if me.role != "parent":
        hw_q = hw_q.filter_by(member_id=me.id)
    unread = [a for a in _visible(db.query(Alert).filter(Alert.family_id == fam.id,
                                                         Alert.created_at >= utcnow() - timedelta(days=14)), me)
              if me.id not in (a.read_by or [])]
    out = {
        "me": member_out(db, me),
        "today": today.isoformat(),
        "chores": [instance_out(i) for i in chores_q.order_by(Chore.title)],
        "events": [event_out(e) for e in events],
        "homework": [homework_out(h) for h in hw_q.order_by(Homework.due_date)],
        "unread_alerts": len(unread),
        "critical_alerts": [alert_out(a, me) for a in unread if a.severity == "critical"][:5],
    }
    if me.role == "parent":
        out["members"] = [member_out(db, m) for m in fam.members]
        out["locks"] = [lock_out(lk) for lk in db.query(Lock).filter_by(family_id=fam.id)]
        out["pending_approvals"] = db.query(ChoreInstance).join(Chore).filter(
            Chore.family_id == fam.id, ChoreInstance.status == "submitted").count()
        out["pending_rewards"] = db.query(Redemption).filter_by(family_id=fam.id, status="pending").count()
        from ..connectors import CATALOG, Ctx

        states = []
        for c in CATALOG:
            try:
                states.append(c.status(Ctx(db, fam, c.key)).state)
            except Exception:
                states.append("attention")
        out["connections"] = {"connected": states.count("connected"), "attention": states.count("attention"),
                              "total": len(states)}
    return out
