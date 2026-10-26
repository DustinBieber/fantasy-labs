"""All datetimes are stored as naive UTC; families carry an IANA timezone for local-time rules."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def local_now(tz: str, now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz))


def local_today(tz: str, now: datetime | None = None) -> date:
    return local_now(tz, now).date()


def iso(dt: datetime | None) -> str | None:
    """Serialize a stored naive-UTC datetime with an explicit Z so browsers parse it as UTC."""
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + "Z"
