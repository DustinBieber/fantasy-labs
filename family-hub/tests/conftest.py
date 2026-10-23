import os

from cryptography.fernet import Fernet

os.environ["FAMILY_HUB_DATABASE_URL"] = "sqlite://"
os.environ["FAMILY_HUB_SCHEDULER"] = "0"
os.environ["FAMILY_HUB_SECRET_KEY"] = Fernet.generate_key().decode()
os.environ["FAMILY_HUB_BASE_URL"] = "https://hub.example"

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.services import http as http_seam  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    auth_router._FAILS.clear()
    yield


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def outbound():
    """Captures outbound HTTP calls; tests register responses with outbound.routes[(method, prefix)]."""
    class Recorder:
        def __init__(self):
            self.calls: list[httpx.Request] = []
            self.routes: dict[tuple[str, str], httpx.Response | callable] = {}

        def handler(self, request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            for (method, prefix), resp in self.routes.items():
                if request.method == method and str(request.url).startswith(prefix):
                    return resp(request) if callable(resp) else resp
            return httpx.Response(200, json={})

    rec = Recorder()
    http_seam.transport = httpx.MockTransport(rec.handler)
    yield rec
    http_seam.transport = None


class Api:
    def __init__(self, client: TestClient, token: str | None = None):
        self.c, self.token = client, token

    def _h(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def get(self, url, **kw):
        return self.c.get(url, headers=self._h(), **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json, headers=self._h(), **kw)

    def patch(self, url, json=None):
        return self.c.patch(url, json=json, headers=self._h())

    def put(self, url, json=None):
        return self.c.put(url, json=json, headers=self._h())

    def delete(self, url):
        return self.c.delete(url, headers=self._h())


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def parent(client):
    r = client.post("/api/auth/signup", json={"family_name": "Rivera Family", "timezone": "America/Chicago",
                                              "parent_name": "Alex", "email": "alex@example.com",
                                              "password": "correct-horse"})
    assert r.status_code == 200, r.text
    return Api(client, r.json()["token"])


@pytest.fixture
def kid(client, parent):
    r = parent.post("/api/members", {"name": "Sam", "role": "child", "pin": "1234", "weekly_allowance_cents": 500})
    assert r.status_code == 200, r.text
    code = parent.get("/api/family").json()["join_code"]
    r = client.post("/api/auth/pin-login", json={"join_code": code, "member_id": r.json()["id"], "pin": "1234"})
    assert r.status_code == 200, r.text
    api = Api(client, r.json()["token"])
    api.id = r.json()["member"]["id"]
    return api
