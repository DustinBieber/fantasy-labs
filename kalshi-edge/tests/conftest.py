import os

os.environ["SCHEDULER_ENABLED"] = "false"

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    eng = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.set_engine(eng)
    from app.services import edges
    from app.sources import http
    edges._cache.update(ts=0.0, data=None)
    monkeypatch.setattr(http, "MIN_INTERVAL", {})
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    yield eng
    http.set_transport(None)
