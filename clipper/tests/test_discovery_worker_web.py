from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from clipper import discovery, publish, socials, worker
from clipper.db import dumps, now_iso
from clipper.publish.base import Vault
from clipper.web.app import create_app


def test_score_lead_rewards_the_gap(cfg):
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    perfect = {"long_videos": 300, "shorts": 0, "subscribers": 40_000, "last_upload": recent}
    score, reasons = discovery.score_lead(perfect, cfg)
    assert score == 100 and "no YouTube Shorts" in reasons and "no tiktok linked" in reasons
    busy = {**perfect, "shorts": 40, "tiktok": "show", "tiktok_videos": 500, "instagram": "show", "instagram_posts": 900}
    assert discovery.score_lead(busy, cfg)[0] < cfg.discovery.min_score
    dead = {**perfect, "last_upload": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()}
    assert discovery.score_lead(dead, cfg)[0] < cfg.discovery.min_score


def test_extract_links_from_youtube_redirects():
    html = ('href="https://www.youtube.com/redirect?q=https%3A%2F%2Fwww.tiktok.com%2F%40ffballers&amp;v=1" '
            '"https://www.instagram.com/fantasyfootballers/" https://twitter.com/theffballers')
    links = socials.extract_links(html)
    assert links["tiktok"] == "ffballers" and links["instagram"] == "fantasyfootballers"
    assert links["other"] == ["https://twitter.com/theffballers"]


def test_pitch_template_mentions_the_gap(cfg):
    lead = {"title": "Dynasty Nerds", "long_videos": 100,
            "reasons": dumps(["100+ long-form videos", "no YouTube Shorts", "no tiktok linked", "instagram barely used (4)"])}
    text = discovery.pitch_template(lead)
    assert "Hey Dynasty Nerds team" in text
    assert "You've got 100+ long-form episodes, but no YouTube Shorts, no TikTok and an Instagram with only 4 posts." in text


@pytest.mark.parametrize("utc,expected", [
    ("2026-10-26T19:00:00+00:00", True),    # Mon 09:00 HST
    ("2026-10-26T17:00:00+00:00", False),   # Mon 07:00 HST, before the window
    ("2026-10-25T20:00:00+00:00", False),   # Sunday
])
def test_work_hours_window(cfg, utc, expected):
    assert worker.in_window(cfg, datetime.fromisoformat(utc)) is expected


def test_tick_outside_window_only_publishes(cfg, db, monkeypatch):
    monkeypatch.setattr(worker, "in_window", lambda cfg, now=None: False)
    monkeypatch.setattr(worker.pipeline, "sync_client", lambda *a: pytest.fail("synced outside work hours"))
    stats = worker.tick(cfg, db)
    assert stats["window"] is False and stats["posted"] == 0


