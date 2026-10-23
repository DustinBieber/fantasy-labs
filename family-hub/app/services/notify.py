"""Alerts: stored in-app, then fanned out to the family's notification channels.

Every alert is also sent as a free Web Push notification to the home-screen app on each family
member's phone that should see it (see services/push.py).

Channel kinds and why they exist:
  imessage    - iMessage (or SMS through a paired iPhone) sent by Messages.app on the Mac running the hub.
  ntfy        - free push notifications to the ntfy app on iPhone and Android.
  twilio_sms  - plain SMS, which lands in Google Messages on Android and Messages on iPhone.
                (Neither Google Messages nor iMessage offers a public sending API.)
  gmail       - email sent from the connected Google account via the Gmail API.
  google_chat - posts into a Google Chat space through an incoming webhook (Workspace accounts).
  webhook     - JSON POST to any URL: Home Assistant, IFTTT, Pushcut (iOS Shortcuts), Zapier, etc.
"""
import base64
import logging
from email.message import EmailMessage

from sqlalchemy.orm import Session

from ..crypto import decrypt_json
from ..models import Alert, Family, Integration, NotificationChannel
from . import http

log = logging.getLogger(__name__)

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
CHANNEL_KINDS = ("imessage", "ntfy", "twilio_sms", "gmail", "google_chat", "webhook")


def raise_alert(db: Session, family: Family, kind: str, title: str, body: str = "", *,
                severity: str = "info", member_id: int | None = None,
                audience: list[str] | None = None) -> Alert:
    alert = Alert(family_id=family.id, kind=kind, severity=severity, title=title, body=body,
                  member_id=member_id, audience=audience or [], read_by=[], delivery={})
    db.add(alert)
    db.flush()
    channels = db.query(NotificationChannel).filter_by(family_id=family.id, enabled=True).all()
    if alert.audience == ["child"]:
        channels = []  # kid-only notices (e.g. "chore approved") stay in the app
    delivery = {}
    for ch in channels:
        if SEVERITY_RANK[severity] < SEVERITY_RANK.get(ch.min_severity, 1):
            continue
        if ch.kinds and kind not in ch.kinds:
            continue
        try:
            send(db, family, ch.kind, decrypt_json(ch.config_enc), alert)
            delivery[str(ch.id)] = "sent"
        except Exception as exc:  # one broken channel must not block the others
            log.warning("Channel %s (%s) failed: %s", ch.id, ch.kind, exc)
            delivery[str(ch.id)] = f"error: {exc}"[:300]
    alert.delivery = delivery
    db.commit()
    min_push = (family.settings or {}).get("push_min_severity", "info")
    if SEVERITY_RANK[severity] >= SEVERITY_RANK.get(min_push, 0):
        try:
            from .push import push_alert  # avoid import cycle

            alert.delivery = {**delivery, "push": push_alert(db, family, alert)}
            db.commit()
        except Exception as exc:
            db.rollback()
            log.warning("Web Push failed: %s", exc)
    return alert


def send(db: Session, family: Family, kind: str, config: dict, alert: Alert) -> None:
    sender = {
        "imessage": _send_imessage,
        "ntfy": _send_ntfy,
        "twilio_sms": _send_twilio,
        "gmail": _send_gmail,
        "google_chat": _send_google_chat,
        "webhook": _send_webhook,
    }.get(kind)
    if sender is None:
        raise ValueError(f"Unknown channel kind {kind}")
    sender(db, family, config, alert)


def _send_imessage(db, family, config, alert):
    from . import macos

    icon = {"critical": "🚨 ", "warning": "⚠️ "}.get(alert.severity, "")
    text = f"{icon}{alert.title}" + (f"\n{alert.body}" if alert.body else "") + f"\n— {family.name} Hub"
    for recipient in config["to"]:
        macos.send_message(recipient, text, config.get("service", "auto"))


def _send_ntfy(db, family, config, alert):
    # JSON publishing keeps titles UTF-8 safe (headers would choke on emoji and curly quotes).
    server = (config.get("server") or "https://ntfy.sh").rstrip("/")
    headers = {"Authorization": f"Bearer {config['token']}"} if config.get("token") else {}
    tag = {"door": "door", "location": "round_pushpin", "chore": "broom", "reward": "gift", "health": "pill",
           "finance": "moneybag", "homework": "books"}.get(alert.kind, "house")
    with http.client() as c:
        c.post(server, headers=headers, json={
            "topic": config["topic"], "title": alert.title, "message": alert.body or alert.title,
            "priority": {"info": 3, "warning": 4, "critical": 5}[alert.severity], "tags": [tag],
        }).raise_for_status()


def _send_twilio(db, family, config, alert):
    sid = config["account_sid"]
    text = f"{family.name}: {alert.title}" + (f"\n{alert.body}" if alert.body else "")
    with http.client() as c:
        for number in config["to_numbers"]:
            c.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                   auth=(sid, config["auth_token"]),
                   data={"From": config["from_number"], "To": number, "Body": text[:1500]}).raise_for_status()


def _send_gmail(db, family, config, alert):
    from .google import gmail_send  # avoid import cycle

    integ = db.query(Integration).filter_by(family_id=family.id, provider="google").first()
    if not integ:
        raise RuntimeError("Google account not connected")
    msg = EmailMessage()
    msg["To"] = ", ".join(config["to"])
    msg["Subject"] = f"[{family.name}] {alert.title}"
    msg.set_content(alert.body or alert.title)
    gmail_send(db, integ, base64.urlsafe_b64encode(msg.as_bytes()).decode())


def _send_google_chat(db, family, config, alert):
    icon = {"critical": "🚨", "warning": "⚠️"}.get(alert.severity, "🏠")
    text = f"{icon} *{alert.title}*" + (f"\n{alert.body}" if alert.body else "")
    with http.client() as c:
        c.post(config["webhook_url"], json={"text": text}).raise_for_status()


def _send_webhook(db, family, config, alert):
    with http.client() as c:
        c.post(config["url"], json={
            "family": family.name, "kind": alert.kind, "severity": alert.severity,
            "title": alert.title, "body": alert.body, "member_id": alert.member_id,
        }).raise_for_status()


REQUIRED_FIELDS = {
    "imessage": ["to"],
    "ntfy": ["topic"],
    "twilio_sms": ["account_sid", "auth_token", "from_number", "to_numbers"],
    "gmail": ["to"],
    "google_chat": ["webhook_url"],
    "webhook": ["url"],
}
