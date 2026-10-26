from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from clipper import publish
from clipper.db import dumps, now_iso
from clipper.publish import tiktok, youtube
from clipper.publish.base import PostMeta, PublishError, Vault


def add_clip(db, tmp_path, show="dynasty", status="ready"):
    db.insert("videos", {"id": "v1", "client": "fantasy-footballers", "show": show, "title": "Dynasty Risers",
                         "url": "https://www.youtube.com/watch?v=v1", "status": "done"})
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 2048)
    return db.insert("clips", {"video_id": "v1", "client": "fantasy-footballers", "show": show, "kind": "single",
                               "segments": dumps([[10, 40]]), "hook": "Sell him now", "title": "Sell him now",
                               "caption": "Why we're selling.", "hashtags": dumps(["#dynasty"]), "score": 80,
                               "status": status, "file_path": str(f)})


def connect(db, cfg, show, platform="youtube"):
    token = {"access_token": "at", "refresh_token": "rt", "expires_at": 9e12, "external_id": "UC1", "display_name": "FF Dynasty"}
    publish.save_account(db, Vault(cfg), "fantasy-footballers", show, platform, token)


def test_vault_roundtrip(cfg):
    v = Vault(cfg)
    blob = v.seal({"access_token": "secret"})
    assert "secret" not in blob and v.open(blob) == {"access_token": "secret"}
    assert (cfg.data_dir / ".secret_key").stat().st_mode & 0o777 == 0o600


def test_next_slot_respects_timezone_and_taken_slots(cfg, db):
    client = cfg.client("fantasy-footballers")       # slots 12:15 and 19:30 America/New_York
    after = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)   # 11:00 EDT
    first = publish.next_slot(db, client, "main", "youtube", after)
    assert first == datetime(2026, 10, 26, 16, 15, tzinfo=timezone.utc)
    db.insert("posts", {"clip_id": 1, "client": client.slug, "show": "main", "platform": "youtube",
                        "scheduled_for": first.isoformat(timespec="seconds"), "status": "queued"})
    assert publish.next_slot(db, client, "main", "youtube", after) == datetime(2026, 10, 26, 23, 30, tzinfo=timezone.utc)
    # other platforms and accounts have their own slots
    assert publish.next_slot(db, client, "main", "tiktok", after) == first


def test_prospects_never_get_posts(cfg, db, tmp_path):
    clip_id = add_clip(db, tmp_path)
    connect(db, cfg, "dynasty")
    notes = publish.review(clip_id, "approve", cfg, db)
    assert "prospect" in notes[0]
    assert db.scalar("SELECT COUNT(*) FROM posts") == 0
    assert db.scalar("SELECT status FROM clips WHERE id=?", (clip_id,)) == "approved"


def test_client_approval_schedules_connected_platforms_only(cfg, db, tmp_path):
    cfg.client("fantasy-footballers").status = "client"
    clip_id = add_clip(db, tmp_path)
    connect(db, cfg, "dynasty", "youtube")
    notes = publish.review(clip_id, "approve", cfg, db, {"title": "Sell him NOW"})
    assert any(n.startswith("youtube: scheduled") for n in notes)
    assert any("tiktok: no account" in n for n in notes)
    post = db.one("SELECT * FROM posts")
    assert post["platform"] == "youtube" and post["show"] == "dynasty"
    # connecting TikTok later picks up the already-approved clip
    connect(db, cfg, "dynasty", "tiktok")
    publish.schedule_approved(cfg, db)
    assert db.scalar("SELECT COUNT(*) FROM posts") == 2
    # rejecting cancels what's queued
    publish.review(clip_id, "reject", cfg, db)
    assert {r["status"] for r in db.all("SELECT status FROM posts")} == {"canceled"}


