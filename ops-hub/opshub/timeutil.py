"""Time helpers. The database stores naive UTC datetimes everywhere."""
from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` suffix allowed) to naive UTC."""
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        return to_utc_naive(datetime.fromisoformat(v))
    except ValueError:
        return None


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def end_of_day_utc(d: date, tz: str) -> datetime:
    """23:59:59 local time on ``d`` in ``tz``, as naive UTC."""
    local = datetime.combine(d, time(23, 59, 59), tzinfo=ZoneInfo(tz))
    return to_utc_naive(local)


def local_date(dt: datetime, tz: str) -> date:
    """The calendar date of a naive-UTC datetime in ``tz``."""
    return dt.replace(tzinfo=UTC).astimezone(ZoneInfo(tz)).date()


def to_local(dt: datetime, tz: str) -> datetime:
    """A naive-UTC datetime as an aware datetime in ``tz`` (for display)."""
    return dt.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))


def iso(dt: datetime | date | None) -> str | None:
    """Serialize for the API: naive UTC datetimes get a ``Z`` suffix."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.replace(microsecond=0).isoformat() + "Z"
    return dt.isoformat()
