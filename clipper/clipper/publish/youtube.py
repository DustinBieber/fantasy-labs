"""YouTube Shorts via the YouTube Data API v3 (Google OAuth + resumable upload)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import PostMeta, PublishError, check, expires_at, file_size

name = "youtube"
label = "YouTube Shorts"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


def _creds() -> tuple[str, str]:
    return os.environ.get("GOOGLE_CLIENT_ID", ""), os.environ.get("GOOGLE_CLIENT_SECRET", "")


def configured() -> bool:
    return all(_creds())


def authorize_url(state: str, redirect_uri: str) -> str:
    cid, _ = _creds()
    return AUTH_URL + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    })


def exchange(code: str, redirect_uri: str) -> dict[str, Any]:
    cid, secret = _creds()
    tok = check(httpx.post(TOKEN_URL, data={
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=30), "Google token exchange")
    token = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
             "expires_at": expires_at(tok.get("expires_in"))}
    me = check(httpx.get(
        "https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=30,
    ), "YouTube channel lookup")
    items = me.get("items") or []
    if not items:
        raise PublishError("That Google account has no YouTube channel.", retryable=False)
    token["external_id"] = items[0]["id"]
    token["display_name"] = items[0]["snippet"]["title"]
    return token


def refresh(token: dict[str, Any]) -> dict[str, Any]:
    if not token.get("refresh_token"):
        raise PublishError("No refresh token; the creator needs to reconnect YouTube.", retryable=False)
    cid, secret = _creds()
    tok = check(httpx.post(TOKEN_URL, data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
    }, timeout=30), "Google token refresh")
    return {**token, "access_token": tok["access_token"], "expires_at": expires_at(tok.get("expires_in"))}


def upload(token: dict[str, Any], path: Path, meta: PostMeta, **_: Any) -> tuple[str, str]:
    size = file_size(path)
    body = {
        "snippet": {
            "title": meta.title.replace("<", "").replace(">", "")[:100],
            "description": meta.description[:4900],
            "tags": [t.lstrip("#") for t in meta.tags][:15],
            "categoryId": "17",                       # Sports
        },
        "status": {"privacyStatus": meta.privacy, "selfDeclaredMadeForKids": False},
    }
    auth = {"Authorization": f"Bearer {token['access_token']}"}
    r = httpx.post(
        UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
        headers={**auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        timeout=60,
    )
    check(r, "YouTube upload init")
    location = r.headers.get("Location")
    if not location:
        raise PublishError("YouTube didn't return an upload URL")
    r = httpx.put(location, content=path.read_bytes(), headers={**auth, "Content-Type": "video/mp4"},
                  timeout=httpx.Timeout(60, write=600, read=600))
    video = check(r, "YouTube upload")
    return video["id"], f"https://www.youtube.com/shorts/{video['id']}"
