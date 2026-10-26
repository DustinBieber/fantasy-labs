"""Google OAuth plus the slices of Calendar, Drive and Gmail the hub uses, over plain REST."""
import json
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..config import settings
from ..crypto import decrypt_json, encrypt_json
from ..models import Event, Family, Integration, OAuthState
from ..timeutil import to_utc_naive, utcnow
from . import http

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",  # only files this app creates; no access to the rest of Drive
    "https://www.googleapis.com/auth/gmail.send",  # send alerts/digests; cannot read the inbox
]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CAL_API = "https://www.googleapis.com/calendar/v3"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


class GoogleNotConfigured(RuntimeError):
    pass


def start_auth(db: Session, family: Family) -> str:
    if not (settings.google_client_id and settings.google_client_secret):
        raise GoogleNotConfigured("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the server first.")
    state = secrets.token_urlsafe(24)
    db.add(OAuthState(state=state, family_id=family.id))
    db.commit()
    return AUTH_URL + "?" + urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })


def finish_auth(db: Session, state: str, code: str) -> Integration:
    row = db.get(OAuthState, state)
    if not row or row.created_at < utcnow() - timedelta(minutes=15):
        raise ValueError("OAuth state expired; start the connection again.")
    family_id = row.family_id
    db.delete(row)
    with http.client() as c:
        resp = c.post(TOKEN_URL, data={
            "code": code, "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri, "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        tokens = resp.json()
        email = c.get(USERINFO_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"}).json().get("email", "")
    tokens["expires_at"] = (utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))).isoformat()
    integ = db.query(Integration).filter_by(family_id=family_id, provider="google").first()
    if integ is None:
        integ = Integration(family_id=family_id, provider="google", options={"calendar_id": "primary"})
        db.add(integ)
    elif "refresh_token" not in tokens:
        tokens["refresh_token"] = decrypt_json(integ.secret_enc).get("refresh_token")
    integ.account = email
    integ.secret_enc = encrypt_json(tokens)
    integ.last_error = ""
    db.commit()
    return integ


def access_token(db: Session, integ: Integration) -> str:
    tokens = decrypt_json(integ.secret_enc)
    if datetime.fromisoformat(tokens["expires_at"]) > utcnow() + timedelta(seconds=60):
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise RuntimeError("Google access expired; reconnect Google in Settings.")
    with http.client() as c:
        resp = c.post(TOKEN_URL, data={
            "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        fresh = resp.json()
    tokens["access_token"] = fresh["access_token"]
    tokens["expires_at"] = (utcnow() + timedelta(seconds=int(fresh.get("expires_in", 3600)))).isoformat()
    integ.secret_enc = encrypt_json(tokens)
    db.commit()
    return tokens["access_token"]


def _auth(db, integ) -> dict:
    return {"Authorization": f"Bearer {access_token(db, integ)}"}


# ---------------------------------------------------------------- Calendar

def _parse_gcal_time(t: dict) -> tuple[datetime, bool]:
    if "date" in t:
        return datetime.combine(date.fromisoformat(t["date"]), datetime.min.time()), True
    return to_utc_naive(datetime.fromisoformat(t["dateTime"].replace("Z", "+00:00"))), False


def sync_calendar(db: Session, integ: Integration, days: int = 60) -> dict:
    """Pull upcoming Google events into the hub and push local events flagged for Google."""
    cal_id = integ.options.get("calendar_id") or "primary"
    headers = _auth(db, integ)
    now = utcnow()
    pulled = pushed = 0
    seen: set[str] = set()
    with http.client() as c:
        page_token = None
        while True:
            params = {"timeMin": now.isoformat() + "Z", "timeMax": (now + timedelta(days=days)).isoformat() + "Z",
                      "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
            if page_token:
                params["pageToken"] = page_token
            resp = c.get(f"{CAL_API}/calendars/{cal_id}/events", headers=headers, params=params)
            resp.raise_for_status()
            body = resp.json()
            for item in body.get("items", []):
                if item.get("status") == "cancelled" or "start" not in item:
                    continue
                # Events this hub pushed to Google come back here; they already exist locally.
                if item.get("extendedProperties", {}).get("private", {}).get("familyHubId"):
                    continue
                seen.add(item["id"])
                start, all_day = _parse_gcal_time(item["start"])
                end, _ = _parse_gcal_time(item["end"]) if "end" in item else (None, False)
                ev = db.query(Event).filter_by(family_id=integ.family_id, source="google", external_id=item["id"]).first()
                if ev is None:
                    ev = Event(family_id=integ.family_id, source="google", external_id=item["id"], member_ids=[])
                    db.add(ev)
                ev.title = item.get("summary") or "(no title)"
                ev.start, ev.end, ev.all_day = start, end, all_day
                ev.location = (item.get("location") or "")[:200]
                ev.notes = item.get("description") or ""
                pulled += 1
            page_token = body.get("nextPageToken")
            if not page_token:
                break

        # Drop future Google-sourced events that were deleted on Google's side.
        for ev in db.query(Event).filter(Event.family_id == integ.family_id, Event.source == "google",
                                         Event.start >= now, Event.start <= now + timedelta(days=days)):
            if ev.external_id not in seen:
                db.delete(ev)

        for ev in db.query(Event).filter(Event.family_id == integ.family_id, Event.source == "local"):
            if "google" not in (ev.push_to or []) or (ev.pushed or {}).get("google"):
                continue
            resp = c.post(f"{CAL_API}/calendars/{cal_id}/events", headers=headers, json=_to_gcal(ev))
            resp.raise_for_status()
            ev.pushed = {**(ev.pushed or {}), "google": resp.json()["id"]}
            pushed += 1
    integ.last_sync_at = utcnow()
    integ.last_error = ""
    db.commit()
    return {"pulled": pulled, "pushed": pushed}


def _to_gcal(ev: Event) -> dict:
    if ev.all_day:
        end = (ev.end or ev.start).date()
        if end <= ev.start.date():
            end = ev.start.date() + timedelta(days=1)
        start_obj, end_obj = {"date": ev.start.date().isoformat()}, {"date": end.isoformat()}
    else:
        end = ev.end or ev.start + timedelta(hours=1)
        start_obj, end_obj = {"dateTime": ev.start.isoformat() + "Z"}, {"dateTime": end.isoformat() + "Z"}
    return {"summary": ev.title, "location": ev.location, "description": ev.notes,
            "start": start_obj, "end": end_obj,
            "extendedProperties": {"private": {"familyHubId": str(ev.id)}}}


# ---------------------------------------------------------------- Drive

def _ensure_folder(db: Session, integ: Integration, c) -> str:
    folder_id = integ.options.get("drive_folder_id")
    if folder_id:
        return folder_id
    resp = c.post(f"{DRIVE_API}/files", headers=_auth(db, integ), params={"fields": "id"},
                  json={"name": "Family Hub", "mimeType": "application/vnd.google-apps.folder"})
    resp.raise_for_status()
    integ.options = {**integ.options, "drive_folder_id": resp.json()["id"]}
    db.commit()
    return integ.options["drive_folder_id"]


def drive_upload(db: Session, integ: Integration, name: str, content: bytes, mime: str) -> dict:
    boundary = "familyhub" + secrets.token_hex(8)
    with http.client(timeout=60) as c:
        meta = {"name": name, "parents": [_ensure_folder(db, integ, c)]}
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--".encode()
        resp = c.post(DRIVE_UPLOAD, params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                      headers={**_auth(db, integ), "Content-Type": f"multipart/related; boundary={boundary}"},
                      content=body)
        resp.raise_for_status()
        f = resp.json()
    return {"name": f["name"], "url": f.get("webViewLink", ""), "drive_id": f["id"]}


# ---------------------------------------------------------------- Gmail

def gmail_send(db: Session, integ: Integration, raw_b64url: str) -> None:
    with http.client() as c:
        c.post(f"{GMAIL_API}/users/me/messages/send", headers=_auth(db, integ),
               json={"raw": raw_b64url}).raise_for_status()
