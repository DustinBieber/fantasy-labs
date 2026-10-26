"""clipper command line. Run `clipper -h` for the list of commands."""
from __future__ import annotations

import argparse
import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
import threading

from .config import Config, init_data_dir, load_config
from .db import DB, loads, now_iso
from .util import ensure_tool_path, fmt_ts, log, setup_logging


def _ctx(args) -> tuple[Config, DB]:
    cfg = load_config(args.config)
    setup_logging(args.log_level, cfg.path("logs", "clipper.log"))
    return cfg, DB(cfg.db_path)


def cmd_init(args) -> None:
    path = init_data_dir()
    print(f"config: {path}")
    env = path.parent / ".env"
    if not env.exists():
        text = open(os.path.join(os.path.dirname(__file__), "env.example")).read()
        text = text.replace("DASHBOARD_PASSWORD=", f"DASHBOARD_PASSWORD={secrets.token_urlsafe(12)}", 1)
        env.write_text(text)
        env.chmod(0o600)
        print(f"secrets: {env} (dashboard password generated; add your API keys here)")
    (path.parent / "fonts").mkdir(exist_ok=True)


def cmd_doctor(args) -> None:
    ok = True

    def line(good: bool, label: str, detail: str = "", required: bool = True) -> None:
        nonlocal ok
        mark = "ok  " if good else ("FAIL" if required else "warn")
        ok = ok and (good or not required)
        print(f"[{mark}] {label}{': ' + detail if detail else ''}")

    try:
        cfg = load_config(args.config)
        line(True, "config", str(cfg.data_dir / "config.yaml"))
    except Exception as e:
        line(False, "config", str(e))
        sys.exit(1)
    ffmpeg = shutil.which("ffmpeg")
    line(bool(ffmpeg), "ffmpeg", ffmpeg or "install it (brew install ffmpeg)")
    if ffmpeg:
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        line(" subtitles " in filters, "ffmpeg subtitles (libass)",
             "" if " subtitles " in filters else "this ffmpeg can't burn in captions; on a Mac: brew install ffmpeg-full")
    line(bool(shutil.which("ffprobe")), "ffprobe")
    line(bool(shutil.which("deno")), "deno (yt-dlp's JS runtime)", shutil.which("deno") or "pip install deno", required=False)
    import yt_dlp
    line(True, "yt-dlp", yt_dlp.version.__version__)
    fonts = subprocess.run(["fc-list"], capture_output=True, text=True).stdout if shutil.which("fc-list") else ""
    font_family = cfg.render.font.split()[0]
    has_font = font_family.lower() in fonts.lower() or any((cfg.data_dir / "fonts").glob(f"{font_family}*"))
    line(has_font, f"font {cfg.render.font}", "" if has_font else f"drop the .ttf files in {cfg.data_dir / 'fonts'} (a fallback font is used until then)", required=False)
    tier = cfg.ai.resolved()
    key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    line(tier is None or key, f"AI tier {cfg.ai.tier}", (tier[0] if tier else "free heuristics") + ("" if tier is None or key else ": set ANTHROPIC_API_KEY or the free heuristics are used"), required=False)
    whisper = [m for m in ("mlx_whisper", "faster_whisper") if importlib.util.find_spec(m)]
    line(bool(whisper), "local whisper fallback", ", ".join(whisper) or "none; videos without YouTube captions will fail", required=False)
    from .publish import PLATFORMS
    for mod in PLATFORMS.values():
        line(mod.configured(), f"{mod.label} app keys", "" if mod.configured() else "not set; creators can't connect this platform yet", required=False)
    line(bool(cfg.public_base_url), "public_base_url", cfg.public_base_url or "not set; creator connect links only work locally", required=False)
    line(bool(os.environ.get("DASHBOARD_PASSWORD")), "dashboard password", "" if os.environ.get("DASHBOARD_PASSWORD") else "not set; the dashboard is open to anyone who can reach it", required=False)
    for c in cfg.clients:
        line(True, f"creator {c.name}", f"{c.status}, shows: {', '.join(s.slug for s in c.shows)}")
    sys.exit(0 if ok else 1)


