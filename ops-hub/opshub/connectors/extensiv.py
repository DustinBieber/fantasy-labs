"""Extensiv 3PL Warehouse Manager: the partner warehouse's orders, stock, lots and billable activity.

We are a customer of the 3PL, so access is the 3PL's API with our customer id.
The 3PL has to issue the client id/secret and a user login id for us.
"""
from __future__ import annotations

from datetime import datetime

from ..demo import fixtures
from ..timeutil import parse_date, parse_iso, utcnow
from ._util import to_dec, to_int
from .base import Connector, NActivity, NLot, NOrder, NOrderLine, NShipment, NStock, SyncBatch, register

ITEMS_REL = "http://api.3plCentral.com/rels/orders/item"


@register
class ExtensivConnector(Connector):
    key = "extensiv"
    name = "Extensiv 3PL Warehouse Manager"
    category = "warehouse"
    capabilities = ("orders", "inventory", "lots", "shipments", "billing")
    settings_keys = ("EXTENSIV_CLIENT_ID", "EXTENSIV_CLIENT_SECRET", "EXTENSIV_USER_LOGIN_ID", "EXTENSIV_CUSTOMER_ID")
    docs_url = "https://developer.extensiv.com/"
    status_note = ("Mapping built for /orders, /inventory/stocksummaries and /inventory (lots). Live calls need "
                   "API credentials from the 3PL. Billable activity has no customer endpoint: built from "
                   "receivers, closed orders and month-end stock.")

    @property
    def vendor(self) -> str:
        return self.setting("EXTENSIV_VENDOR_NAME", fixtures.VENDOR_3PL)

    def pull_demo(self) -> SyncBatch:
        now = utcnow()
        return self.normalize(
            orders=fixtures.extensiv_orders(now),
            summaries=fixtures.extensiv_stock_summaries(now),
            lots=fixtures.extensiv_lots(now),
            activity=fixtures.extensiv_billing_activity(now),
        )

    def normalize(self, orders: list[dict], summaries: list[dict], lots: list[dict],
                  activity: list[dict]) -> SyncBatch:
        batch = SyncBatch()
        for o in orders:
            ro = o.get("readOnly") or {}
            facility = str((ro.get("facilityIdentifier") or {}).get("id") or "")
            items = (o.get("_embedded") or {}).get(ITEMS_REL) or o.get("orderItems") or []
            raw = {0: "Open", 1: "Complete", 2: "Cancelled"}.get(ro.get("status"), str(ro.get("status")))
            if ro.get("status") == 2:
                status = "cancelled"
            elif ro.get("status") == 1:
                status = "shipped"
            else:
                status = "allocated" if ro.get("fullyAllocated") else "open"
            notes = "\n".join(n for n in (o.get("notes"), o.get("shippingNotes")) if n) or None
            ref = o.get("referenceNum") or str(ro.get("orderId"))
            ship_to = o.get("shipTo") or {}
            batch.orders.append(NOrder(
                external_id=str(ro.get("orderId")), number=ref, reference=ref, status=status, raw_status=raw,
                channel="warehouse", customer_name=ship_to.get("companyName") or ship_to.get("name"),
                placed_at=parse_iso(ro.get("creationDate")), updated_at=parse_iso(ro.get("lastModifiedDate")),
                notes=notes, required_by=parse_iso(o.get("shipCancelDate")),
                order_type="wholesale" if o.get("poNum") else None,
                lines=[NOrderLine(sku=(i.get("itemIdentifier") or {}).get("sku"), qty=to_int(i.get("qty")))
                       for i in items],
                ship_from=facility or None,
            ))
            routing = o.get("routingInfo") or {}
            if routing.get("trackingNumber"):
                batch.shipments.append(NShipment(
                    reference=f"EXT-{ro.get('orderId')}", direction="outbound", mode="parcel",
                    carrier_name=routing.get("carrier") or "Unknown carrier", tracking_ref=routing["trackingNumber"],
                    status="in_transit", origin=self.vendor, destination=ship_to.get("companyName"),
                    order_number=ref, booked_at=parse_iso(ro.get("lastModifiedDate")),
                ))
        for s in summaries:
            sku = (s.get("itemIdentifier") or {}).get("sku")
            if sku:
                batch.stock.append(NStock(sku=sku, location=str(s.get("facilityId")), on_hand=to_int(s.get("onHand")),
                                          allocated=to_int(s.get("allocated")), available=to_int(s.get("available"))))
        for lot in lots:
            sku = (lot.get("itemIdentifier") or {}).get("sku")
            if not sku or not to_int(lot.get("onHand")):
                continue
            batch.lots.append(NLot(
                sku=sku, location=str(lot.get("facilityId")),
                lot_number=lot.get("lotNumber") or f"RI-{lot.get('receiveItemId')}",
                qty_on_hand=to_int(lot.get("onHand")),
                best_by=parse_date(lot.get("expirationDate")), received_on=parse_date(lot.get("receivedDate")),
            ))
        for a in activity:
            batch.activities.append(NActivity(vendor=self.vendor, charge_code=a["chargeCode"],
                                              activity_date=parse_date(a["date"]), qty=to_dec(a["qty"]),
                                              reference=a.get("reference") or ""))
        return batch

    def pull_live(self, since: datetime | None) -> SyncBatch:
        # Token: POST https://secure-wms.com/AuthServer/api/Token (Basic client_id:secret,
        #   {"grant_type": "client_credentials", "user_login_id": ...})
        # Orders: GET https://secure-wms.com/orders?detail=OrderItems&rql=readOnly.customerIdentifier.id==<id>
        # Stock:  GET https://secure-wms.com/inventory/stocksummaries?rql=...
        # Lots:   GET https://secure-wms.com/inventory?rql=...  (lotNumber, expirationDate per receive item)
        return super().pull_live(since)
