"""Extensiv 3PL Warehouse Manager: the partner warehouse's orders, stock, lots and billable activity.

We are a customer of the 3PL, so access goes through the 3PL's API account: the warehouse
issues a client id/secret and a user login id for us, plus our customer id and facility id(s).

The API returns PascalCase JSON (``ReadOnly``, ``ResourceList``, ``OnHandQty``); responses are
camel-cased on arrival so one mapping handles them. Stock and lots come from
/inventory/stockdetails, orders from /orders.

Receivers (inbound receipts and ASNs) come from /inventory/receivers. Orders carry the
warehouse's pick, pack and ship times and who last touched them, for the outbound and
productivity trackers.

Billable activity has no customer-side endpoint, so it is derived from what the WMS shows:
one row per shipped order and its extra units, receipt pallets per receiver, and the
count of pallets in storage for the month. EXTENSIV_ACTIVITY_CODES maps these to your
rate card's charge codes.
"""
from __future__ import annotations

import base64
import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..demo import fixtures
from ..timeutil import parse_date, parse_iso, utcnow
from ._util import to_dec, to_int
from .base import (Connector, NActivity, NLot, NOrder, NOrderLine, NReceipt, NReceiptLine, NShipment, NStock, SyncBatch,
                   register)
from .files import FileImports

ITEMS_REL = "http://api.3plCentral.com/rels/orders/item"
RECEIVE_ITEMS_REL = "http://api.3plCentral.com/rels/inventory/receiveitem"
DEFAULT_CODES = {"order": "PICK-ORD", "extra_unit": "PICK-UNIT", "receipt_pallet": "RCV-PLT",
                 "storage_pallet": "STOR-PLT", "hazmat_order": "HAZ-HNDL"}


def camel_keys(obj):
    """Lower-case the first letter of every key, recursively: ReadOnly -> readOnly, OrderId -> orderId."""
    if isinstance(obj, list):
        return [camel_keys(v) for v in obj]
    if isinstance(obj, dict):
        return {(k[:1].lower() + k[1:] if k and k[0].isupper() else k): camel_keys(v) for k, v in obj.items()}
    return obj


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


