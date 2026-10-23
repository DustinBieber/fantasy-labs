import base64

import pytest
from fastapi.testclient import TestClient

from opshub import db
from opshub.api import create_app


@pytest.fixture
def client(settings, monkeypatch):
    for key in ("OPSHUB_PASSWORD", "SYNC_INTERVAL_MINUTES"):
        monkeypatch.delenv(key, raising=False)
    db.init_engine("sqlite://")
    with TestClient(create_app()) as c:
        yield c
    db.Base.metadata.drop_all(db.get_engine())


def test_dashboard_and_sync(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    r = client.post("/api/sync").json()
    assert {x["status"] for x in r["runs"]} == {"ok"}
    ov = client.get("/api/overview").json()
    assert ov["demo_mode"] and ov["orders"]["overdue"] == 1 and ov["billing"]["open_overbilled"] == "522.50"
    watch = client.get("/api/orders?view=watch").json()
    assert watch[0]["number"] == "#2048"  # most overdue first
    assert client.get("/api/orders?view=hazmat").json()[0]["number"] == "#2043"
    for path in ("/api/inventory/stock", "/api/inventory/sell-through", "/api/inventory/lots", "/api/shipments",
                 "/api/billing/invoices", "/api/billing/rate-card", "/api/connectors", "/api/notifications",
                 "/api/calendar/events", "/api/digest", "/api/export/stock.csv", "/api/export/watchlist.csv"):
        assert client.get(path).status_code == 200, path
    plan = client.get("/api/inventory/allocate", params={"sku": "BAR-ENR", "qty": 24, "location": "P3PL-01",
                                                         "min_days": 90}).json()
    assert plan["shortfall"] == 14


def test_manual_handoff_and_invoice_review(client):
    client.post("/api/sync")
    ship = next(s for s in client.get("/api/shipments").json() if s["reference"] == "TR-HQ-0921")
    assert ship["stale"] and ship["late"]
    d = client.post(f"/api/shipments/{ship['id']}/updates", json={
        "status": "in_transit", "description": "Driver called", "new_carrier": "Prairie Line",
        "new_tracking_ref": "PL-1", "eta": "2099-01-01"}).json()
    assert (d["carrier"], d["tracking_ref"], d["stale"], d["late"]) == ("Prairie Line", "PL-1", False, False)

    inv = next(i for i in client.get("/api/billing/invoices").json() if i["overbilled"] != "0.00")
    assert client.post(f"/api/billing/invoices/{inv['id']}/status", json={"status": "bogus"}).status_code == 400
    assert client.post(f"/api/billing/invoices/{inv['id']}/status", json={"status": "approved"}).json()["status"] == "approved"
    assert client.get("/api/overview").json()["billing"]["open_overbilled"] == "0.00"

    alert = client.get("/api/alerts").json()[0]
    assert client.post(f"/api/alerts/{alert['id']}/ack").json()["status"] == "acked"
    assert client.get("/api/orders/999999").status_code == 404


def test_basic_auth(client, monkeypatch):
    monkeypatch.setenv("OPSHUB_PASSWORD", "s3cret")
    assert client.get("/api/overview").status_code == 401
    assert client.get("/healthz").status_code == 200
    good = base64.b64encode(b"ops:s3cret").decode()
    bad = base64.b64encode(b"ops:nope").decode()
    assert client.get("/api/overview", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    assert client.get("/api/overview", headers={"Authorization": f"Basic {good}"}).status_code == 200
