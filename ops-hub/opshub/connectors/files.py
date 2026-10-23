"""Export-file imports: run any system from its own report exports until its API is connected.

Each source has a column profile (header names its exports use). A file is classified by
its columns:
  orders     order number + status/created date, one row per line item (Shopify, Extensiv exports)
  lots       SKU + lot number + quantity (Extensiv stock-by-lot reports): lots and stock
  inventory  SKU + on-hand quantity (inventory listings): stock
  sales      SKU + quantity + date (sales line reports: Lightspeed, RQ)

Files arrive from a Drive folder (``<SOURCE>_IMPORT_FOLDER_ID``), emails matching
``<SOURCE>_IMPORT_GMAIL_QUERY``, or the dashboard's Import button. Record ids from files start
with ``file:``; when the API is connected later, ingest adopts those records instead of
duplicating them.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime
from typing import ClassVar

from ._util import to_dec, to_int
from .base import NLocation, NLot, NOrder, NOrderLine, NSale, NShipment, NStock, SyncBatch

BASE_ALIASES = {
    "store": ["store", "store name", "storename", "store id", "storeid", "shop", "shop name", "location",
              "location name", "branch", "store/location"],
    "sku": ["sku", "custom sku", "product sku", "productsku", "lineitem sku", "item sku", "item #", "item#",
            "item number", "product code", "product id", "productidentifier", "product identifier", "manufacturer sku",
            "manufact. sku", "mfg sku", "mfg part #", "part #", "vendor sku", "system sku"],
    "name": ["description", "product name", "productname", "lineitem name", "item", "item description",
             "product description", "title", "product"],
    "on_hand": ["on hand", "qty on hand", "quantity on hand", "quantityonhand", "qoh", "onhand", "on hand qty",
                "in stock", "quantity in stock", "stock on hand", "soh", "qty."],
    "available": ["available", "available qty", "availableqty", "qty available"],
    "committed": ["committed", "quantitycommitted", "qty committed", "reserved", "allocated", "on hold"],
    "qty": ["qty sold", "quantity sold", "units sold", "lineitem quantity", "quantity", "qty", "units", "sold"],
    "date": ["date", "sale date", "sold date", "invoice date", "datecreated", "date created", "transaction date",
             "sold on", "created at", "created"],
    "invoice": ["invoice", "invoice #", "invoice#", "invoice id", "invoice number", "invoiceidbystore", "receipt",
                "receipt #", "sale id", "transaction id", "transaction #"],
    "amount": ["total", "total price", "totalprice", "extended price", "ext price", "lineitem price", "amount",
               "net sales", "sales", "net total", "revenue", "subtotal"],
    "lot": ["lot", "lot number", "lot #", "lotnumber", "batch", "batch number"],
    "best_by": ["expiration date", "expirationdate", "expiry", "expiry date", "exp date", "exp. date", "best by",
                "best before", "bbd", "best by date"],
    "received": ["received date", "receiveddate", "receive date", "date received"],
    "order": ["order", "order #", "order number", "order id", "reference number", "reference", "referencenum",
              "reference num"],
    "status": ["fulfillment status", "order status", "status"],
    "customer": ["customer", "customer name", "ship to company", "ship to name", "shipping company", "shipping name",
                 "billing name", "company"],
    "created": ["created at", "creation date", "order date", "created", "date"],
    "deadline": ["ship cancel date", "cancel date", "need by", "required by", "deliver by", "ship by",
                 "delivery date", "requested delivery date"],
    "notes": ["notes", "note", "order notes", "shipping notes", "special instructions"],
    "note_attributes": ["note attributes"],
    "tags": ["tags"],
    "cancelled": ["cancelled at", "canceled at", "cancelled date"],
    "tracking": ["tracking number", "tracking #", "tracking", "pro number", "pro #", "bol", "bill of lading"],
    "carrier": ["carrier", "shipping carrier", "shipping method", "ship via"],
    "po": ["po number", "po num", "po #", "purchase order"],
}

# Per source: aliases tried before the base list, and fields whose base list is replaced.
PROFILES = {
    "shopify": {"prefer": {"order": ["name"], "sku": ["lineitem sku"], "qty": ["lineitem quantity"],
                           "name": ["lineitem name"], "amount": ["lineitem price"], "customer": ["shipping name",
                                                                                                  "billing name"],
                           "store": ["location"]},
                "replace": {}, "default_store_key": "SHOPIFY_SALES_LOCATION_ID", "default_store": "online",
                "sales_from_orders": True, "channel": "online"},
    "lightspeed": {"prefer": {"sku": ["custom sku", "system sku"], "on_hand": ["qty.", "qoh", "quantity on hand"],
                              "store": ["shop", "shop name"]},
                   "replace": {}, "default_store_key": "LIGHTSPEED_DEFAULT_SHOP", "default_store": "1",
                   "sales_from_orders": True, "channel": "store"},
    # In Extensiv exports "Location" is a bin, not a building.
    "extensiv": {"prefer": {"order": ["reference number", "transaction id"], "customer": ["ship to company",
                                                                                        "ship to name"]},
                 "replace": {"store": ["facility", "facility name", "warehouse"]},
                 "default_store_key": "EXTENSIV_FACILITY_ID", "default_store": "1", "sales_from_orders": False,
                 "channel": "warehouse"},
    "iqmetrix_rq": {"prefer": {}, "replace": {}, "default_store_key": "RQ_DEFAULT_STORE", "default_store": "RQ",
                    "sales_from_orders": True, "channel": "store"},
}


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", str(h).strip().lower().replace("_", " "))


def aliases_for(source: str) -> dict[str, list[str]]:
    prof = PROFILES.get(source, {"prefer": {}, "replace": {}})
    out = {}
    for field, base in BASE_ALIASES.items():
        lst = prof["replace"].get(field, base)
        out[field] = list(dict.fromkeys(prof["prefer"].get(field, []) + lst))
    return out


def map_headers(header: list[str], source: str = "") -> dict[str, int]:
    """Field -> column index, preferring the first alias listed for each field. One column can serve
    two fields (Shopify's "Created at" is both the order date and the sale date)."""
    cols = {_norm(h): i for i, h in enumerate(header) if h is not None and str(h).strip()}
    found: dict[str, int] = {}
    for field, aliases in aliases_for(source).items():
        for alias in aliases:
            if alias in cols:
                found[field] = cols[alias]
                break
    return found


def classify(m: dict[str, int]) -> str | None:
    if "order" in m and ("status" in m or "created" in m) and ("sku" in m or "status" in m):
        return "orders"
    if "sku" in m and "lot" in m and ("on_hand" in m or "available" in m or "qty" in m):
        return "lots"
    if "sku" in m and ("on_hand" in m or ("available" in m and "date" not in m)):
        return "inventory"
    if "sku" in m and "qty" in m and "date" in m:
        return "sales"
    return None


def find_header(rows: list[list[str]], source: str = "") -> tuple[int, dict[str, int], str] | None:
    """The first row (within the first 15) whose columns make a known report."""
    for i, row in enumerate(rows[:15]):
        m = map_headers(row, source)
        kind = classify(m)
        if kind:
            return i, m, kind
    return None


def parse_when(value: str, tz: str) -> datetime | None:
    """Export dates like 10/22/2026, 10/22/2026 3:45 PM, 2026-10-22 15:04:00 -1000, in business-local time."""
    from zoneinfo import ZoneInfo

    from ..timeutil import to_utc_naive

    v = (value or "").strip()
    if not v:
        return None
    zone = ZoneInfo(tz)
    iso = re.sub(r" ([+-]\d{2})(\d{2})$", r"\1:\2", v)  # Shopify: "2026-10-22 15:04:00 -1000"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return to_utc_naive(dt if dt.tzinfo else dt.replace(tzinfo=zone))
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
                "%m/%d/%y", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return to_utc_naive(datetime.strptime(v, fmt).replace(tzinfo=zone))
        except ValueError:
            continue
    return None


def status_from(text: str, cancelled: bool = False) -> str:
    t = (text or "").strip().lower()
    if cancelled or "cancel" in t or "void" in t:
        return "cancelled"
    if "unfulfilled" in t or t in ("", "open", "new", "pending", "unshipped"):
        return "open"
    if "partial" in t:
        return "partial"
    if "hold" in t:
        return "on_hold"
    if "deliver" in t:
        return "delivered"
    if any(w in t for w in ("fulfilled", "complete", "closed", "shipped")):
        return "shipped"
    if "allocat" in t:
        return "allocated"
    if "pick" in t or "pack" in t or "progress" in t:
        return "processing"
    return "open"


LOC_PREFIX = {"iqmetrix_rq": "RQ", "lightspeed": "LS", "shopify": "SHOP", "extensiv": "3PL"}


def _loc_code(source: str, ref: str) -> str:
    return f"{LOC_PREFIX.get(source, source.upper()[:4])}-{re.sub(r'[^A-Za-z0-9]+', '-', ref).strip('-').upper()}"[:64]


def parse_export(rows: list[list[str]], *, source: str, tz: str, default_store: str | None = None) -> tuple[str, SyncBatch]:
    """('orders' | 'lots' | 'inventory' | 'sales' | 'unknown', batch) for one exported report."""
    prof = PROFILES.get(source, PROFILES["iqmetrix_rq"])
    default_store = default_store or prof["default_store"]
    batch = SyncBatch()
    hit = find_header(rows, source)
    if hit is None:
        return "unknown", batch
    at, m, kind = hit
    cell = lambda row, f: str(row[m[f]]).strip() if f in m and m[f] < len(row) and row[m[f]] is not None else ""  # noqa: E731
    body = [r for r in rows[at + 1:] if any(str(c).strip() for c in r if c is not None)]
    stores: set[str] = set()
    skip = {"total", "totals", "grand total", "subtotal"}

    if kind in ("inventory", "lots"):
        totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
        for r in body:
            sku = cell(r, "sku")
            if not sku or sku.lower() in skip:
                continue
            store = cell(r, "store") or default_store
            stores.add(store)
            on_hand = to_int(cell(r, "on_hand") or cell(r, "qty") or cell(r, "available"))
            available = to_int(cell(r, "available")) if "available" in m else on_hand - to_int(cell(r, "committed"))
            t = totals[(store, sku)]
            t[0] += on_hand
            t[1] += available
            if kind == "lots" and on_hand:
                best_by, received = parse_when(cell(r, "best_by"), tz), parse_when(cell(r, "received"), tz)
                batch.lots.append(NLot(sku=sku, location=store, lot_number=cell(r, "lot") or "no-lot",
                                       qty_on_hand=on_hand, best_by=best_by.date() if best_by else None,
                                       received_on=received.date() if received else None))
        for (store, sku), (on_hand, available, _) in totals.items():
            batch.stock.append(NStock(sku=sku, location=store, on_hand=on_hand, allocated=on_hand - available,
                                      available=available))
        _merge_lots(batch)

    elif kind == "sales":
        lines: dict[str, NSale] = {}
        for r in body:
            sku, when, qty = cell(r, "sku"), parse_when(cell(r, "date"), tz), to_int(cell(r, "qty"))
            if not sku or sku.lower() in skip or when is None or not qty:
                continue
            store = cell(r, "store") or default_store
            stores.add(store)
            inv = cell(r, "invoice")
            key = f"{store}|{inv}|{sku}" if inv else f"{store}|{when.date()}|{sku}"
            ext = "file:" + hashlib.sha1(key.encode()).hexdigest()[:20]
            amount = to_dec(cell(r, "amount"))
            if ext in lines:  # same invoice line split over rows, or a daily total split by employee
                lines[ext].qty += qty
                if amount is not None:
                    lines[ext].net_amount = (lines[ext].net_amount or 0) + amount
            else:
                lines[ext] = NSale(external_id=ext, sku=sku, location=store, sold_at=when, qty=qty, net_amount=amount)
        batch.sales = list(lines.values())

    else:  # orders: one row per line item; order-level fields may only be on the first row
        groups: dict[str, list[list[str]]] = defaultdict(list)
        for r in body:
            number = cell(r, "order")
            if number and number.lower() not in skip:
                groups[number].append(r)
        for number, rs in groups.items():
            first = lambda f: next((cell(r, f) for r in rs if cell(r, f)), "")  # noqa: E731
            cancelled = bool(first("cancelled"))
            status = status_from(first("status"), cancelled)
            created = parse_when(first("created"), tz)
            store = first("store") or default_store
            stores.add(store)
            attrs = {}
            for line in re.split(r"[\n;]", first("note_attributes")):
                if ":" in line:
                    k, v = line.split(":", 1)
                    attrs[k.strip()] = v.strip()
            raw_deadline = first("deadline")
            deadline = parse_when(raw_deadline, tz)
            if deadline and ":" not in raw_deadline:  # a date without a time means by the end of that day
                from ..timeutil import end_of_day_utc, local_date

                deadline = end_of_day_utc(local_date(deadline, tz), tz)
            lines = [NOrderLine(sku=cell(r, "sku") or None, title=cell(r, "name") or None, qty=to_int(cell(r, "qty")),
                                qty_fulfilled=to_int(cell(r, "qty")) if status in ("shipped", "delivered") else 0)
                     for r in rs if cell(r, "sku") or cell(r, "qty")]
            batch.orders.append(NOrder(
                external_id=f"file:{number}", number=number, status=status, raw_status=first("status") or None,
                channel=prof["channel"], customer_name=first("customer") or None, placed_at=created,
                notes=first("notes") or None, tags=[t.strip() for t in first("tags").split(",") if t.strip()],
                attributes=attrs, lines=lines, ship_from=store, required_by=deadline,
                reference=number if source == "extensiv" else None,
                order_type="wholesale" if first("po") and source == "extensiv" else None,
            ))
            if prof["sales_from_orders"] and status != "cancelled" and created:
                for i, line in enumerate(lines):
                    if line.sku and line.qty:
                        batch.sales.append(NSale(external_id=f"file:{number}:{line.sku}:{i}", sku=line.sku,
                                                 location=store, sold_at=created, qty=line.qty))
            tracking = first("tracking")
            if tracking:
                batch.shipments.append(NShipment(
                    reference=f"{source.upper()[:4]}-{number.lstrip('#')}", direction="outbound", mode="parcel",
                    carrier_name=first("carrier") or "Unknown carrier", tracking_ref=tracking, status="in_transit",
                    destination=first("customer") or None, order_number=number, booked_at=created))
    batch.locations = [NLocation(code=_loc_code(source, s), name=s, kind="store" if source != "extensiv" else
                                 "partner_3pl", external_id=s) for s in sorted(stores)]
    return kind, batch


def _merge_lots(batch: SyncBatch) -> None:
    merged: dict[tuple[str, str, str], NLot] = {}
    for lot in batch.lots:
        key = (lot.sku, lot.location, lot.lot_number)
        if key in merged:
            merged[key].qty_on_hand += lot.qty_on_hand
        else:
            merged[key] = lot
    batch.lots = list(merged.values())


def merge(batches: list[SyncBatch]) -> SyncBatch:
    out = SyncBatch()
    seen_locs: dict[str, NLocation] = {}
    for b in batches:
        for loc in b.locations:
            seen_locs.setdefault(loc.external_id, loc)
        for attr in ("stock", "lots", "sales", "orders", "shipments"):
            getattr(out, attr).extend(getattr(b, attr))  # later files win in ingest (same keys upsert)
    out.locations = list(seen_locs.values())
    return out


class FileImports:
    """Mixin: import a source's export files from Drive, Gmail or uploads."""

    import_folder_key: ClassVar[str] = ""
    import_query_key: ClassVar[str] = ""

    @property
    def imports(self) -> bool:
        return True

    def files_ready(self) -> bool:
        from . import google_auth

        return bool(google_auth.method(self.env) and (self.env.get(self.import_folder_key)
                                                      or self.env.get(self.import_query_key)))

    def test_files(self) -> str:
        from .google import DRIVE, GMAIL, GoogleDriveConnector

        g = GoogleDriveConnector(self.env)
        parts = []
        with g.http() as client:
            folder = self.env.get(self.import_folder_key)
            if folder:
                meta = g.gapi(client, "GET", f"{DRIVE}/files/{folder}",
                              params={"fields": "name", "supportsAllDrives": "true"}).json()
                parts.append(f"exports folder \"{meta.get('name')}\" is readable")
            query = self.env.get(self.import_query_key)
            if query:
                found = g.gapi(client, "GET", f"{GMAIL}/messages", params={"q": query, "maxResults": 50}).json()
                parts.append(f"{len(found.get('messages', []))} emails match the search")
        return "Importing export files: " + ", ".join(parts)

    def import_files(self, files: list[tuple[str, str | bytes]], tz: str) -> tuple[SyncBatch, list[dict]]:
        from ..services.files import read_table

        prof = PROFILES.get(self.key, {})
        default_store = self.env.get(prof.get("default_store_key", "")) or None
        batches, report = [], []
        for name, content in files:
            try:
                kind, b = parse_export(read_table(name, content), source=self.key, tz=tz, default_store=default_store)
            except Exception as exc:  # one bad file shouldn't stop the others
                report.append({"file": name, "kind": "error", "error": str(exc)[:200]})
                continue
            report.append({"file": name, "kind": kind, "orders": len(b.orders), "stock_rows": len(b.stock),
                           "lots": len(b.lots), "sales_lines": len(b.sales),
                           "stores": [l.name for l in b.locations]})
            if kind != "unknown":
                batches.append(b)
        return merge(batches), report

    def pull_files(self, since: datetime | None) -> SyncBatch:
        from ..config import get_settings
        from .google import GmailConnector, GoogleDriveConnector

        files: list[tuple[str, str | bytes]] = []
        folder = self.env.get(self.import_folder_key)
        if folder:
            docs = GoogleDriveConnector(self.env).drive_documents(folder, since)
            files += [a for d in sorted(docs, key=lambda d: d.received_at or datetime.min) for a in d.attachments]
        query = self.env.get(self.import_query_key)
        if query:
            gmail = GmailConnector({**self.env, "GMAIL_QUERY": query})
            for d in sorted(gmail.pull_live(since).documents, key=lambda d: d.received_at or datetime.min):
                files += [a for a in d.attachments if a[0].lower().endswith((".csv", ".tsv", ".xlsx", ".xlsm"))]
        batch, _ = self.import_files(files, get_settings().timezone)
        return batch