@register
class ExtensivConnector(FileImports, Connector):
    key = "extensiv"
    name = "Extensiv 3PL Warehouse Manager"
    category = "warehouse"
    capabilities = ("orders", "inventory", "lots", "shipments", "billing", "inbound", "outbound")
    settings_keys = ("EXTENSIV_CLIENT_ID", "EXTENSIV_CLIENT_SECRET", "EXTENSIV_USER_LOGIN_ID", "EXTENSIV_CUSTOMER_ID",
                     "EXTENSIV_FACILITY_ID")
    import_folder_key = "EXTENSIV_IMPORT_FOLDER_ID"
    import_query_key = "EXTENSIV_IMPORT_GMAIL_QUERY"
    docs_url = "https://developer.extensiv.com/"
    status_note = ("Live client built (orders with pick/pack/ship times, receivers, stock details with lots/best-by, "
                   "derived billable activity). Needs API credentials issued by the 3PL; verify on first run.")

    @property
    def vendor(self) -> str:
        return self.setting("EXTENSIV_VENDOR_NAME", fixtures.VENDOR_3PL)

    @property
    def codes(self) -> dict[str, str]:
        return {**DEFAULT_CODES, **json.loads(self.setting("EXTENSIV_ACTIVITY_CODES") or "{}")}

    # --- live -----------------------------------------------------------------------

    def _token(self, client) -> str:
        cached = getattr(self, "_tok", None)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        basic = base64.b64encode(f"{self.setting('EXTENSIV_CLIENT_ID')}:{self.setting('EXTENSIV_CLIENT_SECRET')}"
                                 .encode()).decode()
        body = {"grant_type": "client_credentials", "user_login_id": self.setting("EXTENSIV_USER_LOGIN_ID")}
        if self.setting("EXTENSIV_TPL_KEY"):
            body["tpl"] = "{" + self.setting("EXTENSIV_TPL_KEY").strip("{}") + "}"
        resp = client.post(f"{self.base}/AuthServer/api/Token", json=body,
                           headers={"Authorization": f"Basic {basic}", "Content-Type": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        self._tok = (data["access_token"], time.time() + int(data.get("expires_in", 1800)))
        return self._tok[0]

    @property
    def base(self) -> str:
        return self.setting("EXTENSIV_BASE_URL", "https://secure-wms.com").rstrip("/")

    def _pages(self, client, path: str, params: dict, list_key: str = "resourceList", page_size: int = 200) -> list[dict]:
        out, page = [], 1
        while True:
            for attempt in range(4):
                resp = client.get(f"{self.base}/{path}", params={**params, "pgsiz": page_size, "pgnum": page},
                                  headers={"Authorization": f"Bearer {self._token(client)}",
                                           "Accept": "application/hal+json"})
                if resp.status_code in (429, 503) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                break
            resp.raise_for_status()
            body = camel_keys(resp.json())
            rows = body.get(list_key) or (body.get("_embedded") or {}).get("item") or []
            out += rows
            total = body.get("totalResults")
            if len(rows) < page_size or (total is not None and len(out) >= int(total)):
                return out
            page += 1

    def test(self) -> str:
        with self.http() as client:
            resp = client.get(f"{self.base}/orders", params={"pgsiz": 1, "pgnum": 1},
                              headers={"Authorization": f"Bearer {self._token(client)}", "Accept": "application/hal+json"})
            resp.raise_for_status()
            body = camel_keys(resp.json())
        return f"Signed in to {self.base}; {body.get('totalResults', '?')} orders visible to this login"

    def pull_live(self, since: datetime | None) -> SyncBatch:
        since = since or utcnow() - timedelta(days=int(self.setting("EXTENSIV_BACKFILL_DAYS", "60")))
        customer = str(self.setting("EXTENSIV_CUSTOMER_ID"))
        facilities = [f.strip() for f in self.setting("EXTENSIV_FACILITY_ID", "").split(",") if f.strip()]
        changed = f"ReadOnly.lastModifiedDate=ge={since.replace(microsecond=0).isoformat()}"
        warnings = []
        with self.http() as client:
            orders = self._pages(client, "orders", {
                "detail": "All", "itemdetail": "All", "sort": "ReadOnly.lastModifiedDate", "rql": changed})
            try:
                receivers = self._pages(client, "inventory/receivers", {
                    "detail": "All", "itemdetail": "All", "sort": "ReadOnly.lastModifiedDate",
                    "rql": changed})
            except Exception as exc:  # orders and stock still sync if this login can't read receivers
                receivers, warnings = [], [f"receivers: {exc}"[:300]]
            lots = []
            for fac in facilities:
                for row in self._pages(client, "inventory/stockdetails", {
                        "customerid": customer, "facilityid": fac, "sort": "receivedDate"}, page_size=500):
                    row.setdefault("facilityIdentifier", {"id": fac})
                    lots.append(row)
        # A warehouse-level login can see other customers' orders; keep ours.
        ours = lambda r: str(((r.get("readOnly") or {}).get("customerIdentifier") or {}).get("id") or customer) == customer  # noqa: E731
        orders, receivers = [o for o in orders if ours(o)], [r for r in receivers if ours(r)]
        batch = self.normalize(orders=orders, summaries=None, lots=lots, activity=[], receivers=receivers)
        batch.activities = self.derive_activity(orders, lots, utcnow().date(), receivers)
        batch.warnings = warnings
        return batch

    def derive_activity(self, orders: list[dict], lots: list[dict], today: date,
                        receivers: list[dict] | None = None) -> list[NActivity]:
        codes, out = self.codes, []
        for o in orders:
            ro = o.get("readOnly") or {}
            shipped = parse_date(ro.get("processDate"))
            if ro.get("status") != 1 or not shipped:
                continue
            ref = f"order {ro.get('orderId')}"
            units = sum(to_int(i.get("qty")) for i in self._items(o))
            if codes.get("order"):
                out.append(NActivity(self.vendor, codes["order"], shipped, Decimal(1), ref))
            if codes.get("extra_unit") and units > 1:
                out.append(NActivity(self.vendor, codes["extra_unit"], shipped, Decimal(units - 1), ref))
        receipts: dict[str, tuple[date, set]] = {}
        pallets_now: set = set()
        for lot in lots:
            pallet = (lot.get("palletIdentifier") or {}).get("id")
            receiver = lot.get("receiverId")
            received = parse_date(lot.get("receivedDate"))
            if receiver and received:
                d, pals = receipts.setdefault(str(receiver), (received, set()))
                if pallet:
                    pals.add(pallet)
            if pallet and to_int(lot.get("onHandQty", lot.get("onHand"))) > 0:
                pallets_now.add(pallet)
        for rc in receivers or []:  # receivers list their own pallets; prefer them to the stock-detail view
            ro = rc.get("readOnly") or {}
            pals = {(i.get("palletIdentifier") or {}).get("id") for i in self._receive_items(rc)} - {None}
            received = parse_date(rc.get("arrivalDate") or ro.get("processDate"))
            if ro.get("status") == 1 and pals and received:
                receipts[str(ro.get("receiverId"))] = (received, pals)
        if codes.get("receipt_pallet"):
            for receiver, (d, pals) in receipts.items():
                if pals:
                    out.append(NActivity(self.vendor, codes["receipt_pallet"], d, Decimal(len(pals)), f"receiver {receiver}"))
        if codes.get("storage_pallet") and pallets_now:
            # Updated on every sync, so after the month closes it holds the month-end count.
            out.append(NActivity(self.vendor, codes["storage_pallet"], _month_end(today), Decimal(len(pallets_now)),
                                 f"month-end pallets {today:%Y-%m}"))
        return out

    # --- demo & mapping ---------------------------------------------------------------

    def pull_demo(self) -> SyncBatch:
        now = utcnow()
        return self.normalize(
            orders=fixtures.extensiv_orders(now),
            summaries=fixtures.extensiv_stock_summaries(now),
            lots=fixtures.extensiv_lots(now),
            activity=fixtures.extensiv_billing_activity(now),
            receivers=fixtures.extensiv_receivers(now),
        )

    @staticmethod
    def _items(o: dict) -> list[dict]:
        emb = o.get("_embedded") or {}
        return o.get("orderItems") or emb.get(ITEMS_REL) or emb.get("item") or []

    @staticmethod
    def _receive_items(r: dict) -> list[dict]:
        emb = r.get("_embedded") or {}
        return r.get("receiveItems") or emb.get(RECEIVE_ITEMS_REL) or emb.get("item") or []

    def normalize(self, orders: list[dict], summaries: list[dict] | None, lots: list[dict],
                  activity: list[dict], receivers: list[dict] | None = None) -> SyncBatch:
        orders, lots = camel_keys(orders), camel_keys(lots)
        batch = SyncBatch()
        now = utcnow()
        for r in camel_keys(receivers or []):
            ro = r.get("readOnly") or {}
            arrived = parse_iso(r.get("arrivalDate"))
            raw = ro.get("status")
            status = ({1: "received", 2: "cancelled"}.get(raw)
                      or ("receiving" if arrived and arrived <= now else "expected"))
            items = self._receive_items(r)
            done = status == "received"
            lines: dict[str, NReceiptLine] = {}
            for i in items:
                sku = (i.get("itemIdentifier") or {}).get("sku")
                if not sku:
                    continue
                qty = to_int(i.get("qty"))
                ln = lines.setdefault(sku, NReceiptLine(sku=sku))
                ln.expected += to_int(i.get("expectedQty", qty))
                ln.received += qty if done else to_int(i.get("receivedQty", 0))
            pallets = {(i.get("palletIdentifier") or {}).get("id") for i in items} - {None}
            fac = str((ro.get("facilityIdentifier") or {}).get("id") or "")
            batch.receipts.append(NReceipt(
                external_id=str(ro.get("receiverId")), status=status, kind="receipt",
                reference=r.get("referenceNum") or r.get("poNum") or None,
                vendor=r.get("shipCarrier") or None, location=fac or None,
                expected_at=parse_iso(r.get("expectedDate")), arrived_at=arrived,
                received_at=parse_iso(ro.get("processDate") or (r.get("arrivalDate") if done else None)),
                pallets=len(pallets) or None, lines=list(lines.values()), notes=r.get("notes") or None,
            ))
        for o in orders:
            ro = o.get("readOnly") or {}
            facility = str((ro.get("facilityIdentifier") or {}).get("id") or "")
            raw = {0: "Open", 1: "Complete", 2: "Cancelled"}.get(ro.get("status"), str(ro.get("status")))
            if ro.get("status") == 2:
                status = "cancelled"
            elif ro.get("status") == 1:
                status = "shipped"
            elif ro.get("pickDoneDate"):
                status = "processing"
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
                order_type="wholesale" if o.get("poNum") else None, shipped_at=parse_iso(ro.get("processDate")),
                picked_at=parse_iso(ro.get("pickDoneDate")), packed_at=parse_iso(ro.get("packDoneDate")),
                handled_by=(ro.get("lastModifiedByIdentifier") or {}).get("name") or None,
                lines=[NOrderLine(sku=(i.get("itemIdentifier") or {}).get("sku"), qty=to_int(i.get("qty")))
                       for i in self._items(o)],
                ship_from=facility or None,
            ))
            routing = o.get("routingInfo") or {}
            tracking = routing.get("trackingNumber") or routing.get("billOfLading")
            if tracking:
                batch.shipments.append(NShipment(
                    reference=f"EXT-{ro.get('orderId')}", direction="outbound",
                    mode="ltl" if routing.get("billOfLading") and not routing.get("trackingNumber") else "parcel",
                    carrier_name=routing.get("carrier") or "Unknown carrier", tracking_ref=tracking,
                    status="in_transit", origin=self.vendor, destination=ship_to.get("companyName"),
                    order_number=ref, booked_at=parse_iso(ro.get("processDate") or ro.get("lastModifiedDate")),
                ))
        totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
        for lot in lots:
            sku = (lot.get("itemIdentifier") or {}).get("sku")
            fac = str((lot.get("facilityIdentifier") or {}).get("id") or lot.get("facilityId") or "")
            on_hand = to_int(lot.get("onHandQty", lot.get("onHand")))
            available = to_int(lot.get("availableQty", lot.get("available", on_hand)))
            if not sku or not on_hand:
                continue
            t = totals[(sku, fac)]
            t[0] += on_hand
            t[1] += available
            batch.lots.append(NLot(
                sku=sku, location=fac, lot_number=lot.get("lotNumber") or f"RI-{lot.get('receiveItemId')}",
                qty_on_hand=on_hand, best_by=parse_date(lot.get("expirationDate")),
                received_on=parse_date(lot.get("receivedDate")),
            ))
        if summaries is not None:
            for s in camel_keys(summaries):
                sku = (s.get("itemIdentifier") or {}).get("sku")
                if sku:
                    batch.stock.append(NStock(sku=sku, location=str(s.get("facilityId")), on_hand=to_int(s.get("onHand")),
                                              allocated=to_int(s.get("allocated")), available=to_int(s.get("available"))))
        else:  # live: stock is the sum of receive items
            for (sku, fac), (on_hand, available) in totals.items():
                batch.stock.append(NStock(sku=sku, location=fac, on_hand=on_hand, allocated=on_hand - available,
                                          available=available))
        for a in activity:
            batch.activities.append(NActivity(vendor=self.vendor, charge_code=a["chargeCode"],
                                              activity_date=parse_date(a["date"]), qty=to_dec(a["qty"]),
                                              reference=a.get("reference") or ""))
        return batch
