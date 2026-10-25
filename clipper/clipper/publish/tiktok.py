"""TikTok via the Content Posting API (direct post, or upload to the creator's drafts inbox)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import PostMeta, PublishError, check, expires_at, file_size

name = "tiktok"
label = "TikTok"
API = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.publish,video.upload"
SINGLE_CHUNK_MAX = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


def _creds() -> tuple[str, str]:
    return os.environ.get("TIKTOK_CLIENT_KEY", ""), os.environ.get("TIKTOK_CLIENT_SECRET", "")


def configured() -> bool:
    return all(_creds())


def _ok(r: httpx.Response, what: str) -> dict[str, Any]:
    body = check(r, what)
    err = body.get("error") or {}
    if isinstance(err, dict) and err.get("code") not in (None, "ok"):
        raise PublishError(f"{what}: {err.get('code')} {err.get('message', '')}", retryable=err.get("code") == "rate_limit_exceeded")
    if isinstance(err, str):     # OAuth endpoints report errors as plain strings
        raise PublishError(f"{what}: {err} {body.get('error_description', '')}", retryable=False)
    return body


def authorize_url(state: str, redirect_uri: str) -> str:
    key, _ = _creds()
    return AUTH_URL + "?" + urlencode({
        "client_key": key, "scope": SCOPES, "response_type": "code", "redirect_uri": redirect_uri, "state": state,
    })


def _token(data: dict[str, str]) -> dict[str, Any]:
    key, secret = _creds()
    tok = _ok(httpx.post(
        f"{API}/oauth/token/", data={"client_key": key, "client_secret": secret, **data},
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30,
    ), "TikTok token")
    return {
        "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
        "expires_at": expires_at(tok.get("expires_in")), "open_id": tok.get("open_id"),
    }


def exchange(code: str, redirect_uri: str) -> dict[str, Any]:
    token = _token({"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    me = _ok(httpx.get(
        f"{API}/user/info/", params={"fields": "open_id,display_name"},
        headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=30,
    ), "TikTok user info")
    user = (me.get("data") or {}).get("user") or {}
    token["external_id"] = user.get("open_id") or token.get("open_id")
    token["display_name"] = user.get("display_name") or "TikTok account"
    return token


def refresh(token: dict[str, Any]) -> dict[str, Any]:
    if not token.get("refresh_token"):
        raise PublishError("No refresh token; the creator needs to reconnect TikTok.", retryable=False)
    fresh = _token({"grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
    return {**token, **{k: v for k, v in fresh.items() if v}}


def _headers(token: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json; charset=UTF-8"}


def upload(token: dict[str, Any], path: Path, meta: PostMeta, mode: str = "direct", **_: Any) -> tuple[str, None]:
    size = file_size(path)
    if size <= SINGLE_CHUNK_MAX:
        chunk, count = size, 1
    else:
        chunk, count = CHUNK, size // CHUNK      # the last chunk absorbs the remainder
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}

    if mode == "inbox":
        init = _ok(httpx.post(f"{API}/post/publish/inbox/video/init/", json={"source_info": source},
                              headers=_headers(token), timeout=30), "TikTok inbox init")
    else:
        creator = _ok(httpx.post(f"{API}/post/publish/creator_info/query/", headers=_headers(token), timeout=30),
                      "TikTok creator info")["data"]
        options = creator.get("privacy_level_options") or ["SELF_ONLY"]
        privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else options[0]
        post_info = {
            "title": meta.description[:2200],
            "privacy_level": privacy,
            "disable_duet": bool(creator.get("duet_disabled")),
            "disable_comment": bool(creator.get("comment_disabled")),
            "disable_stitch": bool(creator.get("stitch_disabled")),
            "video_cover_timestamp_ms": 1000,
        }
        init = _ok(httpx.post(f"{API}/post/publish/video/init/", json={"post_info": post_info, "source_info": source},
                              headers=_headers(token), timeout=30), "TikTok post init")
    publish_id = init["data"]["publish_id"]
    upload_url = init["data"]["upload_url"]

    data = path.read_bytes()
    for i in range(count):
        start = i * chunk
        end = size - 1 if i == count - 1 else start + chunk - 1
        r = httpx.put(upload_url, content=data[start:end + 1], headers={
            "Content-Type": "video/mp4", "Content-Range": f"bytes {start}-{end}/{size}",
        }, timeout=httpx.Timeout(60, write=600))
        if r.status_code not in (200, 201, 206):
            raise PublishError(f"TikTok chunk {i + 1}/{count} upload failed ({r.status_code}): {r.text[:300]}")

    # TikTok's API returns no shareable URL, so keep the post id (or the publish id while processing).
    return _wait(token, publish_id) or publish_id, None


def _wait(token: dict[str, Any], publish_id: str, timeout: float = 240) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = _ok(httpx.post(f"{API}/post/publish/status/fetch/", json={"publish_id": publish_id},
                              headers=_headers(token), timeout=30), "TikTok status")
        data = body.get("data") or {}
        status = data.get("status")
        if status == "FAILED":
            raise PublishError(f"TikTok rejected the post: {data.get('fail_reason')}", retryable=False)
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
            return str(ids[0]) if ids else None
        time.sleep(5)
    return None          # still processing; TikTok finishes it on their side
