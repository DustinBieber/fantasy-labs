"""Create Ops Hub's Google Drive folders, sheets and calendar in the signed-in account.

Safe to run again: everything is found by name first and only created if missing, and
settings that already have a value are left alone.

  Ops Hub/
    3PL invoices & documents       GOOGLE_DRIVE_FOLDER_ID
    Imports/Shopify exports        SHOPIFY_IMPORT_FOLDER_ID
    Imports/Lightspeed exports     LIGHTSPEED_IMPORT_FOLDER_ID
    Imports/Extensiv exports       EXTENSIV_IMPORT_FOLDER_ID
    Imports/RQ reports             RQ_DRIVE_FOLDER_ID
    Ops Hub → Muse                 MUSE_DRIVE_OUTBOX_FOLDER_ID
    Muse → Ops Hub                 MUSE_DRIVE_INBOX_FOLDER_ID
    Ops Hub master data (sheet)    GOOGLE_SHEETS_MASTER_ID
    Ops Hub reports (sheet)        GOOGLE_SHEETS_REPORT_ID
  Calendar "Ops Hub"               GOOGLE_CALENDAR_ID
"""
from __future__ import annotations

import httpx

from .. import settings_store
from ..config import Settings
from ..connectors.google import CALENDAR, DRIVE, MASTER_TABS, SHEETS, GoogleDriveConnector
from ..demo import fixtures
from ..timeutil import utcnow

FOLDER = "application/vnd.google-apps.folder"
SHEET = "application/vnd.google-apps.spreadsheet"
README = [
    ["Ops Hub master data"],
    ["Each tab is read on every sync. Keep the header row; add one row per item."],
    ["Products: every SKU, with hazmat (yes/blank), UN number, shelf life in days and seasonal flags."],
    ["Locations: map each system's own store/warehouse id or name (external_id) to your location code. "
     "source is the system trusted for that location's stock: shopify, lightspeed, extensiv or iqmetrix_rq."],
    ["Carriers: names and aliases as carriers write them, hazmat approval, tracking URL with {ref}, contact."],
    ["Shipments: freight booked by phone/email (or use Book shipment on the dashboard)."],
    ["RateCard: the partner warehouse's contract rates; vendor must match EXTENSIV_VENDOR_NAME."],
    ["StandingOrders: recurring customer orders; lines as SKU:qty; SKU:qty."],
    ["The Examples tab shows a filled-in row for every tab."],
]


def _find(g, client, name: str, parent: str, mime: str) -> str | None:
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{safe}' and '{parent}' in parents and mimeType = '{mime}' and trashed = false"
    files = g.gapi(client, "GET", f"{DRIVE}/files", params={
        "q": q, "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}).json()
    return files["files"][0]["id"] if files.get("files") else None


def _folder(g, client, name: str, parent: str, made: list[str]) -> str:
    fid = _find(g, client, name, parent, FOLDER)
    if fid:
        return fid
    made.append(f"folder {name}")
    return g.gapi(client, "POST", f"{DRIVE}/files", params={"supportsAllDrives": "true"},
                  json={"name": name, "mimeType": FOLDER, "parents": [parent]}).json()["id"]


def _sheet(g, client, title: str, tabs: list[str], folder: str, made: list[str]) -> tuple[str, bool]:
    sid = _find(g, client, title, folder, SHEET)
    if sid:
        return sid, False
    made.append(f"sheet {title}")
    sid = g.gapi(client, "POST", SHEETS, json={"properties": {"title": title},
                                               "sheets": [{"properties": {"title": t}} for t in tabs]}).json()["spreadsheetId"]
    parents = g.gapi(client, "GET", f"{DRIVE}/files/{sid}", params={"fields": "parents"}).json().get("parents", [])
    g.gapi(client, "PATCH", f"{DRIVE}/files/{sid}", params={"addParents": folder, "removeParents": ",".join(parents),
                                                           "supportsAllDrives": "true"}, json={})
    return sid, True


def _calendar(g, client, tz: str, made: list[str]) -> str | None:
    try:
        items = g.gapi(client, "GET", f"{CALENDAR}/users/me/calendarList", params={"maxResults": 250}).json()
        for c in items.get("items", []):
            if c.get("summary") == "Ops Hub":
                return c["id"]
        made.append("calendar Ops Hub")
        return g.gapi(client, "POST", f"{CALENDAR}/calendars", json={"summary": "Ops Hub", "timeZone": tz}).json()["id"]
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:  # signed in before calendar creation was requested
            return None
        raise


def initialize(s: Settings, env: dict[str, str] | None = None) -> dict:
    env = env if env is not None else settings_store.effective_env()
    g = GoogleDriveConnector(env)
    made: list[str] = []
    ids: dict[str, str] = {}
    with g.http() as client:
        root = _folder(g, client, "Ops Hub", "root", made)
        ids["GOOGLE_DRIVE_FOLDER_ID"] = _folder(g, client, "3PL invoices & documents", root, made)
        imports = _folder(g, client, "Imports", root, made)
        ids["SHOPIFY_IMPORT_FOLDER_ID"] = _folder(g, client, "Shopify exports", imports, made)
        ids["LIGHTSPEED_IMPORT_FOLDER_ID"] = _folder(g, client, "Lightspeed exports", imports, made)
        ids["EXTENSIV_IMPORT_FOLDER_ID"] = _folder(g, client, "Extensiv exports", imports, made)
        ids["RQ_DRIVE_FOLDER_ID"] = _folder(g, client, "RQ reports", imports, made)
        ids["MUSE_DRIVE_OUTBOX_FOLDER_ID"] = _folder(g, client, "Ops Hub → Muse", root, made)
        ids["MUSE_DRIVE_INBOX_FOLDER_ID"] = _folder(g, client, "Muse → Ops Hub", root, made)

        master, new_master = _sheet(g, client, "Ops Hub master data", ["Read me", *MASTER_TABS, "Examples"], root, made)
        ids["GOOGLE_SHEETS_MASTER_ID"] = master
        if new_master:
            sample = fixtures.sheets_master(utcnow())
            examples: list[list[str]] = []
            for tab in MASTER_TABS:
                examples += [[f"{tab} (example)"], sample[tab][0], *sample[tab][1:3], []]
            data = [{"range": "'Read me'!A1", "values": README}, {"range": "Examples!A1", "values": examples}]
            data += [{"range": f"{tab}!A1", "values": [sample[tab][0]]} for tab in MASTER_TABS]
            g.gapi(client, "POST", f"{SHEETS}/{master}/values:batchUpdate",
                   json={"valueInputOption": "RAW", "data": data})
        ids["GOOGLE_SHEETS_REPORT_ID"], _ = _sheet(g, client, "Ops Hub reports", ["Watchlist"], root, made)
        cal = _calendar(g, client, s.timezone, made)
        if cal:
            ids["GOOGLE_CALENDAR_ID"] = cal
        account = g.who(client)

    current = settings_store.effective_env()
    to_save = {k: v for k, v in ids.items() if not current.get(k)}
    skipped = settings_store.save(to_save)
    return {"account": account, "created": made, "saved": sorted(k for k in to_save if k not in skipped),
            "kept_existing": sorted(k for k in ids if current.get(k)),
            "calendar": "created" if cal else "sign in again to allow calendar creation",
            "folder_url": f"https://drive.google.com/drive/folders/{root}"}
