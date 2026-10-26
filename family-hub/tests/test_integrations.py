import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.config import settings
from app.models import Event, Integration
from app.services import icloud
from app.timeutil import utcnow


def test_channels_fan_out_by_severity_and_kind(parent, kid, outbound):
    parent.post("/api/notify/channels", {"kind": "ntfy", "label": "Phones", "config": {"topic": "rivera-xyz"},
                                         "min_severity": "info"})
    parent.post("/api/notify/channels", {"kind": "twilio_sms", "label": "SMS", "min_severity": "warning",
                                         "config": {"account_sid": "AC1", "auth_token": "secret-token",
                                                    "from_number": "+15550001", "to_numbers": "+15550002, +15550003"}})
    parent.post("/api/notify/channels", {"kind": "google_chat", "label": "Chat", "kinds": ["door"],
                                         "config": {"webhook_url": "https://chat.googleapis.com/v1/spaces/X/messages?key=k"}})
    chans = parent.get("/api/notify/channels").json()
    assert chans[1]["config"]["auth_token"] == "••••oken"  # secrets masked
    assert chans[1]["config"]["to_numbers"] == ["+15550002", "+15550003"]

    # info chore alert: ntfy only.
    parent.post("/api/chores", {"title": "Dishes", "assignee_id": kid.id})
    [inst] = kid.get("/api/chores/instances").json()
    kid.post(f"/api/chores/instances/{inst['id']}/complete")
    assert [c.url.host for c in outbound.calls] == ["ntfy.sh"]
    ntfy = json.loads(outbound.calls[0].content)
    assert ntfy["topic"] == "rivera-xyz" and ntfy["priority"] == 3 and "“Dishes”" in ntfy["title"]

    # Kid-only notices never leave the app.
    outbound.calls.clear()
    parent.post(f"/api/chores/instances/{inst['id']}/review", {"approve": True})
    assert outbound.calls == []

    # Critical door alert: everything, including 2 SMS.
    parent.post("/api/locks", {"name": "Front", "provider": "webhook", "external_id": "f"})
    hook = parent.get("/api/family").json()["webhooks"]["lock"].replace("https://hub.example", "")
    parent.c.post(hook, json={"lock": "f", "event": "jammed"})
    hosts = sorted(c.url.host for c in outbound.calls)
    assert hosts == ["api.twilio.com", "api.twilio.com", "chat.googleapis.com", "ntfy.sh"]
    chat = next(c for c in outbound.calls if c.url.host == "chat.googleapis.com")
    assert "Front is jammed" in json.loads(chat.content)["text"]


def test_failing_channel_is_recorded_not_fatal(parent, outbound):
    outbound.routes[("POST", "https://ntfy.sh")] = httpx.Response(500)
    parent.post("/api/notify/channels", {"kind": "ntfy", "label": "Phones", "config": {"topic": "t"},
                                         "min_severity": "info"})
    hook = parent.get("/api/family").json()["webhooks"]["alert"].replace("https://hub.example", "")
    assert parent.c.post(hook, json={"title": "Garage left open", "severity": "warning"}).status_code == 200
    [alert] = parent.get("/api/alerts").json()
    assert list(alert["delivery"].values())[0].startswith("error")
    assert parent.post("/api/notify/channels/1/test").status_code == 502


def test_channel_validation(parent):
    assert parent.post("/api/notify/channels", {"kind": "ntfy", "label": "x", "config": {}}).status_code == 422
    assert parent.post("/api/notify/channels", {"kind": "pager", "label": "x", "config": {}}).status_code == 422


@pytest.fixture
def google_env():
    object.__setattr__(settings, "google_client_id", "cid")
    object.__setattr__(settings, "google_client_secret", "csecret")
    yield
    object.__setattr__(settings, "google_client_id", "")
    object.__setattr__(settings, "google_client_secret", "")


