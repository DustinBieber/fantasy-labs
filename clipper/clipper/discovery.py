"""Find creators with a deep long-form library and little or no short-form presence."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import socials, ytdlp
from .config import Config
from .db import DB, dumps, loads, now_iso
from .util import log

RECHECK_DAYS = 14


def score_lead(lead: dict, cfg: Config) -> tuple[float, list[str]]:
    d = cfg.discovery
    score, reasons = 0.0, []
    long_videos = lead.get("long_videos") or 0
    shorts = lead.get("shorts")
    subs = lead.get("subscribers")

    if long_videos >= d.min_long_videos:
        score += 30
        reasons.append(f"{long_videos}{'+' if long_videos >= 100 else ''} long-form videos")
    else:
        score += 30 * long_videos / max(1, d.min_long_videos)

    if shorts is None:
        reasons.append("shorts unknown")
    elif shorts <= d.max_shorts:
        score += 30
        reasons.append("no YouTube Shorts")
    elif shorts <= 5:
        score += 10
        reasons.append(f"only {shorts} Shorts")

    for platform, count_key, weight, low in (("tiktok", "tiktok_videos", 15, 10), ("instagram", "instagram_posts", 10, 10)):
        handle, count = lead.get(platform), lead.get(count_key)
        if not handle:
            score += weight
            reasons.append(f"no {platform} linked")
        elif count is not None and count < low:
            score += weight * 0.7
            reasons.append(f"{platform} barely used ({count})")

    if subs is not None and d.min_subscribers <= subs <= d.max_subscribers:
        score += 10
    elif subs is not None:
        score -= 10
        reasons.append("outside subscriber range")

    last = lead.get("last_upload")
    if last:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days
        if age <= d.active_within_days:
            score += 5
        else:
            score -= 40
            reasons.append(f"inactive {age} days")
    return round(max(0.0, min(100.0, score)), 1), reasons


def inspect_channel(channel_id: str, cfg: Config) -> dict:
    """Gather the stats a lead is scored on. Network-heavy: a few requests per channel."""
    base = ytdlp.channel_url(channel_id)
    info = ytdlp.flat_tab(base + "/videos", limit=100, approximate_date=True)
    entries = info["entries"]
    durations = [e["duration"] for e in entries if e.get("duration")]
    long_videos = sum(1 for x in durations if x >= 8 * 60) if durations else len(entries)
    stamps = [e.get("timestamp") for e in entries if e.get("timestamp")]
    last_upload = datetime.fromtimestamp(max(stamps), timezone.utc).isoformat(timespec="seconds") if stamps else None

    try:
        shorts: int | None = len(ytdlp.flat_tab(base + "/shorts", limit=10)["entries"])
    except ytdlp.NoShortsTab:
        shorts = 0
    except Exception as e:
        log.debug("shorts check failed for %s: %s", channel_id, e)
        shorts = None

    lead = {
        "channel_id": channel_id,
        "handle": info.get("uploader_id") or info.get("channel_handle"),
        "title": info.get("channel") or info.get("uploader") or info.get("title"),
        "url": info.get("channel_url") or base,
        "subscribers": info.get("channel_follower_count"),
        "long_videos": long_videos,
        "shorts": shorts,
        "avg_minutes": round(sum(durations) / len(durations) / 60, 1) if durations else None,
        "last_upload": last_upload,
    }
    if cfg.discovery.check_socials:
        links = socials.channel_links(base, info.get("description") or "")
        lead["tiktok"], lead["instagram"], lead["links"] = links["tiktok"], links["instagram"], dumps(links["other"])
        lead["tiktok_videos"] = socials.tiktok_videos(links["tiktok"]) if links["tiktok"] else None
        lead["instagram_posts"] = socials.instagram_posts(links["instagram"]) if links["instagram"] else None
    return lead


def _known(db: DB, cfg: Config, channel_id: str) -> bool:
    row = db.one("SELECT updated_at FROM leads WHERE channel_id=?", (channel_id,))
    if not row:
        return False
    updated = datetime.fromisoformat(row["updated_at"])
    return datetime.now(timezone.utc) - updated < timedelta(days=RECHECK_DAYS)


def discover(cfg: Config, db: DB, queries: list[str] | None = None, max_channels: int | None = None) -> int:
    """Search YouTube, inspect new channels, store scored leads. Returns channels inspected."""
    d = cfg.discovery
    queries = queries or d.queries
    max_channels = max_channels or d.max_new_channels
    clients = {ytdlp.channel_url(c.youtube).lower() for c in cfg.clients}

    candidates: dict[str, str] = {}
    for q in queries:
        try:
            for e in ytdlp.search(q, d.results_per_query):
                cid = e.get("channel_id")
                if cid and cid not in candidates and not _known(db, cfg, cid):
                    candidates[cid] = q
        except Exception as e:
            log.warning("search %r failed: %s", q, e)
        time.sleep(1)

    inspected = 0
    for cid, query in list(candidates.items())[:max_channels]:
        try:
            lead = inspect_channel(cid, cfg)
        except Exception as e:
            log.warning("inspect %s failed: %s", cid, e)
            continue
        ids = {(lead["url"] or "").lower().rstrip("/"), ytdlp.channel_url(cid).lower()}
        if lead.get("handle"):
            ids.add(ytdlp.channel_url(lead["handle"]).lower())
        if ids & clients:
            continue                      # already a creator we work with
        score, reasons = score_lead(lead, cfg)
        existing = db.one("SELECT status FROM leads WHERE channel_id=?", (cid,))
        status = existing["status"] if existing else ("qualified" if score >= d.min_score else "new")
        db.upsert("leads", {
            **lead, "score": score, "reasons": dumps(reasons), "status": status, "found_via": query,
            "created_at": now_iso(), "updated_at": now_iso(),
        }, ["channel_id"])
        inspected += 1
        if score >= d.min_score:
            db.log("discover", f"lead {lead['title']} scored {score}: {', '.join(reasons)}")
        time.sleep(1.5)
    return inspected


def _gap_phrase(reasons: list[str]) -> str:
    phrases = []
    for r in reasons:
        if r == "no YouTube Shorts" or r.startswith("only "):
            phrases.append(r)
        elif r.startswith("no ") and r.endswith(" linked"):
            phrases.append("no " + {"tiktok": "TikTok", "instagram": "Instagram"}.get(r.split()[1], r.split()[1]))
        elif "barely used" in r:
            platform, count = r.split()[0], r.split("(")[-1].rstrip(")")
            phrases.append(("a TikTok" if platform == "tiktok" else "an Instagram") + f" with only {count} posts")
    if not phrases:
        return "very little short-form presence"
    return phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " and " + phrases[-1]


def pitch_template(lead: dict, sender: str = "Fantasy Labs") -> str:
    reasons = loads(lead.get("reasons"), [])
    name = lead.get("title") or "there"
    n = lead.get("long_videos")
    vids = f"{n}{'+' if n and n >= 100 else ''} long-form episodes" if n else "a deep library of long-form episodes"
    return (
        f"Hey {name} team,\n\n"
        f"I've been watching the channel. You've got {vids}, but {_gap_phrase(reasons)}. "
        "That's a lot of great takes that never reach the Shorts, TikTok and Reels audience.\n\n"
        "I run a clipping service for fantasy football shows. We pull the best moments from every new "
        "episode, add captions and a hook, and post them to your accounts on a schedule. Every clip "
        "links back to the full episode. You approve the account connection once and can disconnect anytime.\n\n"
        "I cut a few sample clips from a recent episode. Happy to send them over, no strings attached.\n\n"
        f"- {sender}"
    )


def draft_pitch(lead: dict, cfg: Config, db: DB, sender: str = "Fantasy Labs") -> str:
    """Personalize the pitch with Claude when an AI tier is on; otherwise use the template."""
    from .highlights import claude

    base = pitch_template(lead, sender)
    if claude.unavailable_reason(cfg, db):
        return base
    import anthropic

    model, _ = cfg.ai.resolved()  # type: ignore[misc]
    facts = {k: lead.get(k) for k in ("title", "subscribers", "long_videos", "shorts", "avg_minutes",
                                      "last_upload", "tiktok", "tiktok_videos", "instagram", "instagram_posts")}
    try:
        response = claude.create(
            anthropic.Anthropic(max_retries=2),
            model,
            max_tokens=4000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": (
                "Rewrite this cold outreach email so it's specific to the channel using the facts below. "
                "Keep it under 150 words, plain text, friendly and direct, no hype or fake urgency, and keep "
                "the offer of free sample clips. Return only the email.\n\n"
                f"Facts: {facts}\n\nEmail:\n{base}"
            )}],
        )
    except anthropic.AnthropicError as e:
        log.warning("pitch drafting failed, using template: %s", e)
        return base
    usage = response.usage
    db.insert("ai_usage", {
        "ts": now_iso(), "model": model, "purpose": "pitch", "ref": lead.get("channel_id"),
        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
        "cost_usd": claude.cost_usd(model, usage.input_tokens, usage.output_tokens),
    })
    if response.stop_reason == "refusal":
        return base
    return next((b.text for b in response.content if b.type == "text"), base).strip() or base
