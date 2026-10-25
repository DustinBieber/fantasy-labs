import os

# Tests never depend on a local .env: pin everything config reads.
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["KALSHI_API_BASE"] = "https://external-api.kalshi.com/trade-api/v2"
os.environ["ODDS_API_KEY"] = ""

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
