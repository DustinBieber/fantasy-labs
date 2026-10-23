"""Google API access tokens.

Two ways in, checked in this order:
  1. OAuth refresh token for a Google account (works for Workspace and personal Gmail):
     GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET, and the refresh token from
     ``python -m opshub google-login`` (stored in the database) or GOOGLE_OAUTH_REFRESH_TOKEN.
  2. Service account key file (Workspace; Gmail also needs domain-wide delegation):
     GOOGLE_SERVICE_ACCOUNT_JSON, optionally GOOGLE_DELEGATED_USER to act as a user.
"""
from __future__ import annotations

import time

import httpx

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
KV_REFRESH = "google_refresh_token"
AUTH_REQUIREMENT = "GOOGLE_OAUTH_CLIENT_ID+SECRET (then google-login) or GOOGLE_SERVICE_ACCOUNT_JSON"

_cache: dict[str, tuple[str, float]] = {}


def _stored_refresh_token() -> str | None:
    try:
        from ..services import kv

        return kv.get(KV_REFRESH)
    except Exception:  # no database yet
        return None


def refresh_token(env) -> str | None:
    return env.get("GOOGLE_OAUTH_REFRESH_TOKEN") or _stored_refresh_token()


def method(env) -> str | None:
    if env.get("GOOGLE_OAUTH_CLIENT_ID") and env.get("GOOGLE_OAUTH_CLIENT_SECRET") and refresh_token(env):
        return "oauth"
    if env.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return "service_account"
    return None


def access_token(env, transport: httpx.BaseTransport | None = None) -> str:
    how = method(env)
    if how is None:
        raise RuntimeError(f"Google is not connected: set {AUTH_REQUIREMENT}")
    cache_key = f"{how}:{env.get('GOOGLE_OAUTH_CLIENT_ID') or env.get('GOOGLE_SERVICE_ACCOUNT_JSON')}"
    cached = _cache.get(cache_key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    if how == "oauth":
        with httpx.Client(timeout=30, transport=transport) as client:
            resp = client.post(TOKEN_URL, data={
                "client_id": env["GOOGLE_OAUTH_CLIENT_ID"], "client_secret": env["GOOGLE_OAUTH_CLIENT_SECRET"],
                "refresh_token": refresh_token(env), "grant_type": "refresh_token"})
            resp.raise_for_status()
            body = resp.json()
        token, expires = body["access_token"], time.time() + int(body.get("expires_in", 3600))
    else:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            env["GOOGLE_SERVICE_ACCOUNT_JSON"], scopes=SCOPES, subject=env.get("GOOGLE_DELEGATED_USER") or None)
        creds.refresh(Request())
        token, expires = creds.token, creds.expiry.timestamp() if creds.expiry else time.time() + 3000
    _cache[cache_key] = (token, expires)
    return token


def clear_cache() -> None:
    _cache.clear()


def consent_url(client_id: str, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state})


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    with httpx.Client(timeout=30) as client:
        resp = client.post(TOKEN_URL, data={"client_id": client_id, "client_secret": client_secret, "code": code,
                                             "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        resp.raise_for_status()
        return resp.json()
