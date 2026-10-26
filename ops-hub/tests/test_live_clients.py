"""Live API clients against mocked HTTP: request shapes, auth, pagination and mapping."""
import json

import httpx
import pytest

from opshub.connectors import google_auth
from opshub.connectors.base import Connector
from opshub.connectors.extensiv import ExtensivConnector, camel_keys
from opshub.connectors.google import (GmailConnector, GoogleCalendarConnector, GoogleDriveConnector,
                                      GoogleSheetsConnector)
from opshub.connectors.lightspeed import LightspeedConnector, _token_cache

GOOGLE_ENV = {"GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "sec", "GOOGLE_OAUTH_REFRESH_TOKEN": "rt"}


class Router:
    """Maps (method, url-prefix) to handlers and records every request."""

    def __init__(self):
        self.routes, self.calls = [], []

    def on(self, method, prefix, fn):
        self.routes.append((method, prefix, fn))
        return self

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        url = str(request.url)
        for method, prefix, fn in self.routes:
            if request.method == method and url.startswith(prefix):
                out = fn(request)
                return out if isinstance(out, httpx.Response) else httpx.Response(200, json=out)
        return httpx.Response(404, json={"error": "no route", "url": url})


@pytest.fixture
def router(monkeypatch):
    r = Router().on("POST", google_auth.TOKEN_URL, lambda req: {"access_token": "gtok", "expires_in": 3600})
    monkeypatch.setattr(Connector, "transport", httpx.MockTransport(r))
    google_auth.clear_cache()
    _token_cache.clear()
    yield r
    google_auth.clear_cache()


def test_google_connection_requirements():
    assert GoogleSheetsConnector(env={"GOOGLE_SHEETS_MASTER_ID": "x"}).missing_settings() == [google_auth.AUTH_REQUIREMENT]
    assert GoogleSheetsConnector(env={**GOOGLE_ENV, "GOOGLE_SHEETS_MASTER_ID": "x"}).is_configured()
    assert GmailConnector(env=GOOGLE_ENV).is_configured()  # mailbox = the signed-in account
    assert google_auth.method({"GOOGLE_SERVICE_ACCOUNT_JSON": "/k.json"}) == "service_account"


def test_sheets_reads_only_existing_tabs(router, settings):
    router.on("GET", "https://sheets.googleapis.com/v4/spreadsheets/MASTER/values:batchGet", lambda req: {
        "valueRanges": [{"values": [["sku", "name", "hazmat", "un_number"], ["PB-1", "Power bank", "yes", "UN3480"]]},
                        {"values": [["code", "name", "kind", "source", "external_id"],
                                    ["STORE-1", "Store", "store", "lightspeed", "1"]]}]})
    router.on("GET", "https://sheets.googleapis.com/v4/spreadsheets/MASTER", lambda req: {
        "sheets": [{"properties": {"title": "Products"}}, {"properties": {"title": "Locations"}},
                   {"properties": {"title": "Scratch"}}]})
    c = GoogleSheetsConnector(env={**GOOGLE_ENV, "GOOGLE_SHEETS_MASTER_ID": "MASTER"})
    batch = c.pull(demo_mode=True)
    assert batch.products[0].hazmat and batch.products[0].un_number == "UN3480"
    assert batch.locations[0].source == "lightspeed"
    get = [r for r in router.calls if "batchGet" in str(r.url)][0]
    assert get.url.params.get_list("ranges") == ["Products", "Locations"]
    assert get.headers["Authorization"] == "Bearer gtok"


def test_sheets_report_adds_missing_tabs(router):
    router.on("GET", "https://sheets.googleapis.com/v4/spreadsheets/REP", lambda req: {"sheets": [
        {"properties": {"title": "Stock"}}]})
    router.on("POST", "https://sheets.googleapis.com/v4/spreadsheets/REP", lambda req: {})
    c = GoogleSheetsConnector(env={**GOOGLE_ENV, "GOOGLE_SHEETS_MASTER_ID": "M", "GOOGLE_SHEETS_REPORT_ID": "REP"})
    assert c.write_reports({"Stock": [["a"], ["b"]], "Lots": [["c"]]}) == 3
    posts = [(str(r.url), json.loads(r.content)) for r in router.calls if r.method == "POST" and "REP" in str(r.url)]
    assert posts[0][1] == {"requests": [{"addSheet": {"properties": {"title": "Lots"}}}]}
    assert posts[1][0].endswith("values:batchClear") and posts[2][1]["data"][0]["range"] == "Stock!A1"


def test_drive_list_download_and_upload(router):
    files = {"files": [{"id": "f1", "name": "inv.csv", "mimeType": "text/csv", "modifiedTime": "2026-10-20T00:00:00Z"},
                       {"id": "f2", "name": "Sheet", "mimeType": "application/vnd.google-apps.spreadsheet",
                        "modifiedTime": "2026-10-21T00:00:00Z"}]}

    def list_or_find(req):
        q = req.url.params.get("q", "")
        return {"files": []} if "name =" in q else files

    router.on("GET", "https://www.googleapis.com/drive/v3/files/f1", lambda req: httpx.Response(200, content=b"a,b\n"))
    router.on("GET", "https://www.googleapis.com/drive/v3/files/f2/export", lambda req: httpx.Response(200, content=b"x,y\n"))
    router.on("GET", "https://www.googleapis.com/drive/v3/files", list_or_find)
    router.on("POST", "https://www.googleapis.com/upload/drive/v3/files", lambda req: {"id": "new1"})
    c = GoogleDriveConnector(env={**GOOGLE_ENV, "GOOGLE_DRIVE_FOLDER_ID": "FOLDER"})
    docs = c.pull(demo_mode=True).documents
    assert [d.attachments[0] for d in docs] == [("inv.csv", b"a,b\n"), ("Sheet.csv", b"x,y\n")]
    with c.http() as client:
        assert c.drive_upload(client, "FOLDER", "brief.md", b"# hi", "text/markdown") == "new1"
    up = [r for r in router.calls if "upload" in str(r.url)][0]
    assert b'"parents": ["FOLDER"]' in up.content and b"# hi" in up.content


def test_gmail_reads_text_and_attachments_and_sends(router):
    import base64

    enc = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    router.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1/attachments/A1",
              lambda req: {"data": enc(b"Vendor,X\n")})
    router.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/m1", lambda req: {
        "id": "m1", "internalDate": "1761200000000",
        "payload": {"headers": [{"name": "Subject", "value": "PRO 123 update"}, {"name": "From", "value": "a@b.c"}],
                    "parts": [{"mimeType": "text/html", "body": {"data": enc(b"<p>Delivered&nbsp;today</p>")}},
                              {"mimeType": "application/pdf", "filename": "skip.exe", "body": {"attachmentId": "X"}},
                              {"mimeType": "text/csv", "filename": "inv.csv", "body": {"attachmentId": "A1", "size": 9}}]}})
    router.on("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages", lambda req: {"messages": [{"id": "m1"}]})
    router.on("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", lambda req: {"id": "s1"})
    g = GmailConnector(env={**GOOGLE_ENV, "GMAIL_TO": "ops@example.com"})
    doc = g.pull(demo_mode=True).documents[0]
    assert doc.title == "PRO 123 update" and "Delivered today" in doc.text
    assert doc.attachments == [("inv.csv", b"Vendor,X\n")]
    g.send("Digest line one\nmore")
    raw = json.loads(router.calls[-1].content)["raw"]
    msg = base64.urlsafe_b64decode(raw + "==").decode()
    assert "To: ops@example.com" in msg and "Subject: [Ops Hub] Digest line one" in msg


