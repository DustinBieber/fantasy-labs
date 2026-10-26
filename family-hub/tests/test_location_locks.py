from datetime import datetime, timedelta

import httpx

from app.models import Family, Lock
from app.services import locks
from app.timeutil import utcnow

HOME = (41.8781, -87.6298)
SCHOOL = (41.8900, -87.6200)


def _titles(api):
    return [a["title"] for a in api.get("/api/alerts").json()]


def test_geofence_transitions_with_baseline_and_hysteresis(parent, kid):
    parent.post("/api/geofences", {"name": "Home", "lat": HOME[0], "lng": HOME[1], "radius_m": 100})
    parent.post("/api/geofences", {"name": "School", "lat": SCHOOL[0], "lng": SCHOOL[1], "radius_m": 150})
    # First fix just sets the baseline.
    assert kid.post("/api/location", {"lat": HOME[0], "lng": HOME[1], "accuracy_m": 10}).json()["transitions"] == []
    # ~110 m away: inside the leave slack band, so no flapping.
    assert kid.post("/api/location", {"lat": HOME[0] + 0.001, "lng": HOME[1], "accuracy_m": 10}).json()["transitions"] == []
    r = kid.post("/api/location", {"lat": SCHOOL[0], "lng": SCHOOL[1], "accuracy_m": 10}).json()
    assert r["transitions"] == ["left Home", "arrived at School"]
    assert "Sam arrived at School" in _titles(parent)
    assert "Sam arrived at School" not in _titles(kid)  # location alerts are for parents
    latest = {p["name"]: p for p in parent.get("/api/location/latest").json()}
    assert latest["Sam"]["places"] == ["School"]
    assert [p["name"] for p in kid.get("/api/location/latest").json()] == ["Sam"]
    assert kid.get(f"/api/location/history/{latest['Alex']['member_id']}").status_code == 403


def test_inaccurate_fix_is_stored_but_ignored(parent, kid):
    parent.post("/api/geofences", {"name": "Home", "lat": HOME[0], "lng": HOME[1]})
    kid.post("/api/location", {"lat": HOME[0], "lng": HOME[1]})
    assert kid.post("/api/location", {"lat": SCHOOL[0], "lng": SCHOOL[1], "accuracy_m": 3000}).json()["transitions"] == []


def test_owntracks_style_location_webhook(client, parent, kid):
    hooks = parent.get("/api/family").json()["webhooks"]
    url = hooks["location"].replace("https://hub.example", "").replace("<member id>", str(kid.id))
    r = client.post(url, json={"_type": "location", "lat": HOME[0], "lon": HOME[1], "acc": 12, "batt": 77})
    assert r.status_code == 200 and r.json() == []
    last = {p["name"]: p for p in parent.get("/api/location/latest").json()}["Sam"]["last"]
    assert last["battery"] == 77
    assert client.post(url.replace(url.split("/")[3], "bogus"), json={"lat": 1, "lon": 1}).status_code == 404


def test_lock_webhook_events_and_alerts(client, parent, db):
    parent.patch("/api/family", {"settings": {"quiet_start": "00:00", "quiet_end": "23:59"}})
    parent.post("/api/locks", {"name": "Front Door", "provider": "webhook", "external_id": "front"})
    hook = parent.get("/api/family").json()["webhooks"]["lock"].replace("https://hub.example", "")
    r = client.post(hook, json={"lock": "Front Door", "event": "unlocked", "actor": "Keypad: Mom", "battery": 15})
    assert r.status_code == 200 and r.json()["state"] == "unlocked"
    titles = _titles(parent)
    assert "Front Door unlocked during quiet hours by Keypad: Mom" in titles
    assert "Front Door battery is low" in titles
    # Duplicate state reports don't spam.
    client.post(hook, json={"lock": "front", "event": "unlocked", "battery": 14})
    assert len(_titles(parent)) == 2
    client.post(hook, json={"lock": "front", "event": "jammed"})
    assert any(a["severity"] == "critical" for a in parent.get("/api/alerts").json())
    assert client.post(hook, json={"lock": "front", "event": "exploded"}).status_code == 422
    assert client.post(hook, json={"lock": "front", "event": "locking"}).json()["state"] == "jammed"  # ignored
    assert client.post(hook, json={"lock": "front", "event": "open"}).json()["door"] == "open"
    assert client.post(hook, json={"lock": "back", "event": "locked"}).status_code == 404
    events = parent.get(f"/api/locks/{r.json()['id']}/events").json()
    assert [e["event"] for e in events] == ["door_open", "jammed", "unlocked"]


