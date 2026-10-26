from datetime import date, datetime, timedelta

from app.models import Family, Transaction
from app.services import scheduler
from app.timeutil import utcnow


def test_budget_alerts_and_summary(parent, kid):
    parent.put("/api/finance/budgets", {"category": "Groceries", "monthly_limit_cents": 10000})
    parent.post("/api/finance/transactions", {"amount_cents": 500000, "category": "salary"})
    parent.post("/api/finance/transactions", {"amount_cents": -8500, "category": "groceries"})
    parent.post("/api/finance/transactions", {"amount_cents": -2000, "category": "Groceries "})
    titles = [a["title"] for a in parent.get("/api/alerts").json()]
    assert titles == ["Over budget: groceries", "80% of groceries budget used"]
    s = parent.get("/api/finance/summary").json()
    assert s["income_cents"] == 500000 and s["expense_cents"] == 10500
    assert s["categories"] == [{"category": "groceries", "spent_cents": 10500, "budget_cents": 10000}]
    assert s["wallets"][0]["name"] == "Sam"
    assert kid.get("/api/finance/summary").status_code == 403
    assert parent.post("/api/finance/transactions", {"amount_cents": 0}).status_code == 422


def test_kid_wallet_rules(parent, kid):
    parent.post("/api/finance/transactions", {"amount_cents": 1000, "category": "gift", "member_id": kid.id})
    assert kid.post("/api/finance/transactions", {"amount_cents": 500, "member_id": kid.id}).status_code == 403
    assert kid.post("/api/finance/transactions", {"amount_cents": -1500, "member_id": kid.id}).status_code == 409
    assert kid.post("/api/finance/transactions", {"amount_cents": -300, "member_id": kid.id,
                                                  "description": "Comic"}).status_code == 200
    assert kid.get(f"/api/finance/wallet/{kid.id}").json()["balance_cents"] == 700
    assert len(kid.get("/api/finance/transactions", params={"household": True}).json()) == 2  # forced to own wallet


def test_weekly_allowance_paid_once(parent, kid, db):
    fam = db.query(Family).one()
    fam.settings = {"allowance_weekday": 0}
    db.commit()
    now = datetime(2026, 10, 21, 18, 0)  # a Wednesday, after Monday payday
    scheduler.tick(db, now)
    scheduler.tick(db, now + timedelta(hours=1))
    paid = db.query(Transaction).filter_by(member_id=kid.id, category="allowance").all()
    assert len(paid) == 1 and paid[0].amount_cents == 500
    scheduler.tick(db, now + timedelta(days=7))
    assert db.query(Transaction).filter_by(member_id=kid.id, category="allowance").count() == 2


def test_homework_bonus_points_once(parent, kid):
    hw = parent.post("/api/homework", {"member_id": kid.id, "subject": "Math", "title": "Fractions p.42",
                                       "due_date": date.today().isoformat(), "points": 10}).json()
    kid.patch(f"/api/homework/{hw['id']}", {"status": "done"})
    kid.patch(f"/api/homework/{hw['id']}", {"status": "todo"})
    kid.patch(f"/api/homework/{hw['id']}", {"status": "done"})
    assert kid.get(f"/api/points/{kid.id}").json()["balance"] == 10
    # Kids can add their own homework but not bonus points.
    own = kid.post("/api/homework", {"member_id": kid.id, "subject": "Art", "title": "Sketch",
                                     "due_date": date.today().isoformat(), "points": 999}).json()
    assert own["points"] == 0
    assert any("finished Math" in a["title"] for a in parent.get("/api/alerts").json())


def test_health_records_privacy(parent, kid):
    me = parent.get("/api/auth/me").json()["member"]["id"]
    parent.post("/api/health", {"member_id": kid.id, "kind": "allergy", "title": "Peanuts"})
    parent.post("/api/health", {"member_id": me, "kind": "medication", "title": "Lisinopril", "value": "10",
                                "unit": "mg"})
    assert [r["title"] for r in kid.get("/api/health").json()] == ["Peanuts"]
    assert kid.post("/api/health", {"member_id": kid.id, "kind": "note", "title": "x"}).status_code == 403
    assert parent.post("/api/health", {"member_id": kid.id, "kind": "bogus", "title": "x"}).status_code == 422


def test_milestones_and_events(parent, kid):
    parent.post("/api/milestones", {"member_id": kid.id, "title": "Lost first tooth", "date": "2026-09-14",
                                    "category": "growth"})
    assert kid.get("/api/milestones").json()[0]["title"] == "Lost first tooth"
    start = (utcnow() + timedelta(days=1)).replace(microsecond=0)
    ev = kid.post("/api/events", {"title": "Soccer", "start": start.isoformat() + "Z",
                                  "member_ids": [kid.id]}).json()
    assert ev["start"] == start.isoformat() + "Z"
    assert kid.delete(f"/api/events/{ev['id']}").status_code == 403
    assert parent.post("/api/events", {"title": "Bad", "start": start.isoformat(),
                                       "end": (start - timedelta(hours=1)).isoformat()}).status_code == 422
    assert len(parent.get("/api/events").json()) == 1


def test_dashboard(parent, kid):
    parent.post("/api/chores", {"title": "Dishes", "assignee_id": kid.id})
    d = kid.get("/api/dashboard").json()
    assert [c["title"] for c in d["chores"]] == ["Dishes"] and "locks" not in d
    d = parent.get("/api/dashboard").json()
    assert {m["name"] for m in d["members"]} == {"Alex", "Sam"} and d["locks"] == []


def test_digest(parent, kid, db):
    tomorrow = date.today() + timedelta(days=1)
    parent.post("/api/homework", {"member_id": kid.id, "subject": "Science", "title": "Volcano",
                                  "due_date": tomorrow.isoformat()})
    fam = db.query(Family).one()
    body = scheduler.build_digest(db, fam, date.today())
    assert "Sam: Science – Volcano" in body
