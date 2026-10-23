"""Google Workspace: Sheets (master data in, reports out), Gmail and Drive (documents),
Calendar (deadlines), Chat (alerts).

Sheets, Gmail, Drive and Calendar share one Google login (see google_auth). Chat posts
through an incoming webhook and needs no login.
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import ClassVar

import httpx

from ..demo import fixtures
from ..timeutil import parse_date, parse_iso, utcnow
from . import google_auth
from ._util import rows_to_dicts, to_dec, to_int, truthy
from .base import (Connector, NCarrier, NDocument, NLocation, NProduct, NRate, NShipment, NStandingOrder, Notifier,
                   SyncBatch, register)

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = "https://www.googleapis.com/calendar/v3"
MASTER_TABS = ("Products", "Locations", "Carriers", "Shipments", "RateCard", "StandingOrders")
READABLE_EXTS = (".csv", ".tsv", ".xlsx", ".xlsm", ".pdf", ".txt", ".md", ".json")
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class GoogleConnector(Connector):
    """Shared Google login handling and an authenticated request helper."""

    needs_auth: ClassVar[bool] = True

    def missing_settings(self) -> list[str]:
        missing = [k for k in self.settings_keys if not self.env.get(k)]
        if self.needs_auth and google_auth.method(self.env) is None:
            missing.insert(0, google_auth.AUTH_REQUIREMENT)
        return missing

    def is_configured(self) -> bool:
        return not self.missing_settings()

    def gapi(self, client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {google_auth.access_token(self.env, type(self).transport)}",
                   **kw.pop("headers", {})}
        for attempt in range(3):
            resp = client.request(method, url, headers=headers, **kw)
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(float(resp.headers.get("Retry-After") or 2 * (attempt + 1)))
                continue
            break
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(f"Google API {resp.status_code} on {method} {url}: {resp.text[:300]}",
                                        request=resp.request, response=resp)
        return resp

    # --- Drive helpers used by several connectors ---------------------------------

    def drive_list(self, client: httpx.Client, folder_id: str, since: datetime | None = None) -> list[dict]:
        q = f"'{folder_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        if since:
            q += f" and modifiedTime > '{since.replace(microsecond=0).isoformat()}Z'"
        files, token = [], None
        while True:
            params = {"q": q, "fields": "nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                      "pageSize": 100, "orderBy": "modifiedTime", "supportsAllDrives": "true",
                      "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            body = self.gapi(client, "GET", f"{DRIVE}/files", params=params).json()
            files += body.get("files", [])
            token = body.get("nextPageToken")
            if not token:
                return files

    def drive_download(self, client: httpx.Client, f: dict) -> tuple[str, bytes]:
        mime = f.get("mimeType", "")
        if mime == "application/vnd.google-apps.spreadsheet":
            r = self.gapi(client, "GET", f"{DRIVE}/files/{f['id']}/export", params={"mimeType": "text/csv"})
            return f["name"] + ".csv", r.content
        if mime.startswith("application/vnd.google-apps."):
            r = self.gapi(client, "GET", f"{DRIVE}/files/{f['id']}/export", params={"mimeType": "text/plain"})
            return f["name"] + ".txt", r.content
        r = self.gapi(client, "GET", f"{DRIVE}/files/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"})
        return f["name"], r.content

    def drive_upload(self, client: httpx.Client, folder_id: str, name: str, content: bytes, mime: str) -> str:
        """Create the file in the folder, or replace its content if a file with that name is there."""
        q = f"'{folder_id}' in parents and name = '{name.replace(chr(39), chr(92) + chr(39))}' and trashed = false"
        found = self.gapi(client, "GET", f"{DRIVE}/files", params={
            "q": q, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}).json()
        if found.get("files"):
            fid = found["files"][0]["id"]
            self.gapi(client, "PATCH", f"{DRIVE_UPLOAD}/files/{fid}", params={"uploadType": "media",
                                                                           "supportsAllDrives": "true"},
                      content=content, headers={"Content-Type": mime})
            return fid
        boundary = "opshub-boundary-7f3a"
        meta = json.dumps({"name": name, "parents": [folder_id]}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta
                + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + content + f"\r\n--{boundary}--".encode())
        r = self.gapi(client, "POST", f"{DRIVE_UPLOAD}/files", params={"uploadType": "multipart",
                                                                     "supportsAllDrives": "true"},
                      content=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        return r.json()["id"]

    def drive_documents(self, folder_id: str, since: datetime | None, hint: str | None = None) -> list[NDocument]:
        docs = []
        with self.http() as client:
            for f in self.drive_list(client, folder_id, since):
                if f.get("size") and int(f["size"]) > MAX_ATTACHMENT_BYTES:
                    continue
                name, content = self.drive_download(client, f)
                docs.append(NDocument(external_id=f["id"] + ":" + (f.get("modifiedTime") or ""), title=f["name"],
                                      text="", received_at=parse_iso(f.get("modifiedTime")),
                                      attachments=[(name, content)], hint=hint))
        return docs


@register
class GoogleSheetsConnector(GoogleConnector):
    """The master-data workbook ops maintains by hand: products (hazmat, shelf life, seasonal),
    location mapping, carriers, freight bookings, the 3PL rate card and standing orders.
    Also writes report tabs to GOOGLE_SHEETS_REPORT_ID after each sync."""

    key = "google_sheets"
    name = "Google Sheets"
    category = "workspace"
    capabilities = ("master_data", "shipments", "billing", "export")
    settings_keys = ("GOOGLE_SHEETS_MASTER_ID",)
    docs_url = "https://developers.google.com/sheets/api"
    status_note = ("Live: reads tabs Products, Locations, Carriers, Shipments, RateCard, StandingOrders. "
                   "Writes report tabs to GOOGLE_SHEETS_REPORT_ID if set.")

    def pull_demo(self) -> SyncBatch:
        return self.normalize(fixtures.sheets_master(utcnow()))

    def pull_live(self, since: datetime | None) -> SyncBatch:
        sheet = self.setting("GOOGLE_SHEETS_MASTER_ID")
        with self.http() as client:
            meta = self.gapi(client, "GET", f"{SHEETS}/{sheet}", params={"fields": "sheets.properties.title"}).json()
            titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
            tabs = [t for t in MASTER_TABS if t in titles]
            if not tabs:
                return SyncBatch()
            body = self.gapi(client, "GET", f"{SHEETS}/{sheet}/values:batchGet",
                             params=[("ranges", t) for t in tabs] + [("majorDimension", "ROWS")]).json()
        values = {t: vr.get("values", []) for t, vr in zip(tabs, body.get("valueRanges", []))}
        return self.normalize(values)

    def write_reports(self, tables: dict[str, list[list]]) -> int:
        """Replace each named tab of the report spreadsheet with the given rows."""
        sheet = self.setting("GOOGLE_SHEETS_REPORT_ID")
        if not sheet:
            raise RuntimeError("Set GOOGLE_SHEETS_REPORT_ID to the spreadsheet that should receive reports")
        with self.http() as client:
            meta = self.gapi(client, "GET", f"{SHEETS}/{sheet}", params={"fields": "sheets.properties.title"}).json()
            have = {s["properties"]["title"] for s in meta.get("sheets", [])}
            add = [{"addSheet": {"properties": {"title": t}}} for t in tables if t not in have]
            if add:
                self.gapi(client, "POST", f"{SHEETS}/{sheet}:batchUpdate", json={"requests": add})
            self.gapi(client, "POST", f"{SHEETS}/{sheet}/values:batchClear", json={"ranges": list(tables)})
            self.gapi(client, "POST", f"{SHEETS}/{sheet}/values:batchUpdate", json={
                "valueInputOption": "RAW",
                "data": [{"range": f"{t}!A1", "values": [["" if v is None else v for v in row] for row in rows]}
                         for t, rows in tables.items()]})
        return sum(len(r) for r in tables.values())

    def normalize(self, tabs: dict[str, list[list[str]]]) -> SyncBatch:
        batch = SyncBatch()
        for r in rows_to_dicts(tabs.get("Products", [])):
            batch.products.append(NProduct(
                sku=r["sku"], name=r.get("name") or r["sku"], upc=r.get("upc") or None, category=r.get("category") or None,
                hazmat=truthy(r.get("hazmat")), hazmat_class=r.get("hazmat_class") or None,
                un_number=r.get("un_number") or None, seasonal=truthy(r.get("seasonal")), season=r.get("season") or None,
                shelf_life_days=to_int(r.get("shelf_life_days")) or None, unit_cost=to_dec(r.get("unit_cost")),
            ))
        for r in rows_to_dicts(tabs.get("Locations", [])):
            batch.locations.append(NLocation(code=r["code"], name=r.get("name") or r["code"], kind=r.get("kind") or "unknown",
                                             source=r.get("source") or None, external_id=r.get("external_id") or None,
                                             address=r.get("address") or None))
        for r in rows_to_dicts(tabs.get("Carriers", [])):
            batch.carriers.append(NCarrier(
                name=r["name"], aliases=[a.strip() for a in (r.get("aliases") or "").split(";") if a.strip()],
                hazmat_approved=truthy(r.get("hazmat_approved")),
                tracking_url_template=r.get("tracking_url_template") or None, contact=r.get("contact") or None,
            ))
        for r in rows_to_dicts(tabs.get("Shipments", [])):
            batch.shipments.append(NShipment(
                reference=r["reference"], direction=r.get("direction") or "inbound", mode=r.get("mode") or "ltl",
                carrier_name=r.get("carrier") or "Unknown carrier", agent_name=r.get("agent") or None,
                tracking_ref=r.get("tracking_ref") or None, origin=r.get("origin") or None,
                destination=r.get("destination") or None, destination_location=r.get("destination_location") or None,
                order_number=r.get("order_number") or None, eta=parse_iso(r.get("eta")),
                hazmat=truthy(r.get("hazmat")),
                un_numbers=[u.strip() for u in (r.get("un_numbers") or "").replace(";", ",").split(",") if u.strip()],
                priority=truthy(r.get("priority")), status=r.get("status") or "booked",
                booked_at=parse_iso(r.get("booked_at")), notes=r.get("notes") or None,
            ))
        for r in rows_to_dicts(tabs.get("RateCard", [])):
            batch.rates.append(NRate(
                vendor=r["vendor"], charge_code=r["charge_code"], description=r.get("description") or r["charge_code"],
                unit=r.get("unit") or "", rate=to_dec(r.get("rate")), min_charge=to_dec(r.get("min_charge")),
                requires_approval=truthy(r.get("requires_approval")),
                effective_from=parse_date(r.get("effective_from")), effective_to=parse_date(r.get("effective_to")),
            ))
        for r in rows_to_dicts(tabs.get("StandingOrders", [])):
            lines = []
            for part in (r.get("lines") or "").split(";"):
                if ":" in part:
                    sku, qty = part.split(":", 1)
                    lines.append({"sku": sku.strip(), "qty": to_int(qty)})
            batch.standing_orders.append(NStandingOrder(
                code=r["code"], customer_name=r.get("customer_name") or r["code"],
                cadence_days=to_int(r.get("cadence_days"), 30), next_due=parse_date(r.get("next_due")),
                min_days_to_best_by=to_int(r.get("min_days_to_best_by")), ship_from=r.get("ship_from") or None,
                lines=lines, notes=r.get("notes") or None, active=truthy(r.get("active") or "yes"),
            ))
        return batch


def _walk_parts(part: dict):
    yield part
    for p in part.get("parts") or []:
        yield from _walk_parts(p)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(a, b)
    return re.sub(r"[ \t]+", " ", text).strip()


@register
class GmailConnector(GoogleConnector, Notifier):
    """Reads carrier/forwarder updates and 3PL invoices from the ops mailbox; sends the digest."""

    key = "gmail"
    name = "Gmail"
    category = "workspace"
    capabilities = ("documents", "notify")
    settings_keys = ()
    docs_url = "https://developers.google.com/gmail/api"
    status_note = ("Live: reads mail matching GMAIL_QUERY (text + CSV/XLSX/PDF attachments) and sends the "
                   "digest to GMAIL_TO.")

    def pull_demo(self) -> SyncBatch:
        return self.normalize(fixtures.gmail_messages(utcnow()))

    def normalize(self, messages: list[dict]) -> SyncBatch:
        batch = SyncBatch()
        for m in messages:
            batch.documents.append(NDocument(
                external_id=m["id"], title=m.get("subject") or "", text=m.get("body") or "",
                received_at=parse_iso(m.get("internalDate")), sender=m.get("from"),
                attachments=[(a["filename"], a.get("text") or a.get("content") or "") for a in m.get("attachments") or []],
            ))
        return batch

    def pull_live(self, since: datetime | None) -> SyncBatch:
        q = self.setting("GMAIL_QUERY", "newer_than:14d -in:chats -in:sent")
        if since:
            q += f" after:{int(since.replace(tzinfo=UTC).timestamp()) - 3600}"
        limit = int(self.setting("GMAIL_MAX_MESSAGES", "200"))
        messages = []
        with self.http() as client:
            ids, token = [], None
            while len(ids) < limit:
                params = {"q": q, "maxResults": min(100, limit - len(ids))}
                if token:
                    params["pageToken"] = token
                body = self.gapi(client, "GET", f"{GMAIL}/messages", params=params).json()
                ids += [m["id"] for m in body.get("messages", [])]
                token = body.get("nextPageToken")
                if not token:
                    break
            for mid in ids:
                messages.append(self._read_message(client, mid))
        return self.normalize(messages)

    def _read_message(self, client: httpx.Client, mid: str) -> dict:
        m = self.gapi(client, "GET", f"{GMAIL}/messages/{mid}", params={"format": "full"}).json()
        payload = m.get("payload") or {}
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        plain, html, attachments = [], [], []
        for part in _walk_parts(payload):
            mime, body = part.get("mimeType", ""), part.get("body") or {}
            filename = part.get("filename") or ""
            if filename:
                if not filename.lower().endswith(READABLE_EXTS) or int(body.get("size") or 0) > MAX_ATTACHMENT_BYTES:
                    continue
                data = body.get("data")
                if not data and body.get("attachmentId"):
                    data = self.gapi(client, "GET", f"{GMAIL}/messages/{mid}/attachments/{body['attachmentId']}").json()["data"]
                if data:
                    attachments.append({"filename": filename, "content": b64url_decode(data)})
            elif mime == "text/plain" and body.get("data"):
                plain.append(b64url_decode(body["data"]).decode("utf-8", errors="replace"))
            elif mime == "text/html" and body.get("data"):
                html.append(b64url_decode(body["data"]).decode("utf-8", errors="replace"))
        text = "\n".join(plain) or _html_to_text("\n".join(html))
        received = datetime.fromtimestamp(int(m.get("internalDate", "0")) / 1000, UTC).replace(tzinfo=None)
        return {"id": m["id"], "internalDate": received.isoformat() + "Z", "from": headers.get("from"),
                "subject": headers.get("subject"), "body": text, "attachments": attachments}

    def send_ready(self) -> bool:
        return self.is_configured() and bool(self.setting("GMAIL_TO"))

    def send(self, text: str) -> None:
        to = self.setting("GMAIL_TO")
        if not to:
            raise RuntimeError("Set GMAIL_TO to the address(es) that should get Ops Hub email")
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = self.setting("GMAIL_SUBJECT_PREFIX", "[Ops Hub] ") + text.splitlines()[0][:120]
        msg.set_content(text)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        with self.http() as client:
            self.gapi(client, "POST", f"{GMAIL}/messages/send", json={"raw": raw})


@register
class GoogleDriveConnector(GoogleConnector):
    key = "google_drive"
    name = "Google Drive"
    category = "workspace"
    capabilities = ("documents",)
    settings_keys = ("GOOGLE_DRIVE_FOLDER_ID",)
    docs_url = "https://developers.google.com/drive/api"
    status_note = "Live: reads new files (3PL invoices, BOLs, packing lists) from GOOGLE_DRIVE_FOLDER_ID."

    def pull_demo(self) -> SyncBatch:
        return self.normalize(fixtures.drive_files(utcnow()))

    def normalize(self, files: list[dict]) -> SyncBatch:
        batch = SyncBatch()
        for f in files:
            batch.documents.append(NDocument(external_id=f["id"], title=f["name"], text="",
                                             received_at=parse_iso(f.get("modifiedTime")),
                                             attachments=[(f["name"], f.get("text") or "")]))
        return batch

    def pull_live(self, since: datetime | None) -> SyncBatch:
        return SyncBatch(documents=self.drive_documents(self.setting("GOOGLE_DRIVE_FOLDER_ID"), since))


@register
class GoogleCalendarConnector(GoogleConnector):
    key = "google_calendar"
    name = "Google Calendar"
    category = "workspace"
    capabilities = ("calendar",)
    settings_keys = ("GOOGLE_CALENDAR_ID",)
    docs_url = "https://developers.google.com/calendar/api"
    status_note = "Live: publishes order deadlines, freight ETAs and standing-order dates as all-day events."

    def push_events(self, events: list[dict], previous_ids: set[str] | None = None) -> dict:
        """Upsert events by id; delete ones we published before that are no longer current."""
        cal = self.setting("GOOGLE_CALENDAR_ID")
        base = f"{CALENDAR}/calendars/{cal}/events"
        created = updated = deleted = 0
        with self.http() as client:
            for e in events:
                day = date.fromisoformat(e["date"])
                body = {"id": e["id"], "summary": e["summary"], "description": e.get("description") or "",
                        "start": {"date": day.isoformat()}, "end": {"date": (day + timedelta(days=1)).isoformat()},
                        "transparency": "transparent"}
                try:
                    self.gapi(client, "PUT", f"{base}/{e['id']}", json=body)
                    updated += 1
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
                    self.gapi(client, "POST", base, json=body)
                    created += 1
            for stale in (previous_ids or set()) - {e["id"] for e in events}:
                try:
                    self.gapi(client, "DELETE", f"{base}/{stale}")
                    deleted += 1
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in (404, 410):
                        raise
        return {"created": created, "updated": updated, "deleted": deleted}


@register
class GoogleChatConnector(Notifier):
    key = "google_chat"
    name = "Google Chat"
    category = "messaging"
    settings_keys = ("GOOGLE_CHAT_WEBHOOK_URL",)
    docs_url = "https://developers.google.com/workspace/chat/quickstart/webhooks"
    status_note = "Live: posts alerts to a space through an incoming webhook (Google Workspace accounts)."

    def send(self, text: str) -> None:
        with self.http() as client:
            client.post(self.setting("GOOGLE_CHAT_WEBHOOK_URL"), json={"text": text}).raise_for_status()
