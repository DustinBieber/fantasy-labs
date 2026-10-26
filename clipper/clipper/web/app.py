"""Review dashboard + creator connect pages."""
from __future__ import annotations

import hmac
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .. import discovery, publish, worker
from ..config import PLATFORMS, Config
from ..db import DB, loads, now_iso, utcnow
from ..highlights.claude import month_spend
from ..publish.base import PublishError, Vault
from ..util import fmt_ts, log

HERE = Path(__file__).parent
security = HTTPBasic(auto_error=False)


def create_app(cfg: Config, db: DB) -> FastAPI:
    app = FastAPI(title="Clipper", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["ts"] = fmt_ts
    templates.env.filters["loads"] = lambda v: loads(v, [])
    templates.env.filters["local"] = lambda iso, tz=cfg.worker.timezone: (
        datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%a %b %d, %I:%M %p") if iso else ""
    )
    templates.env.globals["platforms"] = PLATFORMS
    vault = Vault(cfg)

    # Basic auth rides along on cross-site form posts, so every form also carries a signed token.
    csrf_token = vault.sign("csrf:dashboard")
    templates.env.globals["csrf_input"] = Markup(f'<input type="hidden" name="csrf" value="{csrf_token}">')
    templates.env.globals["disconnect_csrf"] = lambda token, show, platform: Markup(
        f'<input type="hidden" name="csrf" value="{vault.sign(f"csrf:{token}:{show}:{platform}")}">'
    )

    def check_csrf(csrf: str = Form("")) -> None:
        if not hmac.compare_digest(csrf, csrf_token):
            raise HTTPException(403, "This form expired. Reload the page and try again.")

    def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
        password = os.environ.get("DASHBOARD_PASSWORD", "")
        if not password:
            return
        user = os.environ.get("DASHBOARD_USER", "admin")
        ok = creds and hmac.compare_digest(creds.username, user) and hmac.compare_digest(creds.password, password)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="clipper"'})

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("msg", request.query_params.get("msg"))
        return templates.TemplateResponse(request, name, {"cfg": cfg, "nav": name.split(".")[0], **ctx})

    def back(url: str, msg: str | None = None) -> RedirectResponse:
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
        return RedirectResponse(url, status_code=303)

    def base_url(request: Request) -> str:
        return cfg.public_base_url or str(request.base_url).rstrip("/")

    def background(target, *args) -> bool:
        if not worker.WORK_LOCK.acquire(blocking=False):
            return False

        def run():
            try:
                target(*args)
            except Exception:
                log.exception("background job failed")
            finally:
                worker.WORK_LOCK.release()

        threading.Thread(target=run, daemon=True).start()
        return True

    # --- dashboard ---------------------------------------------------------------

    @app.get("/", dependencies=[Depends(auth)])
    def index(request: Request):
        counts = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM clips GROUP BY status")}
        posts = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM posts GROUP BY status")}
        videos = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM videos GROUP BY status")}
        leads = db.scalar("SELECT COUNT(*) FROM leads WHERE status='qualified'")
        last_tick = db.get_kv("worker.last_tick")
        return page(
            request, "index.html", counts=counts, posts=posts, videos=videos, leads=leads,
            spend=month_spend(db), budget=cfg.ai.monthly_budget_usd, last_tick=last_tick,
            in_window=worker.in_window(cfg), busy=worker.WORK_LOCK.locked(),
            events=db.all("SELECT * FROM events ORDER BY id DESC LIMIT 40"),
            tier=cfg.ai.tier, model=(cfg.ai.resolved() or ("heuristics", ""))[0],
        )

    @app.post("/run", dependencies=[Depends(auth), Depends(check_csrf)])
    def run_now():
        started = background(worker.tick, cfg, db, True)
        return back("/", "Worker run started" if started else "Worker is busy, try again in a bit")

    @app.get("/review", dependencies=[Depends(auth)])
    def review_page(request: Request, status: str = "ready", client: str = "", show: str = ""):
        sql, args = "SELECT c.*, v.title AS video_title, v.url AS video_url FROM clips c JOIN videos v ON v.id=c.video_id WHERE c.status=?", [status]
        if client:
            sql += " AND c.client=?"
            args.append(client)
        if show:
            sql += " AND c.show=?"
            args.append(show)
        sql += " ORDER BY c.score DESC, c.id DESC LIMIT 60"
        clips = [dict(r) for r in db.all(sql, args)]
        for c in clips:
            c["posts"] = db.all("SELECT * FROM posts WHERE clip_id=?", (c["id"],))
            segs = loads(c["segments"], [])
            c["length"] = sum(e - s for s, e in segs)
            c["start"] = segs[0][0] if segs else 0
        return page(request, "review.html", clips=clips, status=status, client=client, show=show)

    @app.post("/clips/{clip_id}/review", dependencies=[Depends(auth), Depends(check_csrf)])
    def review_clip(clip_id: int, decision: str = Form(...), title: str = Form(None), caption: str = Form(None),
                    hook: str = Form(None), show: str = Form(None), next_url: str = Form("/review")):
        notes = publish.review(clip_id, decision, cfg, db, {"title": title, "caption": caption, "hook": hook, "show": show})
        return back(next_url, f"Clip {clip_id}: " + "; ".join(notes))

    @app.get("/media/{path:path}", dependencies=[Depends(auth)])
    def media(path: str):
        root = cfg.dir("clips").resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    @app.get("/posts", dependencies=[Depends(auth)])
    def posts_page(request: Request):
        rows = db.all(
            "SELECT p.*, c.hook, c.title, c.file_path FROM posts p JOIN clips c ON c.id=p.clip_id "
            "ORDER BY CASE p.status WHEN 'queued' THEN 0 WHEN 'posting' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, "
            "p.scheduled_for DESC LIMIT 200"
        )
        return page(request, "posts.html", posts=rows)

    @app.post("/posts/{post_id}/{action}", dependencies=[Depends(auth), Depends(check_csrf)])
    def post_action(post_id: int, action: str):
        if action == "retry":
            db.update("posts", {"id": post_id}, status="queued", scheduled_for=now_iso(), error=None, attempts=0)
        elif action == "cancel":
            db.update("posts", {"id": post_id}, status="canceled")
        return back("/posts", f"Post {post_id}: {action}")

    @app.get("/videos", dependencies=[Depends(auth)])
    def videos_page(request: Request, client: str = ""):
        sql = "SELECT v.*, (SELECT COUNT(*) FROM clips c WHERE c.video_id=v.id) AS clip_count FROM videos v"
        args: list = []
        if client:
            sql += " WHERE v.client=?"
            args.append(client)
        rows = db.all(sql + " ORDER BY v.created_at DESC LIMIT 200", args)
        return page(request, "videos.html", videos=rows, client=client)

    @app.post("/videos/{video_id}/{action}", dependencies=[Depends(auth), Depends(check_csrf)])
    def video_action(video_id: str, action: str, show: str = Form(None)):
        if action == "requeue":
            db.update("videos", {"id": video_id}, status="new", attempts=0, error=None)
            db.execute("DELETE FROM clips WHERE video_id=? AND status IN ('pending','failed')", (video_id,))
        elif action == "skip":
            db.update("videos", {"id": video_id}, status="skipped", error="skipped from dashboard")
        elif action == "show" and show:
            db.update("videos", {"id": video_id}, show=show)
        return back("/videos", f"{video_id}: {action}")

    @app.get("/leads", dependencies=[Depends(auth)])
    def leads_page(request: Request, status: str = "qualified"):
        rows = db.all("SELECT * FROM leads WHERE status=? ORDER BY score DESC LIMIT 200", (status,))
        counts = {r["status"]: r["n"] for r in db.all("SELECT status, COUNT(*) n FROM leads GROUP BY status")}
        return page(request, "leads.html", leads=rows, status=status, counts=counts)

    @app.post("/leads/{channel_id}/{action}", dependencies=[Depends(auth), Depends(check_csrf)])
    def lead_action(channel_id: str, action: str, status: str = Form(None)):
        lead = db.one("SELECT * FROM leads WHERE channel_id=?", (channel_id,))
        if not lead:
            raise HTTPException(404)
        if action == "pitch":
            db.update("leads", {"channel_id": channel_id}, pitch=discovery.draft_pitch(dict(lead), cfg, db))
        elif action == "status" and status in ("new", "qualified", "pitched", "client", "passed"):
            db.update("leads", {"channel_id": channel_id}, status=status)
        return back(f"/leads?status={lead['status']}#{channel_id}")

    @app.post("/discover", dependencies=[Depends(auth), Depends(check_csrf)])
    def discover_now():
        started = background(discovery.discover, cfg, db)
        return back("/leads", "Discovery started, refresh in a few minutes" if started else "Worker is busy, try again in a bit")

    @app.get("/clients", dependencies=[Depends(auth)])
    def clients_page(request: Request):
        accounts = {(a["client"], a["show"], a["platform"]): a for a in db.all("SELECT * FROM accounts")}
        links = {r["client"]: r["token"] for r in db.all("SELECT client, token FROM connect_links ORDER BY created_at")}
        stats = {r["client"]: dict(r) for r in db.all(
            "SELECT client, COUNT(*) total, SUM(status='done') done FROM videos GROUP BY client")}
        return page(request, "clients.html", accounts=accounts, links=links, stats=stats,
                    configured={p: publish.PLATFORMS[p].configured() for p in PLATFORMS},
                    base=base_url(request))

    @app.post("/clients/{slug}/connect-link", dependencies=[Depends(auth), Depends(check_csrf)])
    def new_connect_link(slug: str):
        cfg.client(slug)
        db.execute("DELETE FROM connect_links WHERE client=?", (slug,))
        db.insert("connect_links", {"token": secrets.token_urlsafe(18), "client": slug, "created_at": now_iso()})
        return back("/clients", "New connect link created. Send it to the creator.")

    # --- public: creator account connection ---------------------------------------

    def link_client(token: str):
        row = db.one("SELECT client FROM connect_links WHERE token=?", (token,))
        if not row:
            raise HTTPException(404, "This link has expired. Ask for a new one.")
        return cfg.client(row["client"])

    @app.get("/connect/{token}")
    def connect_page(request: Request, token: str):
        client = link_client(token)
        accounts = {(a["show"], a["platform"]): a for a in db.all("SELECT * FROM accounts WHERE client=?", (client.slug,))}
        shows = [s for s in client.shows if not s.accounts]
        return page(request, "connect.html", client=client, shows=shows, token=token, accounts=accounts,
                    configured={p: publish.PLATFORMS[p].configured() for p in PLATFORMS})

    @app.get("/connect/{token}/start/{show}/{platform}")
    def connect_start(request: Request, token: str, show: str, platform: str):
        client = link_client(token)
        if platform not in publish.PLATFORMS or show not in {s.slug for s in client.shows}:
            raise HTTPException(404)
        mod = publish.PLATFORMS[platform]
        if not mod.configured():
            return back(f"/connect/{token}", f"{mod.label} isn't set up on our side yet.")
        state = secrets.token_urlsafe(24)
        db.insert("oauth_states", {"state": state, "client": client.slug, "show": show, "platform": platform,
                                   "link": token, "created_at": now_iso()})
        return RedirectResponse(mod.authorize_url(state, f"{base_url(request)}/oauth/{platform}/callback"), status_code=303)

    @app.get("/oauth/{platform}/callback")
    def oauth_callback(request: Request, platform: str, state: str = "", code: str = "", error: str = "",
                       error_description: str = ""):
        row = db.one("SELECT * FROM oauth_states WHERE state=? AND platform=?", (state, platform))
        db.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        if not row or utcnow() - datetime.fromisoformat(row["created_at"]) > timedelta(minutes=30):
            return page(request, "message.html", title="Link expired", body="Please start the connection again.")
        if error or not code:
            return back(f"/connect/{row['link']}", f"Connection canceled: {error_description or error or 'no code'}")
        mod = publish.PLATFORMS[platform]
        try:
            token = mod.exchange(code, f"{base_url(request)}/oauth/{platform}/callback")
        except PublishError as e:
            log.warning("oauth exchange failed: %s", e)
            return back(f"/connect/{row['link']}", f"{mod.label} connection failed: {e}")
        publish.save_account(db, vault, row["client"], row["show"], platform, token)
        db.log("auth", f"{row['client']}/{row['show']}: connected {mod.label} as {token.get('display_name')}")
        return back(f"/connect/{row['link']}", f"Connected {mod.label} as {token.get('display_name')}")

    @app.post("/connect/{token}/disconnect/{show}/{platform}")
    def disconnect(token: str, show: str, platform: str, csrf: str = Form("")):
        client = link_client(token)
        if not hmac.compare_digest(csrf, vault.sign(f"csrf:{token}:{show}:{platform}")):
            raise HTTPException(403, "This form expired. Reload the page and try again.")
        db.execute("DELETE FROM accounts WHERE client=? AND show=? AND platform=?", (client.slug, show, platform))
        db.execute("UPDATE posts SET status='canceled', updated_at=? WHERE client=? AND show=? AND platform=? AND status='queued'",
                   (now_iso(), client.slug, show, platform))
        db.log("auth", f"{client.slug}/{show}: disconnected {platform}")
        return back(f"/connect/{token}", "Disconnected. Nothing more will post to that account.")

    @app.get("/public/clip/{post_id}/{sig}.mp4")
    def public_clip(post_id: int, sig: str):
        if not hmac.compare_digest(sig, vault.sign(str(post_id))):
            raise HTTPException(404)
        row = db.one("SELECT c.file_path FROM posts p JOIN clips c ON c.id=p.clip_id WHERE p.id=? AND p.status IN ('queued','posting')", (post_id,))
        if not row or not row["file_path"] or not Path(row["file_path"]).is_file():
            raise HTTPException(404)
        return FileResponse(row["file_path"], media_type="video/mp4")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "last_tick": db.get_kv("worker.last_tick")}

    return app
