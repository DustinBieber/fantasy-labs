"""Google Workspace: Sheets (master data), Gmail and Drive (documents), Calendar, Chat.

Sheets, Gmail, Drive and Calendar authenticate with a service account
(GOOGLE_SERVICE_ACCOUNT_JSON = path to its key file). Gmail also needs
domain-wide delegation to read a mailbox. Chat posts through an incoming
webhook, which needs no service account.
"""
from __future__ import annotations

from datetime import datetime

from ..demo import fixtures
from ..timeutil import parse_date, parse_iso, utcnow
from ._util import rows_to_dicts, to_dec, to_int, truthy
from .base import (Connector, NCarrier, NDocument, NLocation, NotWiredYet, NProduct, NRate, NShipment,
                   NStandingOrder, Notifier, SyncBatch, register)

SA = "GOOGLE_SERVICE_ACCOUNT_JSON"
NEEDS_AUTH = "Needs the service-account auth step (google-auth) before live calls."


@register
class GoogleSheetsConnector(Connector):
    """The master-data workbook ops maintains by hand: products (hazmat, shelf life, seasonal),
    location mapping, carriers, freight bookings, the 3PL rate card and standing orders."""

    key = "google_sheets"
    name = "Google Sheets"
    category = "workspace"
    capabilities = ("master_data", "shipments", "billing", "export")
    settings_keys = (SA, "GOOGLE_SHEETS_MASTER_ID")
    docs_url = "https://developers.google.com/sheets/api"
    status_note = ("Reads tabs Products, Locations, Carriers, Shipments, RateCard, StandingOrders. " + NEEDS_AUTH)

    def pull_demo(self) -> SyncBatch:
        return self.normalize(fixtures.sheets_master(utcnow()))

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

    def pull_live(self, since: datetime | None) -> SyncBatch:
        # GET https://sheets.googleapis.com/v4/spreadsheets/{id}/values:batchGet?ranges=Products&ranges=...
        return super().pull_live(since)


@register
class GmailConnector(Notifier):
    """Reads carrier/forwarder updates and 3PL invoices from a shared ops mailbox; sends digests."""

    key = "gmail"
    name = "Gmail"
    category = "workspace"
    capabilities = ("documents", "notify")
    settings_keys = (SA, "GMAIL_DELEGATED_USER")
    docs_url = "https://developers.google.com/gmail/api"
    status_note = "Parses tracking emails and invoice attachments. " + NEEDS_AUTH

    def pull_demo(self) -> SyncBatch:
        return self.normalize(fixtures.gmail_messages(utcnow()))

    def normalize(self, messages: list[dict]) -> SyncBatch:
        batch = SyncBatch()
        for m in messages:
            batch.documents.append(NDocument(
                external_id=m["id"], title=m.get("subject") or "", text=m.get("body") or "",
                received_at=parse_iso(m.get("internalDate")), sender=m.get("from"),
                attachments=[(a["filename"], a.get("text") or "") for a in m.get("attachments") or []],
            ))
        return batch

    def pull_live(self, since: datetime | None) -> SyncBatch:
        # GET https://gmail.googleapis.com/gmail/v1/users/me/messages?q=<GMAIL_QUERY> then messages.get
        # (format=full), decoding text/plain parts and CSV/PDF attachments.
        return super().pull_live(since)

    def send(self, text: str) -> None:
        raise NotWiredYet("Gmail digest sending needs the service-account auth step.")


@register
class GoogleDriveConnector(Connector):
    key = "google_drive"
    name = "Google Drive"
    category = "workspace"
    capabilities = ("documents",)
    settings_keys = (SA, "GOOGLE_DRIVE_FOLDER_ID")
    docs_url = "https://developers.google.com/drive/api"
    status_note = "Watches a folder for 3PL invoices, BOLs and packing lists. " + NEEDS_AUTH

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
        # GET https://www.googleapis.com/drive/v3/files?q='<folder>' in parents and modifiedTime > '<since>'
        return super().pull_live(since)


@register
class GoogleCalendarConnector(Connector):
    key = "google_calendar"
    name = "Google Calendar"
    category = "workspace"
    capabilities = ("calendar",)
    settings_keys = (SA, "GOOGLE_CALENDAR_ID")
    docs_url = "https://developers.google.com/calendar/api"
    status_note = "Publishes order deadlines, freight ETAs and standing-order dates. " + NEEDS_AUTH

    def push_events(self, events: list[dict]) -> None:
        # POST https://www.googleapis.com/calendar/v3/calendars/{id}/events (upsert by event id)
        raise NotWiredYet("Google Calendar publishing needs the service-account auth step.")


@register
class GoogleChatConnector(Notifier):
    key = "google_chat"
    name = "Google Chat"
    category = "messaging"
    settings_keys = ("GOOGLE_CHAT_WEBHOOK_URL",)
    docs_url = "https://developers.google.com/workspace/chat/quickstart/webhooks"
    status_note = "Live: posts alerts to a space through an incoming webhook."

    def send(self, text: str) -> None:
        with self.http() as client:
            client.post(self.setting("GOOGLE_CHAT_WEBHOOK_URL"), json={"text": text}).raise_for_status()
