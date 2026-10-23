"""iQmetrix RQ: stock and sales at the wireless-retail partner stores.

RQ data is exposed through iQmetrix Data Connect reports. The row field names
below are placeholders until we see the reports enabled on your RQ account.
"""
from __future__ import annotations

from datetime import datetime

from ..demo import fixtures
from ..timeutil import parse_iso, utcnow
from ._util import to_dec, to_int
from .base import Connector, NLocation, NSale, NStock, SyncBatch, register


@register
class IQMetrixRQConnector(Connector):
    key = "iqmetrix_rq"
    name = "iQmetrix RQ"
    category = "pos"
    capabilities = ("inventory", "sales")
    settings_keys = ("RQ_DATACONNECT_URL", "RQ_COMPANY_ID", "RQ_API_TOKEN")
    docs_url = "https://developers.iqmetrix.com/"
    status_note = ("Report field names are assumed. Need the Data Connect report list and credentials from "
                   "iQmetrix or the store operator.")

    def pull_demo(self) -> SyncBatch:
        return self.normalize(fixtures.rq_stores(), fixtures.rq_inventory(), fixtures.rq_sales(utcnow()))

    def normalize(self, stores: list[dict], inventory: list[dict], sales: list[dict]) -> SyncBatch:
        batch = SyncBatch()
        for s in stores:
            batch.locations.append(NLocation(code=f"RQ-{s['StoreID']}", name=s.get("StoreName") or f"Store {s['StoreID']}",
                                             kind="store", external_id=str(s["StoreID"])))
        for row in inventory:
            on_hand = to_int(row.get("QuantityOnHand"))
            committed = to_int(row.get("QuantityCommitted"))
            batch.stock.append(NStock(sku=row["ProductSKU"], location=str(row["StoreID"]), on_hand=on_hand,
                                      allocated=committed, available=on_hand - committed))
        for row in sales:
            batch.sales.append(NSale(external_id=f"{row['InvoiceIDByStore']}:{row.get('LineID', 1)}",
                                     sku=row["ProductSKU"], location=str(row["StoreID"]),
                                     sold_at=parse_iso(row.get("DateCreated")), qty=to_int(row.get("Quantity")),
                                     net_amount=to_dec(row.get("TotalPrice"))))
        return batch

    def pull_live(self, since: datetime | None) -> SyncBatch:
        return super().pull_live(since)
