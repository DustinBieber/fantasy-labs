"""The connector framework: catalog, Find My, Google location sharing, calendar links, feeds, email, chat."""
from datetime import timedelta

import httpx
import icalendar
import pytest

from app.config import settings
from app.connectors import BY_KEY, location
from app.models import Event, Family, Integration, LocationPing, NotificationChannel
from app.services import macos, scheduler
from app.timeutil import utcnow

HOME = (41.8781, -87.6298)


@pytest.fixture(autouse=True)
def state_dir(tmp_path):
    old = settings.state_dir
    object.__setattr__(settings, "state_dir", str(tmp_path / "state"))
    location._sessions.clear()
    location._pending.clear()
    yield
    object.__setattr__(settings, "state_dir", old)


def _item(parent, key):
    return next(i for i in parent.get("/api/connectors").json()["items"] if i["key"] == key)


def test_catalog_lists_everything(parent, kid):
    d = parent.get("/api/connectors").json()
    keys = {i["key"] for i in d["items"]}
    assert {"google", "icloud", "ics_feeds", "hub_feed", "findmy", "google_location", "wifi_presence",
            "web_push", "imessage", "google_messages", "google_chat", "gmail_smtp", "philips_lock",
            "apple_home", "homeassistant"} <= keys
    assert d["total"] == len(d["items"]) and d["connected"] >= 2  # hub feed + file storage are always ready
    assert _item(parent, "google_chat")["availability"] == "bridge"
    assert d["calendar_feed"].endswith("/calendar.ics")
    assert kid.get("/api/connectors").status_code == 403
    assert parent.post("/api/connectors/web_push/connect", {"values": {}}).status_code == 400
    assert parent.post("/api/connectors/nope/connect", {"values": {}}).status_code == 404
    assert parent.post("/api/connectors/findmy/connect", {"values": {"apple_id": "a@b.c"}}).status_code == 422


class FakeDevice:
    def __init__(self, data):
        self.data = data


class FakeDevices(list):
    def refresh(self, locate=True):
        pass


class FakeICloud:
    def __init__(self, devices, needs_code=True):
        self.requires_2fa, self.requires_2sa = needs_code, False
        self.is_trusted_session, self.trusted = False, False
        self._devices = devices

    def validate_2fa_code(self, code):
        return code == "123456"

    def trust_session(self):
        self.trusted = True

    @property
    def devices(self):
        return FakeDevices(FakeDevice(d) for d in self._devices)


def test_find_my_two_step_sign_in_and_locations(parent, kid, db, monkeypatch):
    parent.post("/api/geofences", {"name": "Home", "lat": HOME[0], "lng": HOME[1]})
    devs = [{"id": "dev1", "name": "Sam's iPhone", "batteryLevel": 0.42,
             "location": {"latitude": HOME[0], "longitude": HOME[1], "horizontalAccuracy": 10, "timeStamp": 1}},
            {"id": "dev2", "name": "Living Room iPad", "location": None}]
    fake = FakeICloud(devs)
    monkeypatch.setattr(location.FindMy, "_service", lambda self, a, p, ctx: fake)

    r = parent.post("/api/connectors/findmy/connect", {"values": {"apple_id": "alex@icloud.com", "password": "pw"}}).json()
    assert r["ok"] is False and r["step"] == "code" and r["fields"][0]["name"] == "code"
    assert parent.post("/api/connectors/findmy/finish", {"values": {"code": "000000"}}).status_code == 400
    r = parent.post("/api/connectors/findmy/finish", {"values": {"code": "123456"}}).json()
    assert r == {"ok": True, "message": "2 found, 1 updated"} and fake.trusted

    item = _item(parent, "findmy")
    assert item["state"] == "connected" and item["mapping"] == {"dev1": kid.id, "dev2": None}
    pings = db.query(LocationPing).filter_by(member_id=kid.id).all()
    assert len(pings) == 1 and pings[0].battery == 42

    # Same timestamp → no duplicate; moved → geofence alert.
    assert parent.post("/api/connectors/findmy/sync").json()["message"] == "2 found, 0 updated"
    devs[0]["location"] = {"latitude": 41.89, "longitude": -87.62, "horizontalAccuracy": 10, "timeStamp": 2}
    parent.post("/api/connectors/findmy/sync")
    assert "Sam left Home" in [a["title"] for a in parent.get("/api/alerts").json()]

    # Parent remaps the iPad to themselves.
    me = parent.get("/api/auth/me").json()["member"]["id"]
    assert parent.put("/api/connectors/findmy/map", {"map": {"dev1": kid.id, "dev2": me}}).status_code == 200
    assert _item(parent, "findmy")["mapping"]["dev2"] == me
    parent.delete("/api/connectors/findmy")
    assert _item(parent, "findmy")["state"] == "off"


