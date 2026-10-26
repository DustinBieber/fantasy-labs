"""Approved clips -> scheduled posts -> uploads to the creator's connected accounts."""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Client, Config
from ..db import DB, loads, now_iso, utcnow
from ..util import log
from . import instagram, tiktok, youtube
from .base import PostMeta, Vault

PLATFORMS: dict[str, ModuleType] = {"youtube": youtube, "tiktok": tiktok, "instagram": instagram}
REFRESH_MARGIN = {"youtube": 300, "tiktok": 3600, "instagram": 7 * 86400}


# --- accounts -----------------------------------------------------------------

def save_account(db: DB, vault: Vault, client: str, show: str, platform: str, token: dict[str, Any]) -> None:
    db.upsert("accounts", {
        "client": client, "show": show, "platform": platform,
        "external_id": token.get("external_id"), "display_name": token.get("display_name"),
        "token": vault.seal(token), "expires_at": str(token.get("expires_at") or ""),
        "connected_at": now_iso(), "refreshed_at": now_iso(),
    }, ["client", "show", "platform"])


def fresh_token(db: DB, vault: Vault, account: dict) -> dict[str, Any]:
    token = vault.open(account["token"])
    platform = account["platform"]
    exp = token.get("expires_at")
    if exp is None or exp - time.time() < REFRESH_MARGIN[platform]:
        token = PLATFORMS[platform].refresh(token)
        db.update("accounts", {"client": account["client"], "show": account["show"], "platform": platform},
                  token=vault.seal(token), expires_at=str(token.get("expires_at") or ""), refreshed_at=now_iso())
    return token


def refresh_all(cfg: Config, db: DB) -> None:
    """Keep long-lived tokens alive even when nothing is posting (Instagram expires in 60 days)."""
    vault = Vault(cfg)
    for acct in db.all("SELECT * FROM accounts"):
        try:
            fresh_token(db, vault, dict(acct))
        except Exception as e:
            db.log("auth", f"{acct['client']}/{acct['show']} {acct['platform']} token refresh failed: {e}", "warn")


# --- scheduling -----------------------------------------------------------------

def next_slot(db: DB, client: Client, account_show: str, platform: str, after: datetime | None = None) -> datetime:
    tz = ZoneInfo(client.posting.timezone)
    after = after or utcnow() + timedelta(minutes=10)
    taken = {r["scheduled_for"] for r in db.all(
        "SELECT scheduled_for FROM posts WHERE client=? AND show=? AND platform=? AND status IN ('queued','posting','posted')",
        (client.slug, account_show, platform),
    )}
    start = after.astimezone(tz).date()
    for day in range(90):
        d = start + timedelta(days=day)
        for slot in client.posting.slots:
            hh, mm = (int(x) for x in slot.split(":"))
            when = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).astimezone(timezone.utc)
            if when > after and when.isoformat(timespec="seconds") not in taken:
                return when
    raise RuntimeError(f"no free posting slot in 90 days for {client.slug}/{account_show}/{platform}")


def schedule_clip(clip: dict, cfg: Config, db: DB) -> list[str]:
    """Queue posts for an approved clip on every connected platform. Idempotent."""
    client = cfg.client(clip["client"])
    if not client.can_post:
        return [f"{client.name} is a {client.status}: approved for the sample reel, nothing will post"]
    account_show = client.show(clip["show"]).account_show
    notes = []
    for platform in client.posting.platforms:
        if db.scalar("SELECT 1 FROM posts WHERE clip_id=? AND platform=?", (clip["id"], platform)):
            continue
        if not db.scalar("SELECT 1 FROM accounts WHERE client=? AND show=? AND platform=?",
                         (client.slug, account_show, platform)):
            notes.append(f"{platform}: no account connected for {account_show} yet")
            continue
        when = next_slot(db, client, account_show, platform)
        db.insert("posts", {
            "clip_id": clip["id"], "client": client.slug, "show": account_show, "platform": platform,
            "scheduled_for": when.isoformat(timespec="seconds"), "status": "queued",
            "created_at": now_iso(), "updated_at": now_iso(),
        }, or_ignore=True)
        notes.append(f"{platform}: scheduled {when.astimezone(ZoneInfo(client.posting.timezone)):%a %b %d %I:%M %p}")
    return notes


def schedule_approved(cfg: Config, db: DB) -> None:
    for clip in db.all("SELECT * FROM clips WHERE status='approved' ORDER BY reviewed_at"):
        try:
            schedule_clip(dict(clip), cfg, db)
        except KeyError:
            continue        # client removed from config