def cmd_serve(args) -> None:
    import uvicorn

    from .web.app import create_app
    from .worker import run_forever

    cfg, db = _ctx(args)
    stop = threading.Event()
    if not args.no_worker:
        threading.Thread(target=run_forever, args=(cfg, db, stop), daemon=True, name="worker").start()
    if not os.environ.get("DASHBOARD_PASSWORD"):
        log.warning("DASHBOARD_PASSWORD is not set; the dashboard has no login")
    try:
        uvicorn.run(create_app(cfg, db), host=args.host, port=args.port, log_level="warning")
    finally:
        stop.set()


def cmd_worker(args) -> None:
    from .worker import run_forever

    cfg, db = _ctx(args)
    run_forever(cfg, db)


def cmd_tick(args) -> None:
    from .worker import tick

    cfg, db = _ctx(args)
    print(tick(cfg, db, force=True))


def cmd_sync(args) -> None:
    from .pipeline import sync_client

    cfg, db = _ctx(args)
    for c in cfg.clients:
        if not args.client or c.slug == args.client:
            print(f"{c.name}: {sync_client(c, cfg, db)} new episodes queued")


def cmd_process(args) -> None:
    from .pipeline import next_videos, process_video

    cfg, db = _ctx(args)
    ids = [args.video] if args.video else next_videos(db, cfg, args.limit)
    if not ids:
        print("nothing queued; run `clipper sync` or `clipper add-video`")
    for vid in ids:
        print(f"{vid}: {process_video(vid, cfg, db)}")


def cmd_add_video(args) -> None:
    from .pipeline import add_video, process_video

    cfg, db = _ctx(args)
    vid = add_video(args.url, cfg.client(args.client), cfg, db, show=args.show)
    print(f"queued {vid}")
    if args.now:
        print(f"{vid}: {process_video(vid, cfg, db)}")


def cmd_discover(args) -> None:
    from .discovery import discover

    cfg, db = _ctx(args)
    n = discover(cfg, db, queries=args.query or None, max_channels=args.max)
    print(f"inspected {n} channels")
    cmd_leads(argparse.Namespace(**{**vars(args), "status": "qualified"}), (cfg, db))


def cmd_leads(args, ctx=None) -> None:
    cfg, db = ctx or _ctx(args)
    for l in db.all("SELECT * FROM leads WHERE status=? ORDER BY score DESC LIMIT 50", (args.status,)):
        print(f"{l['score']:>5.1f}  {l['title']}  ({l['channel_id']})  subs={l['subscribers']} long={l['long_videos']} "
              f"shorts={l['shorts']} tiktok={l['tiktok'] or '-'} ig={l['instagram'] or '-'}")
        print(f"       {', '.join(loads(l['reasons'], []))}")


def cmd_pitch(args) -> None:
    from .discovery import draft_pitch

    cfg, db = _ctx(args)
    lead = db.one("SELECT * FROM leads WHERE channel_id=?", (args.channel_id,))
    if not lead:
        sys.exit(f"no lead {args.channel_id}; run `clipper discover` first")
    pitch = draft_pitch(dict(lead), cfg, db)
    db.update("leads", {"channel_id": args.channel_id}, pitch=pitch)
    print(pitch)


def cmd_review(args) -> None:
    cfg, db = _ctx(args)
    for c in db.all("SELECT * FROM clips WHERE status=? ORDER BY score DESC", (args.status,)):
        segs = loads(c["segments"], [])
        print(f"#{c['id']:<5} {c['score']:>3.0f}  [{c['client']}/{c['show']}] {c['kind']:<7} "
              f"{fmt_ts(segs[0][0])} +{sum(e - s for s, e in segs):.0f}s  {c['hook']}")
        print(f"       {c['file_path'] or c['error'] or ''}")


