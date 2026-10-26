"""The free, Mac-hosted features: Web Push, iMessage, presence, Apple Home locks, local files, backups."""
import sqlite3
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect

from app.config import settings
from app.db import _add_missing_columns
from app.models import Family, Member, PushSubscription
from app.services import macos, presence, push, scheduler
from app.timeutil import utcnow

HOME = (41.8781, -87.6298)


@pytest.fixture
def pushes(monkeypatch):
    sent = []

    def fake(sub, payload, key, claims):
        sent.append((sub["endpoint"], payload))
        return 410 if "gone" in sub["endpoint"] else 201

    monkeypatch.setattr(push, "deliver", fake)
    return sent


def _subscribe(api, endpoint):
    r = api.post("/api/push/subscribe", {"endpoint": endpoint, "keys": {"p256dh": "BPk", "auth": "au"}})
    assert r.status_code == 200, r.text


def test_web_push_goes_to_the_right_people(client, parent, kid, pushes, db):
    key = client.get("/api/push/key").json()["public_key"]
    assert len(key) == 87 and client.get("/api/push/key").json()["public_key"] == key  # stable P-256 point
    _subscribe(parent, "https://web.push.apple.com/parent-phone")
    _subscribe(kid, "https://fcm.googleapis.com/kid-phone")
    _subscribe(parent, "https://web.push.apple.com/gone-device")

    parent.post("/api/chores", {"title": "Dishes", "assignee_id": kid.id})
    [inst] = kid.get("/api/chores/instances").json()
    kid.post(f"/api/chores/instances/{inst['id']}/complete")  # parent-only alert
    assert [e for e, _ in pushes] == ["https://web.push.apple.com/parent-phone", "https://web.push.apple.com/gone-device"]
    assert "Dishes" in pushes[0][1]
    assert db.query(PushSubscription).filter_by(endpoint="https://web.push.apple.com/gone-device").count() == 0

    pushes.clear()
    parent.post(f"/api/chores/instances/{inst['id']}/review", {"approve": True})  # kid-only notice
    assert [e for e, _ in pushes] == ["https://fcm.googleapis.com/kid-phone"]

    pushes.clear()
    assert kid.post("/api/push/test").json() == {"sent": 1}
    assert parent.post("/api/push/subscribe", {"endpoint": "http://insecure", "keys": {"p256dh": "a", "auth": "b"}}).status_code == 422


def test_push_respects_family_minimum(parent, pushes):
    _subscribe(parent, "https://web.push.apple.com/p")
    parent.patch("/api/family", {"settings": {"push_min_severity": "critical"}})
    hook = parent.get("/api/family").json()["webhooks"]["alert"].replace("https://hub.example", "")
    parent.c.post(hook, json={"title": "FYI", "severity": "info"})
    parent.c.post(hook, json={"title": "Water leak!", "severity": "critical"})
    assert [p for _, p in pushes if "FYI" in p] == [] and len(pushes) == 1


def test_imessage_channel(parent, monkeypatch):
    calls = []
    monkeypatch.setattr(macos, "is_mac", lambda: True)

    def fake_run(cmd, timeout=30):
        calls.append(cmd)
        if cmd[0] == "osascript" and cmd[-1] == "imessage" and cmd[-3] == "+15550002":
            raise RuntimeError("not on iMessage")
        return ""

    monkeypatch.setattr(macos, "run", fake_run)
    r = parent.post("/api/notify/channels", {"kind": "imessage", "label": "Texts", "min_severity": "info",
                                             "config": {"to": "+15550001, +15550002"}})
    assert r.json()["config"]["to"] == ["+15550001", "+15550002"]
    assert parent.post(f"/api/notify/channels/{r.json()['id']}/test").status_code == 200
    # First recipient by iMessage; second falls back to SMS.
    assert [(c[-3], c[-1]) for c in calls] == [("+15550001", "imessage"), ("+15550002", "imessage"), ("+15550002", "sms")]
    assert "Test from Family Hub" in calls[0][-2]