def test_calendar_upserts_and_deletes_stale(router):
    def put(req):
        return httpx.Response(404) if req.url.path.endswith("/new1") else {"id": "old1"}

    router.on("PUT", "https://www.googleapis.com/calendar/v3/calendars/CAL/events/", put)
    router.on("POST", "https://www.googleapis.com/calendar/v3/calendars/CAL/events", lambda req: {"id": "new1"})
    router.on("DELETE", "https://www.googleapis.com/calendar/v3/calendars/CAL/events/", lambda req: httpx.Response(204))
    c = GoogleCalendarConnector(env={**GOOGLE_ENV, "GOOGLE_CALENDAR_ID": "CAL"})
    result = c.push_events([{"id": "old1", "summary": "a", "date": "2026-10-24"},
                            {"id": "new1", "summary": "b", "date": "2026-10-25"}], previous_ids={"old1", "gone1"})
    assert result == {"created": 1, "updated": 1, "deleted": 1}
    body = json.loads([r for r in router.calls if r.method == "POST" and "/events" in str(r.url)][0].content)
    assert body["start"] == {"date": "2026-10-25"} and body["end"] == {"date": "2026-10-26"}


def test_lightspeed_rotates_refresh_token_and_paginates(router, session):
    from opshub.services import kv

    tokens = iter([{"access_token": "a1", "refresh_token": "rt2", "expires_in": 1800}])
    router.on("POST", "https://cloud.lightspeedapp.com/auth/oauth/token", lambda req: next(tokens))
    base = "https://api.lightspeedapp.com/API/V3/Account/77"
    router.on("GET", f"{base}/Shop.json", lambda req: {"@attributes": {"next": ""}, "Shop": {"shopID": "1", "name": "Main"}})
    pages = {None: {"@attributes": {"next": f"{base}/Item.json?after=c2"},
                    "Item": [{"itemID": "5", "customSku": "A", "ItemShops": {"ItemShop": {"shopID": "1", "qoh": "3"}}}]},
             "c2": {"@attributes": {"next": ""},
                    "Item": {"itemID": "6", "customSku": "B", "ItemShops": {"ItemShop": [{"shopID": "1", "qoh": "1"}]}}}}
    router.on("GET", f"{base}/Item.json", lambda req: pages[req.url.params.get("after")])
    hits = {"n": 0}

    def sales(req):
        hits["n"] += 1
        if hits["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        assert req.url.params["timeStamp"].startswith(">,") and req.url.params["completed"] == "true"
        return {"@attributes": {"next": ""}, "Sale": {"saleID": "9", "shopID": "1", "completed": "true",
                                                     "timeStamp": "2026-10-20T10:00:00+00:00",
                                                     "SaleLines": {"SaleLine": {"saleLineID": "90", "itemID": "6",
                                                                                "unitQuantity": "2"}}}}

    router.on("GET", f"{base}/Sale.json", sales)
    env = {"LIGHTSPEED_ACCOUNT_ID": "77", "LIGHTSPEED_CLIENT_ID": "c", "LIGHTSPEED_CLIENT_SECRET": "s",
           "LIGHTSPEED_REFRESH_TOKEN": "rt1"}
    batch = LightspeedConnector(env=env).pull(demo_mode=True)
    assert [p.sku for p in batch.products] == ["A", "B"]
    assert batch.sales[0].sku == "B" and batch.sales[0].qty == 2
    assert kv.get("lightspeed_refresh_token") == "rt2"  # the rotated token replaces the old one
    assert LightspeedConnector(env={k: v for k, v in env.items() if k != "LIGHTSPEED_REFRESH_TOKEN"}).is_configured()


def test_extensiv_live_pascal_case_and_activity(router, monkeypatch):
    router.on("POST", "https://secure-wms.com/AuthServer/api/Token",
              lambda req: {"access_token": "et", "token_type": "Bearer", "expires_in": 1800})
    orders = {"TotalResults": 2, "ResourceList": [
        {"ReadOnly": {"OrderId": 1, "Status": 1, "ProcessDate": "2026-10-20T12:00:00", "CustomerIdentifier": {"Id": 9},
                      "FacilityIdentifier": {"Id": 3}, "CreationDate": "2026-10-19T00:00:00"},
         "ReferenceNum": "#100", "RoutingInfo": {"Carrier": "UPS", "TrackingNumber": "1Z9"},
         "OrderItems": [{"ItemIdentifier": {"Sku": "A"}, "Qty": 3}]},
        {"ReadOnly": {"OrderId": 2, "Status": 0, "CustomerIdentifier": {"Id": 99}}, "ReferenceNum": "#other",
         "OrderItems": []},
    ]}
    router.on("GET", "https://secure-wms.com/orders", lambda req: orders)
    router.on("GET", "https://secure-wms.com/inventory/stockdetails", lambda req: {"ResourceList": [
        {"ReceiveItemId": 5, "ItemIdentifier": {"Sku": "A"}, "LotNumber": "L1", "ExpirationDate": "2027-01-01T00:00:00",
         "ReceivedDate": "2026-10-01T00:00:00", "OnHand": 10, "Available": 8, "ReceiverId": 70,
         "PalletIdentifier": {"Id": 501}},
        {"ReceiveItemId": 6, "ItemIdentifier": {"Sku": "A"}, "OnHand": 4, "Available": 4, "ReceiverId": 70,
         "ReceivedDate": "2026-10-01T00:00:00", "PalletIdentifier": {"Id": 502}},
    ]})
    c = ExtensivConnector(env={"EXTENSIV_CLIENT_ID": "i", "EXTENSIV_CLIENT_SECRET": "s", "EXTENSIV_USER_LOGIN_ID": "1",
                               "EXTENSIV_CUSTOMER_ID": "9", "EXTENSIV_FACILITY_ID": "3", "EXTENSIV_TPL_KEY": "abc"})
    batch = c.pull(demo_mode=True)
    token_req = [r for r in router.calls if "Token" in str(r.url)][0]
    assert json.loads(token_req.content)["tpl"] == "{abc}" and token_req.headers["Authorization"].startswith("Basic ")
    assert [o.number for o in batch.orders] == ["#100"]  # other customer's order dropped
    assert batch.orders[0].status == "shipped" and batch.orders[0].shipped_at.day == 20
    assert (batch.stock[0].on_hand, batch.stock[0].available, batch.stock[0].allocated) == (14, 12, 2)
    assert {l.lot_number for l in batch.lots} == {"L1", "RI-6"} and batch.shipments[0].tracking_ref == "1Z9"
    acts = {(a.charge_code, str(a.qty)) for a in batch.activities}
    assert {("PICK-ORD", "1"), ("PICK-UNIT", "2"), ("RCV-PLT", "2"), ("STOR-PLT", "2")} <= acts


def test_camel_keys():
    assert camel_keys({"ReadOnly": {"OrderId": 1}, "_embedded": {"Item": []}, "a": [{"Sku": "x"}]}) == \
        {"readOnly": {"orderId": 1}, "_embedded": {"item": []}, "a": [{"sku": "x"}]}