def review(clip_id: int, decision: str, cfg: Config, db: DB, edits: dict[str, str] | None = None) -> list[str]:
    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not clip:
        raise KeyError(clip_id)
    fields = {k: v for k, v in (edits or {}).items() if k in ("title", "caption", "hook", "show") and v is not None}
    if decision == "approve":
        if clip["status"] not in ("ready", "approved"):
            return [f"clip is {clip['status']}, only rendered clips can be approved"]
        db.update("clips", {"id": clip_id}, status="approved", reviewed_at=now_iso(), **fields)
        return schedule_clip(dict(db.one("SELECT * FROM clips WHERE id=?", (clip_id,))), cfg, db)
    if decision == "reject":
        db.update("clips", {"id": clip_id}, status="rejected", reviewed_at=now_iso(), **fields)
        db.execute("UPDATE posts SET status='canceled', updated_at=? WHERE clip_id=? AND status='queued'",
                   (now_iso(), clip_id))
        return ["rejected"]
    if fields.get("hook") and fields["hook"] != clip["hook"] and clip["status"] in ("ready", "failed"):
        # The hook is burned into the video, so a new one means a new render.
        db.update("clips", {"id": clip_id}, status="pending", error=None, **fields)
        db.update("videos", {"id": clip["video_id"]}, status="picked")
        return ["saved, re-rendering with the new hook"]
    if fields:
        db.update("clips", {"id": clip_id}, **fields)
    return ["saved"]


# --- posting ------------------------------------------------------------------------

def _fill(template: str, values: dict[str, str]) -> str:
    return template.format_map(defaultdict(str, values)).strip()


def build_meta(clip: dict, video: dict, client: Client, platform: str, cfg: Config, vault: Vault, post_id: int) -> PostMeta:
    show = client.show(clip["show"])
    tags = list(dict.fromkeys(loads(clip["hashtags"], []) + show.hashtags + client.hashtags))[:8]
    values = {
        "hook": clip["hook"] or "", "title": clip["title"] or "", "caption": clip["caption"] or "",
        "show_name": show.name, "client_name": client.name, "video_url": video["url"] or "",
        "channel_url": client.youtube, "hashtags": " ".join(tags),
    }
    public_url = None
    if cfg.public_base_url:
        public_url = f"{cfg.public_base_url}/public/clip/{post_id}/{vault.sign(str(post_id))}.mp4"
    return PostMeta(
        title=clip["title"] or clip["hook"] or video["title"],
        description=_fill(client.caption_template(platform), values),
        tags=tags,
        privacy=client.posting.youtube_privacy,
        public_url=public_url,
    )


def recover_interrupted(db: DB) -> None:
    """A post left 'posting' means the process died mid-upload. Don't risk a double post."""
    db.execute(
        "UPDATE posts SET status='failed', error='interrupted mid-upload: check the account, then retry', "
        "updated_at=? WHERE status='posting'", (now_iso(),),
    )


def publish_due(cfg: Config, db: DB, limit: int = 5) -> int:
    vault = Vault(cfg)
    due = db.all(
        "SELECT * FROM posts WHERE status='queued' AND scheduled_for <= ? ORDER BY scheduled_for LIMIT ?",
        (now_iso(), limit),
    )
    posted = 0
    for post in due:
        post = dict(post)
        clip = db.one("SELECT * FROM clips WHERE id=?", (post["clip_id"],))
        if not clip or clip["status"] != "approved":
            db.update("posts", {"id": post["id"]}, status="canceled", error="clip no longer approved")
            continue
        try:
            client = cfg.client(post["client"])
        except KeyError:
            continue
        if not client.can_post:
            continue        # paused or demoted: hold the post until they're active again
        account = db.one("SELECT * FROM accounts WHERE client=? AND show=? AND platform=?",
                         (post["client"], post["show"], post["platform"]))
        if not account:
            db.update("posts", {"id": post["id"]}, status="failed", error="account disconnected")
            continue
        video = dict(db.one("SELECT * FROM videos WHERE id=?", (clip["video_id"],)))
        attempts = post["attempts"] + 1
        db.update("posts", {"id": post["id"]}, status="posting", attempts=attempts)
        platform = PLATFORMS[post["platform"]]
        try:
            token = fresh_token(db, vault, dict(account))
            meta = build_meta(dict(clip), video, client, post["platform"], cfg, vault, post["id"])
            remote_id, remote_url = platform.upload(
                token, Path(clip["file_path"]), meta,
                mode=cfg.publish.tiktok_mode if post["platform"] == "tiktok" else cfg.publish.instagram_upload,
                version=cfg.publish.graph_api_version,
            )
            db.update("posts", {"id": post["id"]}, status="posted", remote_id=remote_id, remote_url=remote_url, error=None)
            db.log("post", f"posted clip {clip['id']} to {post['platform']} ({post['client']}/{post['show']})")
            posted += 1
        except Exception as e:
            retryable = getattr(e, "retryable", True)
            give_up = not retryable or attempts >= cfg.publish.retry_limit
            retry_at = (utcnow() + timedelta(minutes=30 * attempts)).isoformat(timespec="seconds")
            db.update("posts", {"id": post["id"]}, status="failed" if give_up else "queued",
                      scheduled_for=post["scheduled_for"] if give_up else retry_at, error=str(e)[:1000])
            db.log("post", f"{post['platform']} post for clip {clip['id']} failed: {e}", "error")
            log.warning("post %s failed: %s", post["id"], e)
    return posted
