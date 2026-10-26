"""Time helpers. Storage is UTC epoch seconds; display is always Pacific/Honolulu."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo

from .config import LOCAL_TZ

HST = ZoneInfo(LOCAL_TZ)


def now_ts() -> int:
    return int(time.time())


def parse_iso(value: Optional[str]) -> Optional[int]:
    """Parse an ISO-8601 string (with Z or offset) to epoch seconds."""
    if not value:
        return None
    try:
        v = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return None


def hst(ts: Optional[Union[int, float]], fmt: str = "%a %b %d %I:%M %p HST") -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=HST).strftime(fmt)


def hst_date(ts: Optional[Union[int, float]] = None) -> str:
    return datetime.fromtimestamp(float(ts if ts is not None else time.time()), tz=HST).strftime("%Y-%m-%d")


def age_minutes(ts: Optional[Union[int, float]]) -> Optional[float]:
    if ts is None:
        return None
    return round((time.time() - float(ts)) / 60.0, 1)
