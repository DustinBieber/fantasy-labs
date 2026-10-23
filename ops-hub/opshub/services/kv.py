"""Persistent key-value state (rotated tokens, poll offsets, daily job runs).

Pass the caller's session when there is one, so the write lands in the same transaction.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import db
from ..models import KeyValue
from ..timeutil import utcnow


def get(key: str, default: str | None = None, session: Session | None = None) -> str | None:
    if session is not None:
        row = session.get(KeyValue, key)
        return row.value if row else default
    s = db.new_session()  # read only: no commit
    try:
        return get(key, default, s)
    finally:
        s.close()


def set(key: str, value: str, session: Session | None = None) -> None:  # noqa: A001 - mirrors get/set
    if session is None:
        with db.session_scope() as s:
            return set(key, value, s)
    row = session.get(KeyValue, key)
    if row is None:
        session.add(KeyValue(key=key, value=value, updated_at=utcnow()))
    else:
        row.value, row.updated_at = value, utcnow()
    session.flush()
