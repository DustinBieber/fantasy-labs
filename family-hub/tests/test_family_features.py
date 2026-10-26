"""Chore photo proof, medication reminders and savings goals."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.models import Family, MedDose
from app.services import meds, push


@pytest.fixture
def files_dir(tmp_path):
    old = settings.files_dir
    object.__setattr__(settings, "files_dir", str(tmp_path))
    yield tmp_path
    object.__setattr__(settings, "files_dir", old)


# ---------------------------------------------------------------- chore photos

def test_photo_chore_requires_and_shows_photo(parent, kid, files_dir):
    parent.post("/api/chores", {"title": "Clean room", "points": 20, "assignee_id": kid.id, "requires_photo": True})
    [inst] = kid.get("/api/chores/instances").json()
    assert inst["requires_photo"] is True
    r = kid.post(f"/api/chores/instances/{inst['id']}/complete")
    assert r.status_code == 422 and "photo" in r.json()["detail"]
    bad = kid.post(f"/api/chores/instances/{inst['id']}/photo", files={"file": ("x.txt", b"hi", "text/plain")})
    assert bad.status_code == 415
    r = kid.post(f"/api/chores/instances/{inst['id']}/photo", files={"file": ("room.jpg", b"\xff\xd8JPEG", "image/jpeg")})
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    photo = r.json()["photo"]
    assert parent.c.get(photo["url"]).content == b"\xff\xd8JPEG"
    alert = next(a for a in parent.get("/api/alerts").json() if "Clean room" in a["title"])
    assert "Photo attached" in alert["body"]
    # Already submitted: a second upload is refused.
    assert kid.post(f"/api/chores/instances/{inst['id']}/photo", files={"file": ("a.jpg", b"x", "image/jpeg")}).status_code == 409


# ---------------------------------------------------------------- medication reminders

def _local(fam_tz, hh, mm, day_offset=0):
    now_local = datetime.now(ZoneInfo(fam_tz)) + timedelta(days=day_offset)
    local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def test_medication_reminders_escalate_and_taken(parent, kid, db, monkeypatch):
    pushed = []
    monkeypatch.setattr(push, "deliver", lambda sub, payload, key, claims: pushed.append((sub["endpoint"], payload)) or 201)
    kid.post("/api/push/subscribe", {"endpoint": "https://fcm.googleapis.com/kid", "keys": {"p256dh": "a", "auth": "b"}})
    parent.post("/api/push/subscribe", {"endpoint": "https://fcm.googleapis.com/parent", "keys": {"p256dh": "a", "auth": "b"}})
    assert parent.post("/api/health", {"member_id": kid.id, "kind": "medication", "title": "Amoxicillin",
                                       "reminder_times": ["25:00"]}).status_code == 422
    rec = parent.post("/api/health", {"member_id": kid.id, "kind": "medication", "title": "Amoxicillin", "value": "250",
                                      "unit": "mg", "reminder_times": ["8:00", "20:00"]}).json()
    assert rec["reminder_times"] == ["08:00", "20:00"]
    fam = db.query(Family).one()
    slot = _local(fam.timezone, 8, 0)

    meds.tick(db, fam, slot + timedelta(minutes=1))
    meds.tick(db, fam, slot + timedelta(minutes=2))  # no duplicate reminder
    assert [e for e, _ in pushed] == ["https://fcm.googleapis.com/kid"]
    assert '"taken_url"' in pushed[0][1] and "Amoxicillin" in pushed[0][1]
    assert not any("Amoxicillin" in a["title"] for a in parent.get("/api/alerts").json())  # personal reminder

    pushed.clear()
    meds.tick(db, fam, slot + timedelta(minutes=31))
    assert [e for e, _ in pushed] == ["https://fcm.googleapis.com/parent"]
    assert "Sam hasn't taken Amoxicillin (250 mg)" in [a["title"] for a in parent.get("/api/alerts").json()]

    # The Android "Taken ✓" button posts the signed link without signing in.
    dose = db.query(MedDose).one()
    link = meds.taken_link(dose).replace(settings.base_url, "")
    assert kid.c.post(link.replace("sig=", "sig=x")).status_code == 404
    assert kid.c.post(link).json() == {"status": "taken"}
    assert "Sam took Amoxicillin (250 mg)" in [a["title"] for a in parent.get("/api/alerts").json()]


def test_mark_dose_early_and_schedule(parent, kid, db):
    rec = parent.post("/api/health", {"member_id": kid.id, "kind": "medication", "title": "Vitamin D",
                                      "reminder_times": ["23:59"]}).json()
    today = kid.get("/api/meds/today").json()
    assert [(m["title"], m["status"]) for m in today] in ([("Vitamin D", "upcoming")], [("Vitamin D", "due")])
    r = kid.post(f"/api/meds/{rec['id']}/dose", {"slot": today[0]["slot"], "status": "taken"})
    assert r.json() == {"status": "taken"}
    assert kid.get("/api/dashboard").json()["meds"][0]["status"] == "taken"
    fam = db.query(Family).one()
    meds.tick(db, fam, datetime.fromisoformat(today[0]["slot"][:-1]) + timedelta(minutes=1))
    assert not any("Vitamin D" in a["title"] for a in kid.get("/api/alerts").json())  # already taken, no reminder
    assert kid.post(f"/api/meds/{rec['id']}/dose", {"slot": "2020-01-01T08:00:00Z"}).status_code == 404


def test_reminder_days_and_end_date():
    from app.models import HealthRecord

    rec = HealthRecord(kind="medication", reminder_times=["09:00"], reminder_days=[0], title="x")
    monday = datetime(2026, 10, 26).date()
    assert len(meds.slots_on(rec, monday, "UTC")) == 1 and meds.slots_on(rec, monday + timedelta(days=1), "UTC") == []
    rec.reminder_days, rec.reminder_until = [], monday
    assert meds.slots_on(rec, monday + timedelta(days=1), "UTC") == []


# ---------------------------------------------------------------- savings goals

def test_savings_goal_with_match_gift_and_reach(parent, kid):
    parent.post("/api/finance/transactions", {"amount_cents": 1000, "category": "allowance", "member_id": kid.id})
    g = kid.post("/api/finance/goals", {"member_id": kid.id, "title": "Bike", "icon": "🚲", "target_cents": 1500,
                                        "match_percent": 100}).json()
    assert g["match_percent"] == 0  # kids can't set their own match
    assert kid.patch(f"/api/finance/goals/{g['id']}", {"match_percent": 50}).status_code == 403
    parent.patch(f"/api/finance/goals/{g['id']}", {"match_percent": 50})

    g = kid.post(f"/api/finance/goals/{g['id']}/deposit", {"amount_cents": 600}).json()
    assert g["saved_cents"] == 900 and g["percent"] == 60  # $6 + $3 match
    assert kid.get(f"/api/finance/wallet/{kid.id}").json()["balance_cents"] == 400
    assert kid.post(f"/api/finance/goals/{g['id']}/deposit", {"amount_cents": 500}).status_code == 409  # only $4 left
    assert kid.post(f"/api/finance/goals/{g['id']}/spend").status_code == 409
    assert kid.post(f"/api/finance/goals/{g['id']}/deposit", {"amount_cents": 100, "gift": True}).status_code == 403

    g = parent.post(f"/api/finance/goals/{g['id']}/deposit", {"amount_cents": 600, "gift": True}).json()
    assert g["status"] == "reached" and g["saved_cents"] == 1500
    titles = [a["title"] for a in kid.get("/api/alerts").json()]
    assert "🎉 Sam reached their goal: 🚲 Bike!" in titles
    assert kid.get(f"/api/finance/wallet/{kid.id}").json()["balance_cents"] == 400  # gift went straight in

    g = kid.post(f"/api/finance/goals/{g['id']}/withdraw", {"amount_cents": 100}).json()
    assert g["status"] == "active" and kid.get(f"/api/finance/wallet/{kid.id}").json()["balance_cents"] == 500
    assert kid.post(f"/api/finance/goals/{g['id']}/withdraw", {"amount_cents": 99999}).status_code == 409
    summary = parent.get("/api/finance/summary").json()
    assert summary["wallets"][0]["goals"][0]["title"] == "Bike"


def test_close_goal_refunds_wallet(parent, kid):
    parent.post("/api/finance/transactions", {"amount_cents": 500, "category": "gift", "member_id": kid.id})
    g = kid.post("/api/finance/goals", {"member_id": kid.id, "title": "Lego", "target_cents": 5000}).json()
    kid.post(f"/api/finance/goals/{g['id']}/deposit", {"amount_cents": 300})
    assert kid.delete(f"/api/finance/goals/{g['id']}").status_code == 200
    assert kid.get(f"/api/finance/wallet/{kid.id}").json()["balance_cents"] == 500
    assert kid.get("/api/finance/goals").json() == []
    assert any("started saving for 🎯 Lego" in a["title"] for a in parent.get("/api/alerts").json())