def _connect_google(parent, outbound):
    outbound.routes[("POST", "https://oauth2.googleapis.com/token")] = httpx.Response(
        200, json={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
    outbound.routes[("GET", "https://openidconnect.googleapis.com")] = httpx.Response(200, json={"email": "alex@gmail.com"})
    url = parent.get("/api/integrations/google/start").json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["redirect_uri"] == ["https://hub.example/api/integrations/google/callback"]
    assert "https://www.googleapis.com/auth/gmail.send" in q["scope"][0]
    r = parent.c.get("/api/integrations/google/callback", params={"state": q["state"][0], "code": "abc"},
                     follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"].endswith("google=connected")


def test_google_requires_server_credentials(parent):
    assert parent.get("/api/integrations/google/start").status_code == 400


def test_google_oauth_calendar_sync_and_gmail(google_env, parent, outbound, db):
    soon = utcnow() + timedelta(days=2)
    outbound.routes[("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events")] = httpx.Response(200, json={
        "items": [
            {"id": "g1", "summary": "Dentist", "start": {"dateTime": soon.isoformat() + "-05:00"},
             "end": {"dateTime": (soon + timedelta(hours=1)).isoformat() + "-05:00"}, "location": "Main St"},
            {"id": "g2", "summary": "Field trip", "start": {"date": soon.date().isoformat()},
             "end": {"date": (soon.date() + timedelta(days=1)).isoformat()}},
            {"id": "g3", "summary": "Pushed by hub", "start": {"date": soon.date().isoformat()},
             "extendedProperties": {"private": {"familyHubId": "9"}}},
        ]})
    outbound.routes[("POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events")] = httpx.Response(
        200, json={"id": "remote-1"})
    _connect_google(parent, outbound)
    status = parent.get("/api/integrations").json()["google"]
    assert status["account"] == "alex@gmail.com" and status["last_error"] == ""
    events = {e["title"]: e for e in parent.get("/api/events", params={"end": (utcnow() + timedelta(days=5)).isoformat()}).json()}
    assert set(events) == {"Dentist", "Field trip"}
    assert events["Field trip"]["all_day"] and events["Dentist"]["source"] == "google"
    assert events["Dentist"]["start"] == (soon + timedelta(hours=5)).replace(microsecond=0).isoformat() + "Z"

    # Local event flagged for Google gets pushed exactly once.
    parent.post("/api/events", {"title": "Piano", "start": soon.isoformat(), "push_to": ["google"]})
    parent.post("/api/events/sync")
    parent.post("/api/events/sync")
    posts = [c for c in outbound.calls if c.method == "POST" and "/calendar/v3/" in str(c.url)]
    assert len(posts) == 1 and json.loads(posts[0].content)["summary"] == "Piano"

    # Gmail channel sends through the connected account.
    parent.post("/api/notify/channels", {"kind": "gmail", "label": "Email", "config": {"to": "alex@gmail.com"},
                                         "min_severity": "info"})
    assert parent.post("/api/notify/channels/1/test").status_code == 200
    send = next(c for c in outbound.calls if c.url.path.endswith("/messages/send"))
    raw = base64.urlsafe_b64decode(json.loads(send.content)["raw"]).decode()
    assert "Subject: [Rivera Family] Test from Family Hub" in raw
    assert send.headers["authorization"] == "Bearer at1"


def test_google_token_refresh(google_env, parent, outbound, db):
    _connect_google(parent, outbound)
    from app.crypto import decrypt_json, encrypt_json
    from app.services import google

    integ = db.query(Integration).filter_by(provider="google").one()
    tokens = decrypt_json(integ.secret_enc)
    tokens["expires_at"] = (utcnow() - timedelta(minutes=1)).isoformat()
    integ.secret_enc = encrypt_json(tokens)
    db.commit()
    outbound.routes[("POST", "https://oauth2.googleapis.com/token")] = httpx.Response(
        200, json={"access_token": "at2", "expires_in": 3600})
    assert google.access_token(db, integ) == "at2"
    assert decrypt_json(integ.secret_enc)["refresh_token"] == "rt1"


def test_drive_attachment_upload(google_env, parent, kid, outbound):
    _connect_google(parent, outbound)
    outbound.routes[("POST", "https://www.googleapis.com/drive/v3/files")] = httpx.Response(200, json={"id": "folder1"})
    outbound.routes[("POST", "https://www.googleapis.com/upload/drive/v3/files")] = httpx.Response(
        200, json={"id": "file1", "name": "worksheet.pdf", "webViewLink": "https://drive.google.com/file/d/file1"})
    hw = parent.post("/api/homework", {"member_id": kid.id, "subject": "Math", "title": "p.4",
                                       "due_date": "2026-10-30"}).json()
    r = kid.post(f"/api/attachments/homework/{hw['id']}", files={"file": ("worksheet.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json()["attachments"] == [{"name": "worksheet.pdf", "url": "https://drive.google.com/file/d/file1",
                                        "drive_id": "file1"}]
    upload = next(c for c in outbound.calls if "upload/drive" in str(c.url))
    assert b'"parents": ["folder1"]' in upload.content


def test_icloud_sync(parent, db, monkeypatch):
    soon = utcnow() + timedelta(days=1)
    remote = [{"uid": "u1", "title": "Recital", "start": soon, "end": soon + timedelta(hours=2), "all_day": False,
               "location": "School", "notes": "", "from_hub": False},
              {"uid": "u2", "title": "Ours", "start": soon, "end": None, "all_day": False, "location": "",
               "notes": "", "from_hub": True}]
    pushed = []
    monkeypatch.setattr(icloud, "verify", lambda s, o: "Family")
    monkeypatch.setattr(icloud, "fetch_remote", lambda s, o, a, b: list(remote))
    monkeypatch.setattr(icloud, "push_remote", lambda s, o, ev: pushed.append(ev.title) or "uid-x")
    r = parent.post("/api/integrations/icloud", {"username": "alex@icloud.com", "password": "abcd-efgh-ijkl-mnop"})
    assert r.status_code == 200 and r.json()["options"]["resolved_calendar"] == "Family"
    parent.post("/api/events", {"title": "Piano", "start": soon.isoformat(), "push_to": ["icloud"]})
    assert parent.post("/api/events/sync").json() == {"icloud": {"pulled": 1, "pushed": 1}}
    assert pushed == ["Piano"]
    titles = sorted(e.title for e in db.query(Event))
    assert titles == ["Piano", "Recital"]
    remote.pop(0)  # deleted on the iPhone
    parent.post("/api/events/sync")
    assert sorted(e.title for e in db.query(Event)) == ["Piano"]
    parent.delete("/api/integrations/icloud")
    assert parent.get("/api/integrations").json()["icloud"] is None


def test_scheduler_records_sync_errors(parent, db, monkeypatch):
    from app.services import scheduler

    monkeypatch.setattr(icloud, "verify", lambda s, o: "Cal")
    parent.post("/api/integrations/icloud", {"username": "a@icloud.com", "password": "abcd-efgh-ijkl"})

    def boom(*a):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(icloud, "fetch_remote", boom)
    scheduler.tick(db)
    assert "401" in parent.get("/api/integrations").json()["icloud"]["last_error"]
