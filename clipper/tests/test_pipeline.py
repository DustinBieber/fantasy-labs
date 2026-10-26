from clipper import pipeline
from clipper.db import loads

from .conftest import make_transcript, make_video, needs_ffmpeg

ENTRIES = [
    {"id": "vid_live", "title": "LIVE: Trade Deadline Show", "duration": None, "live_status": "is_live"},
    {"id": "vid_dfs", "title": "DFS Picks Week 8: DraftKings Lineups", "duration": 3600},
    {"id": "vid_main", "title": "Week 8 Start/Sit + Waiver Wire", "duration": 4200},
    {"id": "vid_short", "title": "Quick hit", "duration": 120},
    {"id": "vid_dyn", "title": "Dynasty Rookie Risers", "duration": 3000},
    {"id": "vid_old", "title": "Week 7 Recap", "duration": 3900},
]


def fake_tab(entries):
    def flat_tab(url, limit, approximate_date=False):
        assert url.endswith("/videos")
        return {"entries": list(entries)}
    return flat_tab


def test_first_sync_for_prospect_takes_only_samples(cfg, db, monkeypatch):
    monkeypatch.setattr(pipeline.ytdlp, "flat_tab", fake_tab(ENTRIES))
    client = cfg.client("fantasy-footballers")
    assert pipeline.sync_client(client, cfg, db) == 2          # sample_videos: 2
    rows = {r["id"]: dict(r) for r in db.all("SELECT * FROM videos")}
    assert "vid_live" not in rows                                 # picked up after the stream ends
    assert rows["vid_dfs"]["status"] == "new" and rows["vid_dfs"]["show"] == "dfs"
    assert rows["vid_main"]["status"] == "new" and rows["vid_main"]["show"] == "main"
    assert rows["vid_short"]["status"] == "skipped"
    assert rows["vid_dyn"]["status"] == "skipped" and rows["vid_dyn"]["show"] == "dynasty"

    # A new upload later: still a prospect at its sample limit, so it's recorded but skipped.
    monkeypatch.setattr(pipeline.ytdlp, "flat_tab", fake_tab([{"id": "vid_new", "title": "Best Bets Week 9", "duration": 3000}] + ENTRIES))
    assert pipeline.sync_client(client, cfg, db) == 0
    assert db.one("SELECT status, show FROM videos WHERE id='vid_new'")["show"] == "betting"


def test_client_sync_backfills_then_takes_every_new_upload(cfg, db, monkeypatch):
    client = cfg.client("fantasy-footballers")
    client.status = "client"
    client.backfill = 2
    monkeypatch.setattr(pipeline.ytdlp, "flat_tab", fake_tab(ENTRIES))
    assert pipeline.sync_client(client, cfg, db) == 2
    monkeypatch.setattr(pipeline.ytdlp, "flat_tab", fake_tab([
        {"id": "n1", "title": "Best Bets Week 9", "duration": 3000},
        {"id": "n2", "title": "Week 9 Rankings", "duration": 3000},
    ] + ENTRIES))
    assert pipeline.sync_client(client, cfg, db) == 2


@needs_ffmpeg
def test_process_video_end_to_end_with_free_picker(cfg, db, monkeypatch, tmp_path):
    """sync -> transcript -> heuristic picks -> download -> render -> clips ready for review."""
    source = make_video(tmp_path / "episode.mp4", seconds=400)
    transcript = make_transcript(minutes=6.5, hot_at=200, sponsor_at=80)
    monkeypatch.setattr(pipeline, "get_transcript", lambda vid, cfg: transcript)
    monkeypatch.setattr(pipeline.ytdlp, "download_video", lambda vid, out: source)
    cfg.render.preset = "ultrafast"
    cfg.clips.per_video = 2
    db.insert("videos", {"id": "ep1", "client": "fantasy-footballers", "show": "main",
                         "title": "Week 8 Start/Sit", "url": "https://www.youtube.com/watch?v=ep1", "status": "new"})

    assert pipeline.process_video("ep1", cfg, db) == "done"
    clips = [dict(r) for r in db.all("SELECT * FROM clips WHERE video_id='ep1'")]
    assert clips and all(c["status"] == "ready" for c in clips), [c["error"] for c in clips]
    assert any(c["kind"] == "montage" for c in clips)
    for c in clips:
        assert c["picker"] == "heuristic"
        segs = loads(c["segments"])
        assert all(0 <= s < e <= 400 for s, e in segs)
    assert db.one("SELECT status, transcript_source FROM videos WHERE id='ep1'")["transcript_source"] == "test"


def test_failures_retry_then_give_up(cfg, db, monkeypatch):
    def boom(vid, cfg):
        raise RuntimeError("captions exploded")
    monkeypatch.setattr(pipeline, "get_transcript", boom)
    db.insert("videos", {"id": "bad", "client": "fantasy-footballers", "show": "main", "title": "x", "status": "new"})
    assert [pipeline.process_video("bad", cfg, db) for _ in range(3)] == ["new", "new", "failed"]
    assert "captions exploded" in db.scalar("SELECT error FROM videos WHERE id='bad'")
