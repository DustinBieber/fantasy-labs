"""Engine and session setup. SQLite by default; set DATABASE_URL for Postgres."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import Numeric, String, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class Dec(TypeDecorator):
    """Exact decimal: NUMERIC on real databases, text on SQLite (which has no decimal type)."""

    impl = Numeric(14, 4)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(Numeric(14, 4))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        d = Decimal(str(value))
        return str(d) if dialect.name == "sqlite" else d

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(str(value))


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def init_engine(url: str) -> Engine:
    global _engine, _session_factory
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        from .config import get_settings

        init_engine(get_settings().database_url)
    assert _engine is not None
    return _engine


def create_all() -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(get_engine())


def new_session() -> Session:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()
