"""iQmetrix RQ: stock and sales at the wireless-retail partner stores, from report exports.

RQ's API (Data Connect) is a paid add-on that isn't in place, so this connector only
reads the reports people already export from RQ (CSV or Excel). They can arrive three ways:
  * dropped in a Google Drive folder (RQ_DRIVE_FOLDER_ID),
  * emailed to the ops mailbox: scheduled RQ reports work well (RQ_GMAIL_QUERY),
  * uploaded on the dashboard (Import file).

Columns are matched by name (connectors.files), so most inventory and sales layouts work.
"""
from __future__ import annotations

from ..demo import fixtures
from ..timeutil import parse_iso, utcnow
from . import google_auth
from ._util import to_dec, to_int
from .base import Connector, NLocation, NSale, NStock, SyncBatch, register
from .files import FileImports, parse_export


def parse_report(rows, *, default_store: str, tz: str):
    return parse_export(rows, source="iqmetrix_rq", tz=tz, default_store=default_store)


@register
class IQMetrixRQConnector(FileImports, Connector):
    key = "iqmetrix_rq"
    name = "iQmetrix RQ"
    category = "pos"
    capabilities = ("inventory", "sales")
    settings_keys = ()
    import_folder_key = "RQ_DRIVE_FOLDER_ID"
    import_query_key = "RQ_GMAIL_QUERY"
    docs_url = None
    status_note = ("Report-file import (no API): drop RQ inventory/sales exports in RQ_DRIVE_FOLDER_ID, email them "
                   "(RQ_GMAIL_QUERY), or use Import file.")

    def missing_settings(self) -> list[str]:
        missing = [] if google_auth.method(self.env) else [google_auth.AUTH_REQUIREMENT]
        if not (self.env.get("RQ_DRIVE_FOLDER_ID") or self.env.get("RQ_GMAIL_QUERY")):
            missing.append("RQ_DRIVE_FOLDER_ID or RQ_GMAIL_QUERY")
        return missing

    def is_configured(self) -> bool:
        return False  # there is no API mode; with a folder or query set the mode is "files"

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