def test_imessage_off_mac_fails_cleanly(parent, monkeypatch):
    monkeypatch.setattr(macos, "is_mac", lambda: False)
    ch = parent.post("/api/notify/channels", {"kind": "imessage", "label": "T", "config": {"to": "+1555"}}).json()
    r = parent.post(f"/api/notify/channels/{ch['id']}/test")
    assert r.status_code == 502 and "Mac" in r.json()["detail"]


def test_presence_webhook_for_iphone_shortcuts(client, parent, kid):
    parent.post("/api/geofences", {"name": "Home", "lat": HOME[0], "lng": HOME[1]})
    base = parent.get("/api/family").json()["hook_base"].replace("https://hub.example", "")
    r = client.get(f"{base}/presence", params={"member": kid.id, "place": "home", "event": "leave"})
    assert r.json() == {"ok": True, "changed": True}
    r = client.post(f"{base}/presence?member={kid.id}", json={"place": "Home", "event": "arrive"})
    assert r.json()["changed"] is True
    assert client.get(f"{base}/presence", params={"member": kid.id, "event": "arrive"}).json()["changed"] is False
    titles = [a["title"] for a in parent.get("/api/alerts").json()]
    assert titles == ["Sam arrived at Home", "Sam left Home"]
    assert client.get(f"{base}/presence", params={"member": kid.id, "place": "Mars", "event": "arrive"}).status_code == 404
    assert client.get(f"{base}/presence", params={"member": kid.id, "event": "dance"}).status_code == 422


MAC_ARP = """? (192.168.1.1) at 0:11:22:33:44:55 on en0 ifscope [ethernet]
sams-iphone.lan (192.168.1.23) at 4a:b:c:d:e:f on en0 ifscope [ethernet]
? (192.168.1.40) at (incomplete) on en0 ifscope [ethernet]
? (192.168.1.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]
? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]
"""


def test_parse_arp_and_normalize():
    rows = presence.parse_arp(MAC_ARP)
    assert rows == [{"ip": "192.168.1.1", "mac": "00:11:22:33:44:55", "name": ""},
                    {"ip": "192.168.1.23", "mac": "4a:0b:0c:0d:0e:0f", "name": "sams-iphone.lan"}]
    assert presence.normalize_mac("4A-0B-0C-0D-0E-0F") == "4a:0b:0c:0d:0e:0f"
    with pytest.raises(ValueError):
        presence.normalize_mac("not-a-mac")


def test_wifi_presence(parent, kid, db):
    parent.post("/api/geofences", {"name": "Home", "lat": HOME[0], "lng": HOME[1]})
    assert parent.patch(f"/api/members/{kid.id}", {"wifi_macs": ["4A:B:C:D:E:F"]}).json()["wifi_macs"] == ["4a:0b:0c:0d:0e:0f"]
    assert parent.patch(f"/api/members/{kid.id}", {"wifi_macs": ["nope"]}).status_code == 422
    fam = db.query(Family).one()
    t0 = utcnow()
    assert presence.update(db, fam, {"4a:0b:0c:0d:0e:0f"}, t0) == ["Sam arrived at Home"]
    assert presence.update(db, fam, set(), t0 + timedelta(minutes=5)) == []  # phone napping, not gone
    db.expire_all()
    assert presence.update(db, fam, set(), utcnow() + timedelta(minutes=16)) == ["Sam left Home"]
    assert db.get(Member, kid.id).wifi_last_seen is not None


def test_apple_home_lock(parent, monkeypatch, db):
    ran = []
    shortcuts = {"Lock Front Door", "Unlock Front Door"}
    monkeypatch.setattr(macos, "is_mac", lambda: True)
    monkeypatch.setattr(macos, "list_shortcuts", lambda: sorted(shortcuts))
    monkeypatch.setattr(macos, "run_shortcut", lambda name, timeout=60: ran.append(name) or ("Unlocked" if name.startswith("Status") else ""))
    r = parent.post("/api/locks", {"name": "Front", "provider": "apple_home", "external_id": "Back Door"})
    assert r.status_code == 422 and "Lock Back Door" in r.json()["detail"]
    lk = parent.post("/api/locks", {"name": "Front", "provider": "apple_home", "external_id": "Front Door"}).json()
    assert lk["can_control"]
    assert parent.post(f"/api/locks/{lk['id']}/command", {"action": "lock"}).json()["state"] == "locked"
    assert ran == ["Lock Front Door"]
    shortcuts.add("Status Front Door")
    from app.services import locks
    locks.poll(db, db.query(Family).one())
    assert parent.get("/api/locks").json()[0]["state"] == "unlocked"
    assert [locks.parse_state(x) for x in ("Secured", "0", "Jammed", "", "huh")] == ["locked", "unlocked", "jammed", None, None]


