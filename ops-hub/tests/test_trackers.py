"""Inbound, outbound and productivity: the new pulls, their mapping, the trackers and their API."""
import json

import httpx
from fastapi.testclient import TestClient

from opshub.connectors.base import Connector
from opshub.connectors.extensiv import ExtensivConnector
from opshub.connectors.files import parse_export
from opshub.connectors.lightspeed import LightspeedConnector, _token_cache
from opshub.connectors.shopify import ShopifyConnector
from opshub.services import trackers

from test_live_clients import Router


def test_extensiv_receivers_and_order_milestones():
    c = ExtensivConnector(env={})
    receivers = [
        {"ReadOnly": {"ReceiverId": 70, "Status": 1, "ProcessDate": "2026-10-20T18:00:00",
                      "FacilityIdentifier": {"Id": 3}},
         "ReferenceNum": "PO 55", "ArrivalDate": "2026-10-20T08:00:00", "ExpectedDate": "2026-10-19T00:00:00",
         "ShipCarrier": "Ridgeway",
         "ReceiveItems": [{"ItemIdentifier": {"Sku": "A"}, "Qty": 10, "PalletIdentifier": {"Id": 1}},
                          {"ItemIdentifier": {"Sku": "A"}, "Qty": 5, "PalletIdentifier": {"Id": 2}}]},
        {"ReadOnly": {"ReceiverId": 71, "Status": 0, "FacilityIdentifier": {"Id": 3}}, "ReferenceNum": "PO 56",
         "ExpectedDate": "2099-01-01T00:00:00", "ReceiveItems": [{"ItemIdentifier": {"Sku": "B"}, "Qty": 8}]},
    ]
    orders = [{"ReadOnly": {"OrderId": 1, "Status": 1, "ProcessDate": "2026-10-20T12:00:00",
                            "PickDoneDate": "2026-10-20T09:00:00", "PackDoneDate": "2026-10-20T10:30:00",
                            "LastModifiedByIdentifier": {"Name": "Jose R."}, "FacilityIdentifier": {"Id": 3}},
               "ReferenceNum": "#100", "OrderItems": [{"ItemIdentifier": {"Sku": "A"}, "Qty": 3}]}]
    batch = c.normalize(orders=orders, summaries=None, lots=[], activity=[], receivers=receivers)
    done, open_ = batch.receipts
    assert (done.status, done.reference, done.pallets, done.location) == ("received", "PO 55", 2, "3")
    assert done.lines[0].sku == "A" and done.lines[0].received == 15 and done.received_at.hour == 18
    assert open_.status == "expected" and open_.lines[0].expected == 8 and open_.lines[0].received == 0
    o = batch.orders[0]
    assert (o.picked_at.hour, o.packed_at.minute, o.handled_by) == (9, 30, "Jose R.")
    acts = c.derive_activity([], [], done.received_at.date(),
                             [{"readOnly": {"receiverId": 70, "status": 1}, "arrivalDate": "2026-10-20T08:00:00",
                               "receiveItems": [{"palletIdentifier": {"id": 1}}, {"palletIdentifier": {"id": 2}}]}])
    assert [(a.charge_code, int(a.qty)) for a in acts] == [("RCV-PLT", 2)]


def test_shopify_fulfillment_and_delivery_times():
    order = {"id": "gid://shopify/Order/1", "name": "#1", "createdAt": "2026-10-18T10:00:00Z",
             "displayFulfillmentStatus": "FULFILLED", "lineItems": {"nodes": [{"sku": "A", "quantity": 1}]},
             "fulfillments": [{"createdAt": "2026-10-19T02:00:00Z", "deliveredAt": "2026-10-21T20:00:00Z",
                               "status": "SUCCESS", "displayStatus": "DELIVERED", "trackingInfo": []}]}
    o = ShopifyConnector(env={}).normalize([order], []).orders[0]
    assert o.status == "delivered" and o.shipped_at.day == 19 and o.delivered_at.day == 21


def test_lightspeed_employees_hours_and_purchase_orders(monkeypatch, session):
    router = Router()
    monkeypatch.setattr(Connector, "transport", httpx.MockTransport(router))
    _token_cache.clear()
    base = "https://api.lightspeedapp.com/API/V3/Account/77"
    router.on("POST", "https://cloud.lightspeedapp.com/auth/oauth/token", lambda req: {"access_token": "a", "expires_in": 1800})
    router.on("GET", f"{base}/Shop.json", lambda req: {"Shop": {"shopID": "1", "name": "Main"}})
    router.on("GET", f"{base}/Item.json", lambda req: {"Item": {"itemID": "5", "customSku": "A"}})
    router.on("GET", f"{base}/Sale.json", lambda req: {"Sale": {
        "saleID": "9", "shopID": "1", "completed": "true", "employeeID": "11", "timeStamp": "2026-10-20T20:00:00+00:00",
        "SaleLines": {"SaleLine": {"saleLineID": "90", "itemID": "5", "unitQuantity": "2", "calcTotal": "20"}}}})
    router.on("GET", f"{base}/EmployeeHours.json", lambda req: {"EmployeeHours": [
        {"employeeHoursID": "1", "employeeID": "11", "shopID": "1", "checkIn": "2026-10-20T18:00:00+00:00",
         "checkOut": "2026-10-21T02:00:00+00:00"}]})
    router.on("GET", f"{base}/Employee.json", lambda req: {"Employee": {"employeeID": "11", "firstName": "Keoni",
                                                                          "lastName": "Kahale"}})
    router.on("GET", f"{base}/Order.json", lambda req: httpx.Response(403, json={"message": "scope"}))
    env = {"LIGHTSPEED_ACCOUNT_ID": "77", "LIGHTSPEED_CLIENT_ID": "c", "LIGHTSPEED_CLIENT_SECRET": "s",
           "LIGHTSPEED_REFRESH_TOKEN": "rt"}
    batch = LightspeedConnector(env=env).pull(demo_mode=True)
    assert batch.sales[0].employee == "Keoni Kahale"
    assert batch.labor[0].employee == "Keoni Kahale" and batch.labor[0].clock_out.hour == 2
    assert batch.receipts == [] and batch.warnings and batch.warnings[0].startswith("Order:")  # no purchasing scope

    pos = {"Order": {"orderID": "301", "shopID": "1", "arrivalDate": "2026-10-22T00:00:00+00:00",
                     "receivedDate": "2026-10-23T00:00:00+00:00", "complete": "true", "Vendor": {"name": "Island Coffee"},
                     "OrderLines": {"OrderLine": [{"itemID": "5", "quantity": "10", "numReceived": "8"}]}}}
    rc = LightspeedConnector(env={}).normalize({"Shop": []}, {"Item": {"itemID": "5", "customSku": "A"}}, {"Sale": []},
                                               orders=pos).receipts[0]
    assert (rc.kind, rc.status, rc.vendor, rc.lines[0].expected, rc.lines[0].received) == \
        ("purchase_order", "received", "Island Coffee", 10, 8)


