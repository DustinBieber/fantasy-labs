import pytest

from opshub import db
from opshub.config import Settings, set_settings


@pytest.fixture
def settings():
    s = Settings(database_url="sqlite://", demo_mode=True, scheduler=False)
    set_settings(s)
    yield s
    set_settings(None)


@pytest.fixture
def session(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    with db.session_scope() as s:
        yield s
    db.Base.metadata.drop_all(db.get_engine())


@pytest.fixture
def synced(session, settings):
    """A database loaded with the full demo data set, rules run."""
    from opshub.services import sync

    result = sync.sync_all(session, settings, env={})
    assert all(r["status"] == "ok" for r in result["runs"]), result["runs"]
    return session
