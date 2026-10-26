"""Credentials and IDs saved from the Setup page.

They live in the database (key ``setting:NAME``). A non-empty environment variable or .env
entry with the same name always wins, so a deployment can pin values in .env.
"""
from __future__ import annotations

import os
import threading

PREFIX = "setting:"
_lock = threading.Lock()
_cache: dict[str, str] | None = None


def saved() -> dict[str, str]:
    """Values saved from the Setup page (cached until the next save)."""
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
    try:
        from sqlalchemy import select

        from . import db
        from .models import KeyValue

        s = db.new_session()
        try:
            rows = s.scalars(select(KeyValue).where(KeyValue.key.like(PREFIX + "%")))
            values = {r.key[len(PREFIX):]: r.value for r in rows if r.value}
        finally:
            s.close()
    except Exception:  # no database yet: nothing saved
        return {}
    with _lock:
        _cache = values
    return dict(values)


def effective_env() -> dict[str, str]:
    """Saved settings overlaid with non-empty environment variables."""
    return {**saved(), **{k: v for k, v in os.environ.items() if v}}


def from_env(key: str) -> bool:
    return bool(os.environ.get(key))


def save(values: dict[str, str | None]) -> list[str]:
    """Save (or, with an empty value, remove) settings. Returns keys skipped because .env sets them."""
    from . import db
    from .models import KeyValue
    from .timeutil import utcnow

    skipped = []
    with db.session_scope() as s:
        for key, value in values.items():
            if from_env(key):
                skipped.append(key)
                continue
            row = s.get(KeyValue, PREFIX + key)
            value = (value or "").strip()
            if not value:
                if row is not None:
                    s.delete(row)
            elif row is None:
                s.add(KeyValue(key=PREFIX + key, value=value, updated_at=utcnow()))
            else:
                row.value, row.updated_at = value, utcnow()
    invalidate()
    return skipped


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None