def test_webhook_lock_cannot_be_controlled(parent):
    lk = parent.post("/api/locks", {"name": "Front", "provider": "webhook", "external_id": "f"}).json()
    r = parent.post(f"/api/locks/{lk['id']}/command", {"action": "unlock"})
    assert r.status_code == 400 and "Philips app" in r.json()["detail"]


def test_left_unlocked_alert_fires_once(parent, db):
    lk = parent.post("/api/locks", {"name": "Front", "provider": "simulated", "external_id": "sim"}).json()
    parent.post(f"/api/locks/{lk['id']}/command", {"action": "unlock"})
    fam = db.query(Family).one()
    later = utcnow() + timedelta(minutes=11)
    locks.check_stale(db, fam, later)
    locks.check_stale(db, fam, later + timedelta(minutes=5))
    titles = [t for t in _titles(parent) if "has been unlocked" in t]
    assert len(titles) == 1
    parent.post(f"/api/locks/{lk['id']}/command", {"action": "lock"})
    assert parent.get("/api/locks").json()[0]["state"] == "locked"


def test_nobody_home_and_unlocked_is_critical(parent, kid, db):
    parent.post("/api/geofences", {"name": "Home", "lat": HOME[0], "lng": HOME[1], "radius_m": 100})
    lk = parent.post("/api/locks", {"name": "Front", "provider": "simulated", "external_id": "sim"}).json()
    for api in (parent, kid):
        api.post("/api/location", {"lat": HOME[0], "lng": HOME[1], "accuracy_m": 5})
        api.post("/api/location", {"lat": SCHOOL[0], "lng": SCHOOL[1], "accuracy_m": 5})
    parent.post(f"/api/locks/{lk['id']}/command", {"action": "unlock"})
    locks.check_stale(db, db.query(Family).one())
    alert = next(a for a in parent.get("/api/alerts").json() if a["kind"] == "door")
    assert alert["severity"] == "critical" and "Nobody is home" in alert["title"]


def test_quiet_hours_wraps_midnight():
    fam = Family(name="f", timezone="UTC", settings={"quiet_start": "22:00", "quiet_end": "06:00"})
    assert locks.in_quiet_hours(fam, datetime(2026, 1, 1, 23, 30))
    assert locks.in_quiet_hours(fam, datetime(2026, 1, 1, 5, 59))
    assert not locks.in_quiet_hours(fam, datetime(2026, 1, 1, 12, 0))


def test_home_assistant_lock_control_and_poll(parent, outbound, db):
    outbound.routes[("GET", "http://ha.local:8123/api/states/lock.front_door")] = httpx.Response(
        200, json={"state": "locked", "attributes": {"battery_level": 90}})
    r = parent.post("/api/integrations/homeassistant", {"url": "http://ha.local:8123/", "token": "x" * 40})
    assert r.status_code == 200, r.text
    lk = parent.post("/api/locks", {"name": "Front", "provider": "homeassistant", "external_id": "lock.front_door"}).json()
    assert lk["state"] == "locked" and lk["battery"] == 90
    r = parent.post(f"/api/locks/{lk['id']}/command", {"action": "unlock"})
    assert r.json()["state"] == "unlocked"
    svc = [c for c in outbound.calls if c.url.path == "/api/services/lock/unlock"]
    assert len(svc) == 1 and b"lock.front_door" in svc[0].content
    assert svc[0].headers["authorization"] == "Bearer " + "x" * 40
    assert db.get(Lock, lk["id"]).state == "unlocked"