def test_find_my_expired_session_is_reported(parent, db, monkeypatch):
    fake = FakeICloud([], needs_code=False)
    monkeypatch.setattr(location.FindMy, "_service", lambda self, a, p, ctx: fake)
    assert parent.post("/api/connectors/findmy/connect", {"values": {"apple_id": "a@icloud.com", "password": "pw"}}).json()["ok"]
    location._sessions.clear()
    fake.requires_2fa = True
    integ = db.query(Integration).filter_by(provider="findmy").one()
    integ.last_sync_at = utcnow() - timedelta(hours=1)
    db.commit()
    scheduler.tick(db)
    item = _item(parent, "findmy")
    assert item["state"] == "attention" and "verification code" in item["detail"]


class FakePerson:
    def __init__(self, pid, name, lat, lng, ts):
        self.id, self.full_name, self.nickname = pid, name, name
        self.latitude, self.longitude, self.accuracy, self.battery_level, self.timestamp = lat, lng, 20, 80, ts


def test_google_location_sharing(parent, kid, db, monkeypatch):
    class FakeService:
        def get_all_people(self):
            return [FakePerson("g1", "Sam Rivera", HOME[0], HOME[1], 1000), FakePerson("g2", "Aunt Jo", 1, 1, 5)]

    monkeypatch.setattr(location.GoogleLocationSharing, "_service", lambda self, ctx: FakeService())
    bad = parent.post("/api/connectors/google_location/connect", {"values": {"account": "a@gmail.com", "cookies": "nope"}})
    assert bad.status_code == 400
    cookies = "# Netscape HTTP Cookie File\n.google.com\tTRUE\t/\tTRUE\t0\tSID\tx"
    r = parent.post("/api/connectors/google_location/connect", {"values": {"account": "a@gmail.com", "cookies": cookies}})
    assert r.json() == {"ok": True, "message": "2 found, 1 updated"}
    assert db.query(LocationPing).filter_by(member_id=kid.id).count() == 1


ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:practice@team
DTSTART:%(start)s
DTEND:%(end)s
RRULE:FREQ=WEEKLY;COUNT=3
SUMMARY:Soccer practice
LOCATION:Field 4
END:VEVENT
BEGIN:VEVENT
UID:noschool@school
DTSTART;VALUE=DATE:%(day)s
SUMMARY:No school - teacher workday
END:VEVENT
END:VCALENDAR
"""


def test_calendar_links(parent, kid, db, outbound):
    start = (utcnow() + timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    body = {"feed": ICS % {b"start": start.strftime("%Y%m%dT%H%M%SZ").encode(),
                           b"end": (start + timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ").encode(),
                           b"day": (start + timedelta(days=2)).strftime("%Y%m%d").encode()}}
    outbound.routes[("GET", "https://team.example/cal.ics")] = lambda req: httpx.Response(200, content=body["feed"])
    feeds = "Panthers | webcal://team.example/cal.ics | Sam"
    r = parent.post("/api/connectors/ics_feeds/connect", {"values": {"feeds": feeds}}).json()
    assert r == {"ok": True, "message": "4 events"}  # 3 weekly practices + 1 all-day
    evs = db.query(Event).filter_by(source="ics").order_by(Event.start).all()
    assert [e.title for e in evs].count("Soccer practice") == 3 and evs[0].member_ids == [kid.id]
    assert any(e.all_day and e.title.startswith("No school") for e in evs)
    # Team cancels the series → gone on next sync.
    body["feed"] = b"BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
    assert parent.post("/api/events/sync").json()["calendar links"] == {"message": "0 events"}
    assert db.query(Event).filter_by(source="ics").count() == 0
    assert parent.post("/api/connectors/ics_feeds/connect", {"values": {"feeds": "ftp://x"}}).status_code == 400


def test_family_calendar_feed(client, parent, kid):
    parent.patch(f"/api/members/{kid.id}", {"birthday": "2017-03-09"})
    start = (utcnow() + timedelta(days=2)).replace(microsecond=0)
    parent.post("/api/events", {"title": "Piano, recital; bring flowers", "start": start.isoformat() + "Z", "member_ids": [kid.id]})
    me = parent.get("/api/auth/me").json()["member"]["id"]
    parent.post("/api/events", {"title": "Date night", "start": start.isoformat() + "Z", "member_ids": [me]})
    parent.post("/api/events", {"title": "Family BBQ", "start": start.isoformat() + "Z"})  # untagged = everyone
    parent.post("/api/homework", {"member_id": kid.id, "subject": "Math", "title": "Fractions",
                                  "due_date": (start.date() + timedelta(days=1)).isoformat()})
    url = parent.get("/api/connectors").json()["calendar_feed"].replace("https://hub.example", "")
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    cal = icalendar.Calendar.from_ical(r.content)
    titles = [str(c.get("summary")) for c in cal.walk("VEVENT")]
    assert "Piano, recital; bring flowers (Sam)" in titles and "📚 Sam: Math – Fractions" in titles
    assert sum(t == "🎂 Sam's birthday" for t in titles) == 2 and "Date night (Alex)" in titles
    mine = [str(c.get("summary")) for c in icalendar.Calendar.from_ical(client.get(url, params={"member": kid.id}).content).walk("VEVENT")]
    assert "Date night" not in mine and "Family BBQ" in mine and "Piano, recital; bring flowers" in mine
    assert client.get(url.replace(url.split("/")[3], "bogus")).status_code == 404


def test_event_webhook(client, parent, kid):
    base = parent.get("/api/family").json()["hook_base"].replace("https://hub.example", "")
    r = client.post(f"{base}/event", json={"title": "Dentist", "start": "2026-11-02T15:00:00-05:00", "member": "sam"})
    assert r.status_code == 200
    [ev] = [e for e in parent.get("/api/events", params={"start": "2026-11-01T00:00:00Z", "end": "2026-11-03T00:00:00Z"}).json()]
    assert ev["start"] == "2026-11-02T20:00:00Z" and ev["member_ids"] == [kid.id]


def test_gmail_app_password_email(parent, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, pw):
            if pw != "abcdefghijklmnop":
                raise RuntimeError("535 bad credentials")

        def send_message(self, msg):
            sent.append((self.host, msg["To"], msg["Subject"]))

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    bad = parent.post("/api/connectors/gmail_smtp/connect", {"values": {"username": "a@gmail.com", "password": "nope", "to": "a@gmail.com"}})
    assert bad.status_code == 502 and "535" in bad.json()["detail"]
    r = parent.post("/api/connectors/gmail_smtp/connect",
                    {"values": {"username": "a@gmail.com", "password": "abcd efgh ijkl mnop", "to": "a@gmail.com, b@icloud.com"}})
    assert r.json()["ok"] and sent[-1] == ("smtp.gmail.com", "a@gmail.com, b@icloud.com", "[Rivera Family] Family Hub is connected ✅")
    assert _item(parent, "gmail_smtp")["state"] == "connected"
    hook = parent.get("/api/family").json()["webhooks"]["alert"].replace("https://hub.example", "")
    parent.c.post(hook, json={"title": "Garage open", "severity": "warning"})
    assert sent[-1][2] == "[Rivera Family] Garage open"
    assert parent.get("/api/notify/channels").json()[0]["config"]["password"].startswith("••••")


def test_messaging_bridges(parent, monkeypatch, outbound, db):
    calls = []
    monkeypatch.setattr(macos, "is_mac", lambda: True)
    monkeypatch.setattr(macos, "run", lambda cmd, timeout=30: calls.append(cmd) or "")
    assert parent.post("/api/connectors/imessage/connect", {"values": {"to": "mom@icloud.com"}}).json()["ok"]
    assert parent.post("/api/connectors/google_messages/connect", {"values": {"to": "+15550100"}}).json()["ok"]
    assert [c[-1] for c in calls] == ["imessage", "sms"]
    assert _item(parent, "imessage")["state"] == "connected" and _item(parent, "google_messages")["state"] == "connected"
    r = parent.post("/api/connectors/google_chat/connect", {"values": {"webhook_url": "https://chat.googleapis.com/v1/spaces/X/messages?key=k"}})
    assert r.json()["ok"] and outbound.calls[-1].url.host == "chat.googleapis.com"
    parent.delete("/api/connectors/google_messages")
    assert {c.label for c in db.query(NotificationChannel)} == {"iMessage", "Google Chat"}


def test_home_assistant_and_philips_status(parent, outbound):
    assert _item(parent, "philips_lock")["state"] == "off"
    r = parent.post("/api/connectors/homeassistant/connect", {"values": {"url": "http://ha.local:8123/", "token": "t" * 30}})
    assert r.json()["ok"] and _item(parent, "homeassistant")["state"] == "connected"
    hook = parent.get("/api/family").json()["webhooks"]["lock"].replace("https://hub.example", "")
    parent.post("/api/locks", {"name": "Front Door", "provider": "webhook", "external_id": "front"})
    assert _item(parent, "philips_lock")["state"] == "attention"
    parent.c.get(hook, params={"lock": "front", "event": "locked"})
    assert _item(parent, "philips_lock")["state"] == "connected"


def test_scheduler_polls_on_interval(parent, db, monkeypatch, outbound):
    polls = []
    monkeypatch.setattr(BY_KEY["ics_feeds"].__class__, "poll", lambda self, ctx: polls.append(1) or "ok")
    parent.post("/api/connectors/ics_feeds/connect", {"values": {"feeds": "https://x.example/a.ics"}})
    polls.clear()
    fam = db.query(Family).one()
    now = utcnow()
    scheduler._connectors(db, fam, now + timedelta(minutes=31))
    scheduler._connectors(db, fam, now + timedelta(minutes=40))
    assert len(polls) == 1


def test_channel_status_reflects_failed_delivery(parent, monkeypatch):
    monkeypatch.setattr(macos, "is_mac", lambda: True)
    monkeypatch.setattr(macos, "run", lambda cmd, timeout=30: "")
    parent.post("/api/connectors/imessage/connect", {"values": {"to": "+15550001"}})
    hook = parent.get("/api/family").json()["webhooks"]["alert"].replace("https://hub.example", "")

    def broken(cmd, timeout=30):
        raise RuntimeError("Messages isn't signed in")

    monkeypatch.setattr(macos, "run", broken)
    parent.c.post(hook, json={"title": "Water leak", "severity": "critical"})
    item = _item(parent, "imessage")
    assert item["state"] == "attention" and "signed in" in item["detail"]


def test_smartthings_oauth_locks_and_token_rotation(parent, db, outbound):
    from urllib.parse import parse_qs, urlparse

    from app.crypto import decrypt_json, encrypt_json

    tokens = iter([{"access_token": "at1", "refresh_token": "rt1", "expires_in": 86399},
                   {"access_token": "at2", "refresh_token": "rt2", "expires_in": 86399}])
    outbound.routes[("POST", "https://auth-global.api.smartthings.com/oauth/token")] = lambda req: httpx.Response(200, json=next(tokens))
    outbound.routes[("GET", "https://api.smartthings.com/v1/devices?")] = httpx.Response(
        200, json={"items": [{"deviceId": "d-1", "label": "Front Door"}]})
    lock_status = {"lock": {"lock": {"value": "unlocked"}}, "battery": {"battery": {"value": 64}},
                   "contactSensor": {"contact": {"value": "closed"}}}
    outbound.routes[("GET", "https://api.smartthings.com/v1/devices/d-1/status")] = lambda req: httpx.Response(
        200, json={"components": {"main": lock_status}})
    outbound.routes[("POST", "https://api.smartthings.com/v1/devices/d-1/commands")] = httpx.Response(200, json={})

    r = parent.post("/api/connectors/smartthings/connect", {"values": {"client_id": "cid", "client_secret": "sec"}}).json()
    assert r["step"] == "redirect"
    q = parse_qs(urlparse(r["url"]).query)
    assert q["redirect_uri"] == ["https://hub.example/api/connectors/smartthings/callback"] and "x:devices:*" in q["scope"][0]
    assert _item(parent, "smartthings")["state"] == "attention"  # not signed in yet

    cb = parent.c.get("/api/connectors/smartthings/callback", params={"state": q["state"][0], "code": "c"}, follow_redirects=False)
    assert cb.headers["location"].endswith("smartthings=connected")
    token_call = next(c for c in outbound.calls if "oauth/token" in str(c.url))
    assert b"grant_type=authorization_code" in token_call.content and token_call.headers["authorization"].startswith("Basic ")
    assert _item(parent, "smartthings")["state"] == "connected"

    from app.services import locks
    locks.poll(db, db.query(Family).one())
    [lk] = parent.get("/api/locks").json()
    assert (lk["name"], lk["state"], lk["battery"], lk["door"], lk["can_control"]) == ("Front Door", "unlocked", 64, "closed", True)

    # Expired access token → refresh with the single-use refresh token, store the new one.
    integ = db.query(Integration).filter_by(provider="smartthings").one()
    sec = decrypt_json(integ.secret_enc)
    sec["expires_at"] = (utcnow() - timedelta(minutes=1)).isoformat()
    integ.secret_enc = encrypt_json(sec)
    db.commit()
    assert parent.post(f"/api/locks/{lk['id']}/command", {"action": "lock"}).json()["state"] == "locked"
    cmd = outbound.calls[-1]
    assert cmd.headers["authorization"] == "Bearer at2" and b'"command":"lock"' in cmd.content.replace(b" ", b"")
    db.expire_all()
    assert decrypt_json(db.query(Integration).filter_by(provider="smartthings").one().secret_enc)["refresh_token"] == "rt2"