def test_publish_due_posts_and_retries(cfg, db, tmp_path, monkeypatch):
    cfg.client("fantasy-footballers").status = "client"
    clip_id = add_clip(db, tmp_path)
    connect(db, cfg, "dynasty", "youtube")
    publish.review(clip_id, "approve", cfg, db)
    db.execute("UPDATE posts SET scheduled_for=?", (now_iso(),))

    calls = []

    def fake_upload(token, path, meta, **kw):
        calls.append(meta)
        if len(calls) == 1:
            raise PublishError("quota exceeded (429)", retryable=True)
        return "yt123", "https://www.youtube.com/shorts/yt123"

    monkeypatch.setattr(publish.youtube, "upload", fake_upload)
    assert publish.publish_due(cfg, db) == 0
    post = db.one("SELECT * FROM posts")
    assert post["status"] == "queued" and post["attempts"] == 1 and "quota" in post["error"]

    db.execute("UPDATE posts SET scheduled_for=?", (now_iso(),))
    assert publish.publish_due(cfg, db) == 1
    post = db.one("SELECT * FROM posts")
    assert post["status"] == "posted" and post["remote_url"].endswith("yt123")
    meta = calls[-1]
    assert "Full episode: https://www.youtube.com/watch?v=v1" in meta.description
    assert "#dynasty" in meta.description and "#dynastyfantasyfootball" in meta.tags


def test_interrupted_posts_are_not_retried_blindly(db):
    db.insert("posts", {"clip_id": 1, "client": "c", "show": "s", "platform": "youtube",
                        "scheduled_for": now_iso(), "status": "posting"})
    publish.recover_interrupted(db)
    assert db.scalar("SELECT status FROM posts") == "failed"


class Recorder:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method):
        def fn(url, **kw):
            self.calls.append((method, url, kw))
            status, body, headers = self.responses.pop(0)
            return SimpleNamespace(status_code=status, json=lambda: body, text=str(body), headers=headers)
        return fn


def test_youtube_resumable_upload(monkeypatch, tmp_path):
    f = tmp_path / "c.mp4"
    f.write_bytes(b"x" * 1000)
    rec = Recorder([(200, {}, {"Location": "https://upload.example/session"}), (200, {"id": "abc"}, {})])
    monkeypatch.setattr(youtube.httpx, "post", rec("POST"))
    monkeypatch.setattr(youtube.httpx, "put", rec("PUT"))
    vid, url = youtube.upload({"access_token": "at"}, f, PostMeta("Title <b>", "desc", ["#a", "b"]))
    assert (vid, url) == ("abc", "https://www.youtube.com/shorts/abc")
    init = rec.calls[0][2]
    assert init["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert init["json"]["snippet"]["title"] == "Title b" and init["json"]["snippet"]["tags"] == ["a", "b"]
    assert init["headers"]["X-Upload-Content-Length"] == "1000"
    assert rec.calls[1][1] == "https://upload.example/session"


def test_tiktok_direct_post_chunks_and_privacy(monkeypatch, tmp_path):
    f = tmp_path / "c.mp4"
    size = 70 * 1024 * 1024 + 123          # over the single-chunk limit
    with f.open("wb") as fh:
        fh.truncate(size)
    ok = {"error": {"code": "ok"}}
    rec = Recorder([
        (200, {**ok, "data": {"privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"]}}, {}),
        (200, {**ok, "data": {"publish_id": "p1", "upload_url": "https://up.example"}}, {}),
        (206, {}, {}), (206, {}, {}), (206, {}, {}), (206, {}, {}), (206, {}, {}), (206, {}, {}), (201, {}, {}),
        (200, {**ok, "data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7000123]}}, {}),
    ])
    monkeypatch.setattr(tiktok.httpx, "post", rec("POST"))
    monkeypatch.setattr(tiktok.httpx, "put", rec("PUT"))
    remote_id, url = tiktok.upload({"access_token": "at"}, f, PostMeta("t", "caption #fantasyfootball", []))
    assert remote_id == "7000123" and url is None
    init = rec.calls[1][2]["json"]
    assert init["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert init["source_info"]["total_chunk_count"] == 7 and init["source_info"]["chunk_size"] == 10 * 1024 * 1024
    puts = [c for c in rec.calls if c[0] == "PUT"]
    assert puts[-1][2]["headers"]["Content-Range"] == f"bytes {6 * 10 * 1024 * 1024}-{size - 1}/{size}"


def test_tiktok_api_errors_surface(monkeypatch, tmp_path):
    f = tmp_path / "c.mp4"
    f.write_bytes(b"x")
    rec = Recorder([(200, {"error": {"code": "spam_risk_too_many_posts", "message": "slow down"}}, {})])
    monkeypatch.setattr(tiktok.httpx, "post", rec("POST"))
    with pytest.raises(PublishError, match="spam_risk_too_many_posts"):
        tiktok.upload({"access_token": "at"}, f, PostMeta("t", "c", []))