def test_lock_webhook_accepts_plain_url(client, parent):
    parent.post("/api/locks", {"name": "Front Door", "provider": "webhook", "external_id": "front"})
    hook = parent.get("/api/family").json()["webhooks"]["lock"].replace("https://hub.example", "")
    r = client.get(hook, params={"lock": "Front Door", "event": "unlocked", "battery": "55"})
    assert r.status_code == 200 and r.json()["state"] == "unlocked" and r.json()["battery"] == 55
    assert client.get(hook, params={"event": "unlocked"}).status_code == 422


def test_local_file_storage(parent, kid, tmp_path):
    object.__setattr__(settings, "files_dir", str(tmp_path))
    try:
        hw = parent.post("/api/homework", {"member_id": kid.id, "subject": "Art", "title": "Sketch",
                                           "due_date": "2026-10-30"}).json()
        r = kid.post(f"/api/attachments/homework/{hw['id']}", files={"file": ("../my drawing.png", b"PNGDATA", "image/png")})
        assert r.status_code == 200, r.text
        [att] = r.json()["attachments"]
        assert att["name"] == "../my drawing.png" and att["url"].startswith("/api/files/homework/")
        got = parent.c.get(att["url"])
        assert got.status_code == 200 and got.content == b"PNGDATA"
        assert parent.c.get(att["url"].replace("sig=", "sig=0")).status_code == 404
        assert parent.c.get("/api/files/../../etc/passwd?sig=x").status_code == 404
    finally:
        object.__setattr__(settings, "files_dir", "./files")


def test_daily_backup(tmp_path):
    src = tmp_path / "live.db"
    with sqlite3.connect(src) as c:
        c.execute("create table t (x)")
        c.execute("insert into t values (42)")
    old = settings.database_url, settings.backup_dir
    object.__setattr__(settings, "database_url", f"sqlite:///{src}")
    object.__setattr__(settings, "backup_dir", str(tmp_path / "backups"))
    try:
        now = utcnow()
        for i in range(16):
            scheduler.backup(now + timedelta(days=i))
        assert scheduler.backup(now + timedelta(days=15)) is None  # already done today
        files = sorted((tmp_path / "backups").glob("*.db"))
        assert len(files) == 14
        with sqlite3.connect(files[-1]) as c:
            assert c.execute("select x from t").fetchone() == (42,)
    finally:
        object.__setattr__(settings, "database_url", old[0])
        object.__setattr__(settings, "backup_dir", old[1])


def test_upgrade_adds_new_columns(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.exec_driver_sql("create table members (id integer primary key, name varchar(80))")
    _add_missing_columns(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("members")}
    assert {"wifi_macs", "wifi_last_seen", "share_location"} <= cols


def test_failed_wifi_scan_is_reported_not_treated_as_away(parent, kid, db, monkeypatch):
    parent.post("/api/geofences", {"name": "Home", "lat": HOME[0], "lng": HOME[1]})
    parent.patch(f"/api/members/{kid.id}", {"wifi_macs": ["4a:0b:0c:0d:0e:0f"]})
    parent.patch("/api/family", {"settings": {"wifi_presence": True, "wifi_away_minutes": 0}})
    fam = db.query(Family).one()
    presence.update(db, fam, {"4a:0b:0c:0d:0e:0f"}, utcnow() - timedelta(hours=1))
    monkeypatch.setattr(presence, "_cache", {"at": 0.0, "seen": set()})

    def broken(**kw):
        raise PermissionError("No route to host")

    monkeypatch.setattr(presence, "scan", broken)
    scheduler.tick(db)
    assert [a["title"] for a in parent.get("/api/alerts").json()] == ["Sam arrived at Home"]
    assert "No route" in parent.get("/api/family").json()["wifi_scan"]["error"]