@pytest.fixture
def web(cfg, db, monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    clip_file = cfg.dir("clips") / "fantasy-footballers" / "v1_1.mp4"
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    clip_file.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    db.insert("videos", {"id": "v1", "client": "fantasy-footballers", "show": "dfs", "title": "DFS Picks Week 8",
                         "url": "https://www.youtube.com/watch?v=v1", "status": "done", "created_at": now_iso()})
    db.insert("clips", {"video_id": "v1", "client": "fantasy-footballers", "show": "dfs", "kind": "single",
                        "segments": dumps([[100, 140]]), "hook": "Stack the Lions", "title": "Stack the Lions",
                        "caption": "c", "hashtags": dumps(["#dfs"]), "score": 77, "status": "ready",
                        "reason": "bold call", "file_path": str(clip_file)})
    db.insert("leads", {"channel_id": "UC1", "title": "Dynasty Nerds", "url": "https://youtube.com/channel/UC1",
                        "score": 90, "status": "qualified", "reasons": dumps(["no YouTube Shorts"]), "links": dumps([])})
    return TestClient(create_app(cfg, db))


AUTH = ("admin", "pw")


def token(cfg):
    return Vault(cfg).sign("csrf:dashboard")


def test_forms_need_the_csrf_token(web, db, cfg):
    r = web.post("/clips/1/review", data={"decision": "reject"}, auth=AUTH)
    assert r.status_code == 403
    assert db.scalar("SELECT status FROM clips WHERE id=1") == "ready"
    assert 'name="csrf"' in web.get("/review", auth=AUTH).text


def test_dashboard_requires_login(web):
    assert web.get("/").status_code == 401
    assert web.get("/", auth=("admin", "nope")).status_code == 401
    assert web.get("/", auth=AUTH).status_code == 200


@pytest.mark.parametrize("path", ["/", "/review", "/posts", "/videos", "/leads", "/clients", "/review?status=approved"])
def test_pages_render(web, path):
    r = web.get(path, auth=AUTH)
    assert r.status_code == 200, r.text[:500]
    assert "FANTASY" in r.text


def test_review_page_and_approve(web, db, cfg):
    page = web.get("/review", auth=AUTH).text
    assert "Stack the Lions" in page and "Fantasy Footballers DFS" in page and "sample only" in page
    r = web.post("/clips/1/review", data={"csrf": token(cfg), "decision": "approve", "title": "Stack the Lions", "caption": "c",
                                         "hook": "Stack the Lions", "show": "dfs"}, auth=AUTH, follow_redirects=False)
    assert r.status_code == 303 and "prospect" in r.headers["location"]
    assert db.scalar("SELECT status FROM clips WHERE id=1") == "approved"


def test_editing_the_hook_rerenders(web, db, cfg):
    web.post("/clips/1/review", data={"csrf": token(cfg), "decision": "save", "hook": "Fade the chalk"}, auth=AUTH)
    assert db.scalar("SELECT status FROM clips WHERE id=1") == "pending"
    assert db.scalar("SELECT status FROM videos WHERE id='v1'") == "picked"


def test_media_is_served_and_sandboxed(web):
    assert web.get("/media/fantasy-footballers/v1_1.mp4", auth=AUTH).status_code == 200
    assert web.get("/media/../clipper.db", auth=AUTH).status_code == 404
    assert web.get("/media/fantasy-footballers/v1_1.mp4").status_code == 401


def test_connect_flow(web, db, cfg, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    web.post("/clients/fantasy-footballers/connect-link", data={"csrf": token(cfg)}, auth=AUTH)
    link = db.scalar("SELECT token FROM connect_links")
    page = web.get(f"/connect/{link}")           # public, no login
    assert page.status_code == 200 and "Fantasy Footballers Dynasty" in page.text

    r = web.get(f"/connect/{link}/start/dynasty/youtube", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/")
    state = db.scalar("SELECT state FROM oauth_states")

    monkeypatch.setattr(publish.youtube, "exchange", lambda code, redirect: {
        "access_token": "at", "refresh_token": "rt", "expires_at": 9e12, "external_id": "UC9", "display_name": "FF Dynasty"})
    r = web.get(f"/oauth/youtube/callback?state={state}&code=xyz", follow_redirects=False)
    assert r.status_code == 303 and "Connected" in r.headers["location"]
    acct = db.one("SELECT * FROM accounts")
    assert acct["show"] == "dynasty" and acct["display_name"] == "FF Dynasty"
    assert Vault(cfg).open(acct["token"])["refresh_token"] == "rt"
    # state is single use
    assert "expired" in web.get(f"/oauth/youtube/callback?state={state}&code=xyz").text

    assert web.post(f"/connect/{link}/disconnect/dynasty/youtube").status_code == 403
    page = web.get(f"/connect/{link}").text
    assert "Disconnect" in page
    csrf = page.split('name="csrf" value="')[1].split('"')[0]
    web.post(f"/connect/{link}/disconnect/dynasty/youtube", data={"csrf": csrf})
    assert db.scalar("SELECT COUNT(*) FROM accounts") == 0
    assert web.get("/connect/not-a-token").status_code == 404


def test_public_clip_url_requires_signature(web, db, cfg):
    db.insert("posts", {"clip_id": 1, "client": "fantasy-footballers", "show": "dfs", "platform": "instagram",
                        "scheduled_for": now_iso(), "status": "posting"})
    sig = Vault(cfg).sign("1")
    assert web.get(f"/public/clip/1/{sig}.mp4").status_code == 200
    assert web.get("/public/clip/1/bad.mp4").status_code == 404


def test_discover_scores_leads_and_skips_existing_clients(cfg, db, monkeypatch):
    monkeypatch.setattr(discovery.ytdlp, "search", lambda q, n: [
        {"channel_id": "UC-No2ITxJsNt50oJQ3fLmUA"}, {"channel_id": "UCnew"}, {"channel_id": "UCnew"}])
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    def inspect(cid, cfg):
        handle = "@TheFantasyFootballers" if cid.startswith("UC-No2") else "@DynastyDugout"
        return {"channel_id": cid, "handle": handle, "title": handle[1:], "url": f"https://www.youtube.com/channel/{cid}",
                "subscribers": 50_000, "long_videos": 120, "shorts": 0, "avg_minutes": 60, "last_upload": recent}

    monkeypatch.setattr(discovery, "inspect_channel", inspect)
    monkeypatch.setattr(discovery.time, "sleep", lambda s: None)
    cfg.discovery.queries = ["dynasty fantasy football"]
    assert discovery.discover(cfg, db) == 1
    lead = db.one("SELECT * FROM leads")
    assert lead["channel_id"] == "UCnew" and lead["status"] == "qualified" and lead["score"] == 100
    # a second run within two weeks doesn't re-inspect known channels
    assert discovery.discover(cfg, db) == 0
