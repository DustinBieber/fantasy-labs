"""iQmetrix RQ: stock and sales at the wireless-retail partner stores, from report exports.

RQ's API (Data Connect) is a paid add-on that isn't in place, so this connector reads
the reports people already export from RQ (CSV or Excel). They can arrive three ways:
  * dropped in a Google Drive folder (RQ_DRIVE_FOLDER_ID),
  * emailed to the ops mailbox: scheduled RQ reports work well (RQ_GMAIL_QUERY),
  * uploaded on the dashboard (Inventory > Import RQ report).

Columns are matched by name (see HEADER_ALIASES), so most inventory and sales report
layouts work without setup. A report with an on-hand column is read as inventory; one
with a quantity and a date is read as sales.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime

from ..demo import fixtures
from ..timeutil import parse_iso, utcnow
from . import google_auth
from ._util import to_dec, to_int
from .base import NLocation, NSale, NStock, SyncBatch, register
from .google import GoogleConnector

HEADER_ALIASES = {
    "store": ["store", "store name", "storename", "store id", "storeid", "location", "location name", "branch",
              "store/location"],
    "sku": ["sku", "product sku", "productsku", "item #", "item#", "item number", "product code", "product id",
            "productidentifier", "product identifier", "manufacturer sku", "mfg sku", "mfg part #", "part #",
            "vendor sku"],
    "name": ["description", "product name", "productname", "product", "item", "item description",
             "product description"],
    "on_hand": ["on hand", "qty on hand", "quantity on hand", "quantityonhand", "qoh", "onhand", "in stock",
                "quantity in stock", "stock on hand", "soh", "on hand qty"],
    "committed": ["committed", "quantitycommitted", "qty committed", "reserved", "allocated", "on hold"],
    "qty": ["qty sold", "quantity sold", "units sold", "quantity", "qty", "units", "sold"],
    "date": ["date", "invoice date", "sale date", "sold date", "datecreated", "date created", "transaction date",
             "sold on", "created"],
    "invoice": ["invoice", "invoice #", "invoice#", "invoice id", "invoice number", "invoiceidbystore", "receipt",
                "receipt #", "transaction id", "transaction #", "sale id"],
    "amount": ["total", "total price", "totalprice", "extended price", "ext price", "amount", "net sales", "sales",
               "net total", "revenue"],
}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", h.strip().lower().replace("_", " "))


def map_headers(header: list[str]) -> dict[str, int]:
    """Field -> column index, preferring the first alias listed for each field."""
    cols = {_norm(h): i for i, h in enumerate(header) if h and h.strip()}
    found: dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in cols and cols[alias] not in found.values():
                found[field] = cols[alias]
                break
    return found


def find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    """The first row (within the first 15) that names a SKU column plus on-hand or quantity."""
    for i, row in enumerate(rows[:15]):
        m = map_headers(row)
        if "sku" in m and ("on_hand" in m or "qty" in m):
            return i, m
    return None


def _parse_when(value: str, tz: str) -> datetime | None:
    """RQ exports dates as e.g. 10/22/2026, 10/22/2026 3:45 PM or ISO, in store-local time."""
    from zoneinfo import ZoneInfo

    from ..timeutil import to_utc_naive

    v = (value or "").strip()
    if not v:
        return None
    zone = ZoneInfo(tz)
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return to_utc_naive(dt if dt.tzinfo else dt.replace(tzinfo=zone))
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return to_utc_naive(datetime.strptime(v, fmt).replace(tzinfo=zone))
        except ValueError:
            continue
    return None


def parse_report(rows: list[list[str]], *, default_store: str, tz: str) -> tuple[str, SyncBatch]:
    """('inventory' | 'sales' | 'unknown', batch) for one exported RQ report."""
    batch = SyncBatch()
    hit = find_header(rows)
    if hit is None:
        return "unknown", batch
    at, m = hit
    cell = lambda row, f: row[m[f]].strip() if f in m and m[f] < len(row) else ""  # noqa: E731
    body = [r for r in rows[at + 1:] if any(c.strip() for c in r)]
    stores: set[str] = set()
    if "on_hand" in m:
        totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        for r in body:
            sku = cell(r, "sku")
            if not sku or sku.lower() in {"total", "totals", "grand total"}:
                continue
            store = cell(r, "store") or default_store
            stores.add(store)
            t = totals[(store, sku)]
            t[0] += to_int(cell(r, "on_hand"))
            t[1] += to_int(cell(r, "committed"))
        for (store, sku), (on_hand, committed) in totals.items():
            batch.stock.append(NStock(sku=sku, location=store, on_hand=on_hand, allocated=committed,
                                      available=on_hand - committed))
        kind = "inventory"
    elif "date" in m:
        lines: dict[str, NSale] = {}
        for r in body:
            sku, when = cell(r, "sku"), _parse_when(cell(r, "date"), tz)
            qty = to_int(cell(r, "qty"))
            if not sku or when is None or not qty:
                continue
            store = cell(r, "store") or default_store
            stores.add(store)
            inv = cell(r, "invoice")
            key = f"{store}|{inv}|{sku}" if inv else f"{store}|{when.date()}|{sku}"
            ext = "rq:" + hashlib.sha1(key.encode()).hexdigest()[:20]
            amount = to_dec(cell(r, "amount"))
            if ext in lines:  # same invoice line split over rows, or a daily total split by employee
                lines[ext].qty += qty
                if amount is not None:
                    lines[ext].net_amount = (lines[ext].net_amount or 0) + amount
            else:
                lines[ext] = NSale(external_id=ext, sku=sku, location=store, sold_at=when, qty=qty, net_amount=amount)
        batch.sales = list(lines.values())
        kind = "sales"
    else:
        return "unknown", batch
    batch.locations = [NLocation(code=f"RQ-{re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').upper()}"[:64], name=s,
                                 kind="store", external_id=s) for s in sorted(stores)]
    return kind, batch


def merge(batches: list[SyncBatch]) -> SyncBatch:
    out = SyncBatch()
    seen_locs: dict[str, NLocation] = {}
    for b in batches:
        for loc in b.locations:
            seen_locs.setdefault(loc.external_id, loc)
        out.stock += b.stock  # later files win in ingest (same sku/location upserts)
        out.sales += b.sales
    out.locations = list(seen_locs.values())
    return out


@register
class IQMetrixRQConnector(GoogleConnector):
    key = "iqmetrix_rq"
    name = "iQmetrix RQ"
    category = "pos"
    capabilities = ("inventory", "sales")
    settings_keys = ("RQ_DRIVE_FOLDER_ID",)
    docs_url = None
    status_note = ("Report-file import (no API): drop RQ inventory/sales exports in RQ_DRIVE_FOLDER_ID, email them "
                   "(RQ_GMAIL_QUERY), or upload on the Inventory page.")

    def missing_settings(self) -> list[str]:
        missing = [] if google_auth.method(self.env) else [google_auth.AUTH_REQUIREMENT]
        if not (self.env.get("RQ_DRIVE_FOLDER_ID") or self.env.get("RQ_GMAIL_QUERY")):
            missing.append("RQ_DRIVE_FOLDER_ID or RQ_GMAIL_QUERY")
        return missing

    def pull_demo(self) -> SyncBatch:
        return self.normalize(fixtures.rq_stores(), fixtures.rq_inventory(), fixtures.rq_sales(utcnow()))

    def normalize(self, stores: list[dict], inventory: list[dict], sales: list[dict]) -> SyncBatch:
        """Demo data arrives as already-split rows (the shape of RQ's own report columns)."""
        batch = SyncBatch()
        for s in stores:
            batch.locations.append(NLocation(code=f"RQ-{s['StoreID']}", name=s.get("StoreName") or f"Store {s['StoreID']}",
                                             kind="store", external_id=str(s["StoreID"])))
        for row in inventory:
            on_hand, committed = to_int(row.get("QuantityOnHand")), to_int(row.get("QuantityCommitted"))
            batch.stock.append(NStock(sku=row["ProductSKU"], location=str(row["StoreID"]), on_hand=on_hand,
                                      allocated=committed, available=on_hand - committed))
        for row in sales:
            batch.sales.append(NSale(external_id=f"{row['InvoiceIDByStore']}:{row.get('LineID', 1)}",
                                     sku=row["ProductSKU"], location=str(row["StoreID"]),
                                     sold_at=parse_iso(row.get("DateCreated")), qty=to_int(row.get("Quantity")),
                                     net_amount=to_dec(row.get("TotalPrice"))))
        return batch

    def import_files(self, files: list[tuple[str, str | bytes]], tz: str) -> tuple[SyncBatch, list[dict]]:
        from ..services.files import read_table

        batches, report = [], []
        for name, content in files:
            try:
                kind, b = parse_report(read_table(name, content), default_store=self.setting("RQ_DEFAULT_STORE", "RQ"),
                                       tz=tz)
            except Exception as exc:  # a bad file shouldn't stop the others
                report.append({"file": name, "kind": "error", "error": str(exc)[:200]})
                continue
            report.append({"file": name, "kind": kind, "stock_rows": len(b.stock), "sales_lines": len(b.sales),
                           "stores": [l.name for l in b.locations]})
            if kind != "unknown":
                batches.append(b)
        return merge(batches), report

    def pull_live(self, since: datetime | None) -> SyncBatch:
        from ..config import get_settings

        folder = self.setting("RQ_DRIVE_FOLDER_ID")
        docs = self.drive_documents(folder, since) if folder else []
        files = [a for d in sorted(docs, key=lambda d: d.received_at or datetime.min) for a in d.attachments]
        query = self.setting("RQ_GMAIL_QUERY")
        if query:
            from .google import GmailConnector

            gmail = GmailConnector(self.env)
            gmail.env = {**self.env, "GMAIL_QUERY": query}
            for d in sorted(gmail.pull_live(since).documents, key=lambda d: d.received_at or datetime.min):
                files += [a for a in d.attachments if a[0].lower().endswith((".csv", ".tsv", ".xlsx", ".xlsm"))]
        batch, _ = self.import_files(files, get_settings().timezone)
        return batch
