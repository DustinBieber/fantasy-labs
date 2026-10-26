"""Send alerts to chat channels, routed by severity (ALERT_ROUTES)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..connectors import notifiers
from ..models import Alert, Notification
from .rules import SEVERITY_RANK

log = logging.getLogger(__name__)
ICON = {"urgent": "\U0001F534", "warn": "\U0001F7E0", "info": "\U0001F535"}


def format_alert(a: Alert) -> str:
    return f"{ICON.get(a.severity, '')} {a.severity.upper()} - {a.title}\n{a.detail or ''}".strip()


def deliver(session: Session, settings: Settings, channel: str, text: str, alert_id: int | None = None,
            env: dict[str, str] | None = None) -> Notification | None:
    """Send on one channel. Unconfigured channels are logged as 'demo' in demo mode and skipped otherwise."""
    notifier = notifiers(env).get(channel)
    if notifier is None:
        return None
    if notifier.send_ready():
        try:
            notifier.send(text)
            status, error = "sent", None
        except Exception as exc:
            log.warning("Sending to %s failed: %s", channel, exc)
            status, error = "failed", str(exc)[:500]
    elif settings.demo_mode:
        status, error = "demo", None
    else:
        return None
    n = Notification(channel=channel, alert_id=alert_id, body=text, status=status, error=error)
    session.add(n)
    return n


def dispatch(session: Session, settings: Settings, env: dict[str, str] | None = None) -> dict:
    """Notify open alerts that are new or more severe than when last notified."""
    stats = {"alerts": 0, "messages": 0, "failed": 0}
    for a in session.scalars(select(Alert).where(Alert.status == "open")):
        if a.notified_severity and SEVERITY_RANK[a.severity] <= SEVERITY_RANK.get(a.notified_severity, 0):
            continue
        channels = settings.alert_routes.get(a.severity, [])
        stats["alerts"] += 1
        failed = False
        for channel in channels:
            n = deliver(session, settings, channel, format_alert(a), a.id, env)
            if n is not None:
                stats["messages"] += 1
                failed = failed or n.status == "failed"
        stats["failed"] += int(failed)
        if not failed:  # failed sends retry on the next dispatch
            a.notified_severity = a.severity
    session.flush()
    return stats


def digest_text(session: Session) -> str:
    alerts = list(session.scalars(select(Alert).where(Alert.status.in_(("open", "acked")))))
    if not alerts:
        return "Ops Hub digest: nothing open. ✅"
    lines = [f"Ops Hub digest: {len(alerts)} open item(s)"]
    for sev in ("urgent", "warn", "info"):
        group = [a for a in alerts if a.severity == sev]
        if group:
            lines.append(f"\n{ICON[sev]} {sev.upper()} ({len(group)})")
            lines += [f"  - {a.title}" for a in sorted(group, key=lambda a: a.kind)[:25]]
    return "\n".join(lines)
