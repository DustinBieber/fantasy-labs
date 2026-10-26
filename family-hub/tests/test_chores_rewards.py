from datetime import date, timedelta

from app.models import Chore
from app.services.chores import occurs_on


def test_recurrence_rules():
    start = date(2026, 1, 31)  # a Saturday
    weekly = Chore(title="t", recurrence="weekly", weekdays=[0, 2], start_date=start, active=True)
    assert occurs_on(weekly, date(2026, 2, 2)) and occurs_on(weekly, date(2026, 2, 4))
    assert not occurs_on(weekly, date(2026, 2, 3))
    monthly = Chore(title="t", recurrence="monthly", weekdays=[], start_date=start, active=True)
    assert occurs_on(monthly, date(2026, 2, 28))  # clamps to the last day of short months
    assert not occurs_on(monthly, date(2026, 1, 30))  # before start
    once = Chore(title="t", recurrence="once", weekdays=[], start_date=start, active=True)
    assert occurs_on(once, start) and not occurs_on(once, start + timedelta(days=1))


def test_chore_approval_flow_awards_points(parent, kid):
    parent.post("/api/chores", {"title": "Feed the dog", "points": 15, "assignee_id": kid.id, "recurrence": "daily"})
    [inst] = kid.get("/api/chores/instances").json()
    r = kid.post(f"/api/chores/instances/{inst['id']}/complete").json()
    assert r["status"] == "submitted"
    assert kid.post(f"/api/chores/instances/{inst['id']}/complete").status_code == 409
    assert kid.post(f"/api/chores/instances/{inst['id']}/review", {"approve": True}).status_code == 403
    parent_alerts = parent.get("/api/alerts").json()
    assert any("Feed the dog" in a["title"] for a in parent_alerts)

    assert parent.post(f"/api/chores/instances/{inst['id']}/review", {"approve": True}).json()["status"] == "approved"
    assert kid.get(f"/api/points/{kid.id}").json()["balance"] == 15
    assert any("+15" in a["title"] for a in kid.get("/api/alerts").json())
    # A week of the daily chore materializes on request.
    week = kid.get("/api/chores/instances", params={"start": date.today().isoformat(),
                                                    "end": (date.today() + timedelta(days=6)).isoformat()}).json()
    assert len(week) >= 6


def test_rejected_chore_can_be_redone(parent, kid):
    parent.post("/api/chores", {"title": "Room", "points": 5, "assignee_id": kid.id})
    [inst] = kid.get("/api/chores/instances").json()
    kid.post(f"/api/chores/instances/{inst['id']}/complete")
    assert parent.post(f"/api/chores/instances/{inst['id']}/review", {"approve": False}).json()["status"] == "rejected"
    assert kid.post(f"/api/chores/instances/{inst['id']}/complete").json()["status"] == "submitted"


def test_no_approval_chore_awards_immediately(parent, kid):
    parent.post("/api/chores", {"title": "Make bed", "points": 3, "assignee_id": kid.id, "requires_approval": False})
    [inst] = kid.get("/api/chores/instances").json()
    assert kid.post(f"/api/chores/instances/{inst['id']}/complete").json()["status"] == "approved"
    assert kid.get(f"/api/points/{kid.id}").json()["balance"] == 3


def test_cannot_complete_someone_elses_chore(client, parent, kid):
    other = parent.post("/api/members", {"name": "Jo", "role": "child", "pin": "5555"}).json()
    parent.post("/api/chores", {"title": "Trash", "assignee_id": other["id"]})
    [inst] = kid.get("/api/chores/instances").json()
    assert kid.post(f"/api/chores/instances/{inst['id']}/complete").status_code == 403


def test_reward_redeem_hold_and_refund(parent, kid):
    reward = parent.post("/api/rewards", {"title": "Movie night", "cost": 20, "icon": "🍿"}).json()
    assert kid.post(f"/api/rewards/{reward['id']}/redeem").status_code == 409  # not enough points
    parent.post("/api/points/adjust", {"member_id": kid.id, "amount": 25, "reason": "Helped grandma"})
    red = kid.post(f"/api/rewards/{reward['id']}/redeem").json()
    assert kid.get(f"/api/points/{kid.id}").json()["balance"] == 5
    assert parent.post(f"/api/redemptions/{red['id']}/decide", {"fulfill": False}).json()["status"] == "declined"
    assert kid.get(f"/api/points/{kid.id}").json()["balance"] == 25
    assert parent.post(f"/api/redemptions/{red['id']}/decide", {"fulfill": True}).status_code == 409


def test_deactivating_chore_removes_future_todos(parent, kid):
    chore = parent.post("/api/chores", {"title": "Water plants", "assignee_id": kid.id, "recurrence": "daily"}).json()
    kid.get("/api/chores/instances", params={"end": (date.today() + timedelta(days=5)).isoformat()})
    parent.delete(f"/api/chores/{chore['id']}")
    assert kid.get("/api/chores/instances").json() == []
