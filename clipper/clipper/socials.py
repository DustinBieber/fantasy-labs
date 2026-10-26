"""Find a channel's TikTok/Instagram links and check, best-effort, whether they're active."""
from __future__ import annotations

import re
from urllib.parse import unquote

import httpx

from .util import log

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
TIKTOK = re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,24})")
INSTAGRAM = re.compile(r"instagram\.com/(?!p/|reel/|explore/|accounts/)([A-Za-z0-9_.]{2,30})")
OTHER = re.compile(r"https?://(?:www\.)?(twitter\.com|x\.com|patreon\.com|discord\.gg|linktr\.ee)/[^\s\"'<>\\]+")


def _get(url: str) -> str | None:
    try:
        r = httpx.get(
            url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
            cookies={"CONSENT": "YES+1"}, follow_redirects=True, timeout=20,
        )
        return r.text if r.status_code == 200 else None
    except httpx.HTTPError as e:
        log.debug("GET %s failed: %s", url, e)
        return None


def extract_links(text: str) -> dict:
    """Pull social handles out of a page or description (YouTube wraps links in redirects)."""
    text = unquote(unquote(text))
    tiktok = TIKTOK.findall(text)
    instagram = [h for h in INSTAGRAM.findall(text) if h.lower() not in ("instagram", "about")]
    other = sorted({m.group(0).rstrip(".,)") for m in OTHER.finditer(text)})
    return {
        "tiktok": tiktok[0].rstrip(".") if tiktok else None,
        "instagram": instagram[0].rstrip(".") if instagram else None,
        "other": other[:8],
    }


def channel_links(channel_url: str, description: str = "") -> dict:
    page = _get(channel_url.rstrip("/") + "/about") or ""
    return extract_links(description + "\n" + page)


def _count(text: str) -> int | None:
    text = text.replace(",", "").strip().upper()
    mult = 1
    if text.endswith("K"):
        mult, text = 1_000, text[:-1]
    elif text.endswith("M"):
        mult, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except ValueError:
        return None


def tiktok_videos(handle: str) -> int | None:
    """Video count from the public profile page, or None if TikTok blocks the request."""
    page = _get(f"https://www.tiktok.com/@{handle}")
    if not page:
        return None
    m = re.search(r'"videoCount":(\d+)', page)
    return int(m.group(1)) if m else None


def instagram_posts(handle: str) -> int | None:
    """Post count from the public profile's meta description, or None if unavailable."""
    page = _get(f"https://www.instagram.com/{handle}/")
    if not page:
        return None
    m = re.search(r"([\d.,]+[KkMm]?) Posts", page)
    return _count(m.group(1)) if m else None
