"""Instagram Reels via the Instagram API with Instagram Login (professional accounts)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import PostMeta, PublishError, check, expires_at, file_size

name = "instagram"
label = "Instagram Reels"
AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
SCOPES = "instagram_business_basic,instagram_business_content_publish"
DEFAULT_VERSION = "v23.0"


def _creds() -> tuple[str, str]:
    return os.environ.get("INSTAGRAM_APP_ID", ""), os.environ.get("INSTAGRAM_APP_SECRET", "")


def configured() -> bool:
    return all(_creds())


def authorize_url(state: str, redirect_uri: str) -> str:
    app_id, _ = _creds()
    return AUTH_URL + "?" + urlencode({
        "client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES, "state": state,
    })


def exchange(code: str, redirect_uri: str, version: str = DEFAULT_VERSION) -> dict[str, Any]:
    app_id, secret = _creds()
    short = check(httpx.post(TOKEN_URL, data={
        "client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
        "redirect_uri": redirect_uri, "code": code,
    }, timeout=30), "Instagram token exchange")
    if isinstance(short.get("data"), list) and short["data"]:
        short = short["data"][0]
    long = check(httpx.get(f"{GRAPH}/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": short["access_token"],
    }, timeout=30), "Instagram long-lived token")
    token = {"access_token": long["access_token"], "expires_at": expires_at(long.get("expires_in")),
             "issued_at": time.time()}
    me = check(httpx.get(f"{GRAPH}/{version}/me", params={
        "fields": "user_id,username", "access_token": token["access_token"],
    }, timeout=30), "Instagram profile")
    token["external_id"] = str(me.get("user_id") or me.get("id") or short.get("user_id"))
    token["display_name"] = "@" + me.get("username", "instagram")
    return token


def refresh(token: dict[str, Any]) -> dict[str, Any]:
    # Long-lived tokens last 60 days and can be refreshed once they're a day old.
    if time.time() - token.get("issued_at", 0) < 86400:
        return token
    r = check(httpx.get(f"{GRAPH}/refresh_access_token", params={
        "grant_type": "ig_refresh_token", "access_token": token["access_token"],
    }, timeout=30), "Instagram token refresh")
    return {**token, "access_token": r["access_token"], "expires_at": expires_at(r.get("expires_in")),
            "issued_at": time.time()}


def upload(token: dict[str, Any], path: Path, meta: PostMeta, mode: str = "resumable",
           version: str = DEFAULT_VERSION, **_: Any) -> tuple[str, str | None]:
    user = token["external_id"]
    at = token["access_token"]
    params = {"media_type": "REELS", "caption": meta.description[:2200], "share_to_feed": "true", "access_token": at}
    if mode == "url":
        if not meta.public_url:
            raise PublishError("instagram_upload is 'url' but publish.public_base_url isn't set", retryable=False)
        container = check(httpx.post(f"{GRAPH}/{version}/{user}/media", data={**params, "video_url": meta.public_url},
                                     timeout=60), "Instagram container")
    else:
        container = check(httpx.post(f"{GRAPH}/{version}/{user}/media", data={**params, "upload_type": "resumable"},
                                     timeout=60), "Instagram container")
        r = httpx.post(f"{RUPLOAD}/{version}/{container['id']}", content=path.read_bytes(), headers={
            "Authorization": f"OAuth {at}", "offset": "0", "file_size": str(file_size(path)),
        }, timeout=httpx.Timeout(60, write=600, read=300))
        check(r, "Instagram video upload")
    cid = container["id"]

    deadline = time.time() + 600
    while True:
        status = check(httpx.get(f"{GRAPH}/{version}/{cid}", params={
            "fields": "status_code,status", "access_token": at,
        }, timeout=30), "Instagram container status")
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(f"Instagram couldn't process the video: {status.get('status')}", retryable=False)
        if time.time() > deadline:
            raise PublishError("Instagram is still processing the video after 10 minutes")
        time.sleep(10)

    media = check(httpx.post(f"{GRAPH}/{version}/{user}/media_publish", data={"creation_id": cid, "access_token": at},
                             timeout=60), "Instagram publish")
    link = check(httpx.get(f"{GRAPH}/{version}/{media['id']}", params={"fields": "permalink", "access_token": at},
                           timeout=30), "Instagram permalink")
    return media["id"], link.get("permalink")