def test_receipt_and_salesperson_files():
    kind, batch = parse_export([["Receiver #", "SKU", "Qty Expected", "Qty Received", "Arrival Date", "Vendor"],
                                ["R-1", "A", "10", "10", "10/20/2026", "Ridgeway"],
                                ["R-1", "B", "5", "3", "10/20/2026", "Ridgeway"],
                                ["R-2", "A", "12", "", "11/02/2026", "Tri-State"]], source="extensiv", tz="Pacific/Honolulu")
    assert kind == "receipts"
    r1, r2 = batch.receipts
    assert (r1.reference, r1.status, sum(l.expected for l in r1.lines), sum(l.received for l in r1.lines)) == \
        ("R-1", "receiving", 15, 13)
    assert r2.status == "expected" and r2.expected_at.month == 11
    kind, batch = parse_export([["Store", "Date", "SKU", "Qty", "Sales Person"],
                                ["Eastgate", "10/20/2026", "A", "2", "Alana K."]], source="iqmetrix_rq",
                               tz="Pacific/Honolulu")
    assert kind == "sales" and batch.sales[0].employee == "Alana K."


def test_trackers_on_demo_data(synced, settings):
    inb = trackers.inbound(synced, settings, days=30)
    refs = {r["reference"]: r for r in inb["open"]}
    assert refs["PO 7781"]["status"] == "expected" and refs["TR-HQ-0921"]["status"] == "receiving"
    assert refs["PO-304"]["late"] and inb["kpis"]["late_receipts"] >= 2
    assert inb["kpis"]["received_units"] > 0 and inb["kpis"]["short_receipts"] >= 1
    assert inb["kpis"]["dock_to_stock_hours"] > 0 and len(inb["series"]) == 30
    assert any(f["reference"] == "IN-PO-7781" for f in inb["freight"])

    out = trackers.outbound(synced, settings, days=30)
    assert {o["origin"] for o in out["by_origin"]} == {"HQ-WH", "P3PL-01"}
    assert any(b["number"] == "#2048" for b in out["backlog"]) and out["kpis"]["backlog"] == len(out["backlog"])
    assert out["kpis"]["order_to_ship_hours"] > 0 and out["kpis"]["ship_to_deliver_days"] > 0
    assert all(out["stages"][k] for k in ("order_to_pick_hours", "pick_to_pack_hours", "pack_to_ship_hours"))
    assert sum(a["orders"] for a in out["aging"]) == out["kpis"]["backlog"]
    linked = next(o for o in out["shipped_orders"] if o["number"] == "#2044")
    assert linked["origin"] == "P3PL-01" and linked["picked_at"] and linked["delivered_at"]  # 3PL scans + carrier delivery

    prod = trackers.productivity(synced, settings, days=30)
    assert {h["name"] for h in prod["handlers"]} >= {"Kalani A.", "Jose R.", "Mele S.", "Tran N."}
    keoni = next(p for p in prod["people"] if p["name"] == "Keoni Kahale")
    assert keoni["hours"] > 0 and keoni["revenue_per_hour"] > 0 and keoni["location"] == "STR-DTN"
    assert any(p["source"] == "iqmetrix_rq" and p["hours"] == 0 for p in prod["people"])
    assert prod["kpis"]["staff_on_clock"] >= 1 and prod["kpis"]["store_revenue_per_labor_hour"] > 0


def test_tracker_api_exports_and_reports(synced, settings):
    from opshub.api import create_app
    from opshub.services import jobs, muse

    client = TestClient(create_app())
    assert client.get("/api/trackers/outbound?days=7").json()["days"] == 7
    assert client.get("/api/trackers/nope").status_code == 404
    for name, first in (("inbound", "status"), ("outbound", "number"), ("backlog", "number"),
                        ("productivity", "name"), ("warehouse-handlers", "name")):
        r = client.get(f"/api/export/{name}.csv?days=14")
        assert r.status_code == 200 and r.text.split(",")[0] == first and len(r.text.splitlines()) > 1
    tabs = jobs.report_tables(synced, settings)
    assert {"Inbound", "Outbound", "Productivity"} <= set(tabs)
    data = muse.build_export(synced, settings)
    assert data["trackers"]["outbound"]["kpis"]["backlog"] > 0
    assert "Inbound, outbound and productivity" in muse.brief_markdown(json.loads(json.dumps(data, default=str)))
