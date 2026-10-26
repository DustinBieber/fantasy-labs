"""Free Web Push to installed home-screen apps (iPhone/iPad iOS 16.4+, Android, desktop) — no extra apps.

The server signs each push with a VAPID key pair it generates once and keeps (encrypted) in the database;
Apple's, Google's and Mozilla's push services deliver it at no cost.
"""
import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.orm import Session

from ..crypto import decrypt_json, encrypt_json
from ..models import Alert, Family, Member, PushSubscription, ServerKey

log = logging.getLogger(__name__)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def vapid_keys(db: Session) -> dict:
    row = db.get(ServerKey, "vapid")
    if row:
        return decrypt_json(row.value_enc)
    key = ec.generate_private_key(ec.SECP256R1())
    private_der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    public_raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
    keys = {"private": _b64url(private_der), "public": _b64url(public_raw)}
    db.add(ServerKey(name="vapid", value_enc=encrypt_json(keys)))
    db.commit()
    return keys


def recipients(db: Session, family: Family, alert: Alert) -> list[Member]:
    """Same visibility rules as the in-app feed."""
    from .notify import visible_to

    return [m for m in db.query(Member).filter_by(family_id=family.id) if visible_to(alert, m)]


def deliver(subscription: dict, payload: str, private_key: str, claims: dict) -> int:
    """Send one push; returns the push service's HTTP status. Tests replace this function."""
    from pywebpush import WebPushException, webpush

    try:
        resp = webpush(subscription, payload, vapid_private_key=private_key, vapid_claims=dict(claims),
                       ttl=24 * 3600, timeout=10, headers={"Urgency": "high"})
        return resp.status_code
    except WebPushException as exc:
        return exc.response.status_code if exc.response is not None else 0


def push_alert(db: Session, family: Family, alert: Alert) -> int:
    """Push an alert to every subscribed device that should see it. Returns the number delivered."""
    return _push(db, family, alert, recipients(db, family, alert))


def push_alert_to(db: Session, member: Member, alert: Alert) -> int:
    return _push(db, member.family, alert, [member])


def _push(db: Session, family: Family, alert: Alert, members: list[Member]) -> int:
    subs = db.query(PushSubscription).filter(PushSubscription.member_id.in_([m.id for m in members])).all()
    if not subs:
        return 0
    keys = vapid_keys(db)
    parent = db.query(Member).filter_by(family_id=family.id, role="parent").first()
    claims = {"sub": f"mailto:{parent.email}" if parent and parent.email else "mailto:family-hub@example.com"}
    payload = json.dumps({"title": alert.title, "body": alert.body, "kind": alert.kind,
                          "severity": alert.severity, "id": alert.id, **(alert.extra or {})})
    sent = 0
    for sub in subs:
        status = deliver({"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                         payload, keys["private"], claims)
        if status in (404, 410):  # the device unsubscribed or the app was removed
            db.delete(sub)
        elif 200 <= status < 300:
            sent += 1
        else:
            log.warning("Push to %s failed with %s", sub.endpoint[:60], status)
    db.commit()
    return sent
