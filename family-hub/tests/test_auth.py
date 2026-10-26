from .conftest import Api


def test_signup_login_and_me(client, parent):
    me = parent.get("/api/auth/me").json()
    assert me["member"]["role"] == "parent" and me["family"]["name"] == "Rivera Family"
    r = client.post("/api/auth/login", json={"email": "ALEX@example.com", "password": "correct-horse"})
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"email": "alex@example.com", "password": "nope-nope"}).status_code == 401


def test_duplicate_email_rejected(client, parent):
    r = client.post("/api/auth/signup", json={"family_name": "X", "parent_name": "Y", "email": "alex@example.com",
                                              "password": "12345678"})
    assert r.status_code == 409


def test_kid_pin_login_and_permissions(client, parent, kid):
    code = parent.get("/api/family").json()["join_code"]
    info = client.get(f"/api/auth/join/{code.lower()}").json()
    assert [m["name"] for m in info["members"]] == ["Sam"]
    assert kid.get("/api/family").status_code == 403
    assert kid.post("/api/chores", {"title": "x"}).status_code == 403
    assert kid.get("/api/locks").status_code == 403
    # Kids can change their own color but not their allowance.
    assert kid.patch(f"/api/members/{kid.id}", {"color": "#FF0000"}).status_code == 200
    assert kid.patch(f"/api/members/{kid.id}", {"weekly_allowance_cents": 99999}).status_code == 403


def test_pin_lockout(client, parent, kid):
    code = parent.get("/api/family").json()["join_code"]
    for _ in range(5):
        assert client.post("/api/auth/pin-login", json={"join_code": code, "member_id": kid.id, "pin": "0000"}).status_code == 401
    r = client.post("/api/auth/pin-login", json={"join_code": code, "member_id": kid.id, "pin": "1234"})
    assert r.status_code == 429


def test_logout_revokes_token(client, parent):
    assert parent.post("/api/auth/logout").status_code == 200
    assert parent.get("/api/auth/me").status_code == 401


def test_families_are_isolated(client, parent, kid):
    chore = parent.post("/api/chores", {"title": "Dishes", "assignee_id": kid.id}).json()
    r = client.post("/api/auth/signup", json={"family_name": "Other", "parent_name": "Pat", "email": "pat@example.com",
                                              "password": "12345678"})
    other = Api(client, r.json()["token"])
    assert other.patch(f"/api/chores/{chore['id']}", {"title": "hacked"}).status_code == 404
    assert other.patch(f"/api/members/{kid.id}", {"name": "hacked"}).status_code == 404
    assert other.get("/api/chores").json() == []
    assert [m["name"] for m in other.get("/api/members").json()] == ["Pat"]