def cmd_decide(args) -> None:
    from .publish import review

    cfg, db = _ctx(args)
    for cid in args.clip_ids:
        print(f"#{cid}: {'; '.join(review(cid, args.decision, cfg, db))}")


def cmd_publish(args) -> None:
    from .publish import publish_due, schedule_approved

    cfg, db = _ctx(args)
    schedule_approved(cfg, db)
    print(f"posted {publish_due(cfg, db)}")


def cmd_connect_link(args) -> None:
    cfg, db = _ctx(args)
    cfg.client(args.client)
    token = secrets.token_urlsafe(18)
    db.execute("DELETE FROM connect_links WHERE client=?", (args.client,))
    db.insert("connect_links", {"token": token, "client": args.client, "created_at": now_iso()})
    base = cfg.public_base_url or "http://localhost:8787"
    print(f"{base}/connect/{token}")


def cmd_spend(args) -> None:
    from .highlights.claude import month_spend

    cfg, db = _ctx(args)
    print(f"this month: ${month_spend(db):.4f} of ${cfg.ai.monthly_budget_usd:.2f} budget (tier {cfg.ai.tier})")
    for r in db.all("SELECT model, purpose, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, SUM(cost_usd) c "
                    "FROM ai_usage GROUP BY model, purpose ORDER BY c DESC"):
        print(f"  {r['model']:<20} {r['purpose']:<6} calls={r['n']:<4} in={r['i']:<9} out={r['o']:<8} ${r['c']:.4f}")


def main(argv: list[str] | None = None) -> None:
    ensure_tool_path()
    p = argparse.ArgumentParser(prog="clipper", description="Fantasy Labs clipping service")
    p.add_argument("--config", help="path to config.yaml (default: $CLIPPER_DATA/config.yaml)")
    p.add_argument("--log-level", default=os.environ.get("CLIPPER_LOG_LEVEL", "INFO"))
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, help: str):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    add("init", cmd_init, "create the data dir, config.yaml and .env")
    add("doctor", cmd_doctor, "check tools, keys and config")
    sp = add("serve", cmd_serve, "dashboard + background worker (what the sandbox runs)")
    sp.add_argument("--host", default=os.environ.get("CLIPPER_HOST", "127.0.0.1"))
    sp.add_argument("--port", type=int, default=int(os.environ.get("CLIPPER_PORT", "8787")))
    sp.add_argument("--no-worker", action="store_true")
    add("worker", cmd_worker, "background worker only")
    add("tick", cmd_tick, "run one worker pass now, ignoring work hours")
    sp = add("sync", cmd_sync, "check creators for new uploads")
    sp.add_argument("--client")
    sp = add("process", cmd_process, "transcribe, pick, download and render queued episodes")
    sp.add_argument("--video")
    sp.add_argument("--limit", type=int, default=1)
    sp = add("add-video", cmd_add_video, "queue one specific episode")
    sp.add_argument("url")
    sp.add_argument("--client", required=True)
    sp.add_argument("--show")
    sp.add_argument("--now", action="store_true", help="process it immediately")
    sp = add("discover", cmd_discover, "search YouTube for creators without short-form")
    sp.add_argument("--query", action="append")
    sp.add_argument("--max", type=int)
    sp = add("leads", cmd_leads, "list leads")
    sp.add_argument("--status", default="qualified")
    sp = add("pitch", cmd_pitch, "draft an outreach pitch for a lead")
    sp.add_argument("channel_id")
    sp = add("review", cmd_review, "list clips")
    sp.add_argument("--status", default="ready")
    for decision in ("approve", "reject"):
        sp = add(decision, cmd_decide, f"{decision} clips by id")
        sp.add_argument("clip_ids", type=int, nargs="+")
        sp.set_defaults(decision=decision)
    add("publish", cmd_publish, "post anything that's due now")
    sp = add("connect-link", cmd_connect_link, "make a link a creator uses to connect accounts")
    sp.add_argument("client")
    add("spend", cmd_spend, "AI spend this month")

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
