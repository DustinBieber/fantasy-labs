"""Muse AI: a daily two-way handoff through Google Drive folders.

  Ops Hub -> Muse: every morning (MUSE_EXPORT_TIME) Ops Hub writes a JSON data file and a
                   readable brief to MUSE_DRIVE_OUTBOX_FOLDER_ID.
  Muse -> Ops Hub: files Muse drops in MUSE_DRIVE_INBOX_FOLDER_ID are read on every sync
                   and turned into alerts, tasks, order deadline/note updates and forecasts.

File formats are in MUSE_HANDOFF.md. Without Google credentials (demo mode) the outbox is
a local folder (MUSE_LOCAL_DIR, default ./handoff/to-muse) and the inbox is sample data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ..timeutil import utcnow
from . import google_auth
from .base import NDocument, SyncBatch, register
from .google import GoogleConnector


@register
class MuseConnector(GoogleConnector):
    key = "muse"
    name = "Muse AI"
    category = "ai"
    capabilities = ("documents", "export")
    settings_keys = ("MUSE_DRIVE_INBOX_FOLDER_ID", "MUSE_DRIVE_OUTBOX_FOLDER_ID")
    docs_url = None
    status_note = ("Live: daily handoff through two Drive folders: Ops Hub's brief + data out, Muse's "
                   "alerts/tasks/order updates/forecasts in. Formats in MUSE_HANDOFF.md.")

    def pull_live(self, since: datetime | None) -> SyncBatch:
        return SyncBatch(documents=self.drive_documents(self.setting("MUSE_DRIVE_INBOX_FOLDER_ID"), since, hint="muse"))

    def test(self) -> str:
        with self.http() as client:
            out = self.folder_name(client, self.setting("MUSE_DRIVE_OUTBOX_FOLDER_ID"))
            inn = self.folder_name(client, self.setting("MUSE_DRIVE_INBOX_FOLDER_ID"))
        return f"Outbox \"{out}\" and inbox \"{inn}\" are reachable"

    def pull_demo(self) -> SyncBatch:
        now = utcnow()
        today = now.date()
        payload = {
            "kind": "muse_to_opshub", "version": 1, "date": today.isoformat(),
            "alerts": [
                {"severity": "warn", "title": "Holiday Gift Box selling slower than plan",
                 "detail": "Sell-through is 7% against a 20% plan; consider a bundle with Cold Brew Cans.",
                 "sku": "HOL-GBX"},
            ],
            "tasks": [
                {"title": "Confirm Northwind Legal note cards are at the 3PL before kitting",
                 "due": (today + timedelta(days=2)).isoformat(), "order": "#2046"},
                {"title": "Ask Tri-State Express for their DG certificate or rebook IN-PO-7790",
                 "due": today.isoformat(), "shipment": "IN-PO-7790"},
            ],
            "order_updates": [
                {"order": "#2054", "deadline": (today + timedelta(days=4)).isoformat(),
                 "note": "Customer emailed: needs it before their Monday shop opening."},
            ],
            "forecasts": [
                {"sku": "PWR-20K", "location": "RQ-101", "units_per_day": 1.8,
                 "note": "Partner store promo next week"},
                {"sku": "CBC-32", "units_per_day": 7.5, "note": "Cooler weather lift"},
            ],
            "notes": "Overall: beverage demand steady, seasonal gifting starting slow. Watch power bank stock at Eastgate.",
        }
        doc = NDocument(external_id=f"muse-demo-{today.isoformat()}", title=f"muse-to-opshub-{today}.json", text="",
                        received_at=now - timedelta(hours=2),
                        attachments=[(f"muse-to-opshub-{today}.json", json.dumps(payload, indent=2))], hint="muse")
        return SyncBatch(documents=[doc])

    def write_outbox(self, files: list[tuple[str, bytes, str]]) -> list[str]:
        """Upload (or, without Google, save locally) the day's files for Muse. Returns where each went."""
        folder = self.setting("MUSE_DRIVE_OUTBOX_FOLDER_ID")
        if folder and google_auth.method(self.env):
            with self.http() as client:
                return [f"drive:{self.drive_upload(client, folder, name, content, mime)}"
                        for name, content, mime in files]
        out_dir = Path(self.setting("MUSE_LOCAL_DIR", "handoff")) / "to-muse"
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, content, _ in files:
            p = out_dir / name
            p.write_bytes(content)
            paths.append(str(p))
        return paths
