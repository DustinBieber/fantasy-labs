"""Setup page (saved settings, tests, OAuth), Google auto-setup, export-file imports and the file-to-API switch."""
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from opshub import db, settings_store
from opshub.connectors import get_connector, google_auth
from opshub.connectors.base import Connector
from opshub.connectors.files import parse_export, status_from
from opshub.models import InventoryLot, Order, SaleLine
from opshub.services import ingest

SHOPIFY_CSV = (
    "Name,Email,Financial Status,Fulfillment Status,Created at,Lineitem quantity,Lineitem name,Lineitem price,"
    "Lineitem sku,Lineitem fulfillment status,Billing Name,Shipping Name,Notes,Note Attributes,Cancelled at,Tags,Total\n"
    "#3001,a@b.c,paid,unfulfilled,2026-10-20 14:03:11 -1000,2,Cold Brew 32,14.99,CBC-32,pending,Bill A,Ship A,"
    "\"Need by Oct 25, gift wrap\",\"Delivery Date: 2026-10-26\",,\"priority, gift\",44.97\n"
    "#3001,,,,,1,Mug,14.99,MUG-TRV,pending,,,,,,,\n"
    "#3002,c@d.e,paid,fulfilled,2026-10-19 09:00:00 -1000,1,Charger,29.99,CHG-65W,fulfilled,Bill B,Ship B,,,,,29.99\n"
    "#3003,e@f.g,refunded,unfulfilled,2026-10-18 09:00:00 -1000,1,Case,19.99,CASE-UNI,,Bill C,Ship C,,,"
    "2026-10-18 10:00:00 -1000,,19.99\n")


@pytest.fixture
def client(settings, monkeypatch):
    for k in ("OPSHUB_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SHOPIFY_SHOP", "GOOGLE_OAUTH_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    db.init_engine("sqlite://")
    settings_store.invalidate()
    google_auth.clear_cache()
    with TestClient(create_app()) as c:
        yield c
    settings_store.invalidate()
    db.Base.metadata.drop_all(db.get_engine())


def create_app():
    from opshub.api import create_app as _c

    return _c()


# --- settings ------------------------------------------------------------------------------------

def test_saved_settings_feed_connectors_and_env_wins(client, monkeypatch):
    r = client.post("/api/setup", json={"values": {"SHOPIFY_SHOP": "jocor.myshopify.com",
                                                   "SHOPIFY_ACCESS_TOKEN": "shpat_secret123", "NOT_A_SETTING": "x"}})
    assert r.json()["saved"] == ["SHOPIFY_ACCESS_TOKEN", "SHOPIFY_SHOP"]
    assert get_connector("shopify").is_configured()
    state = client.get("/api/setup").json()
    shop = next(s for s in state["sections"] if s["key"] == "shopify")
    token = next(f for f in shop["fields"] if f["key"] == "SHOPIFY_ACCESS_TOKEN")
    assert token["value"] == "••••t123" and token["source"] == "saved"  # never sent back in clear
    # Posting the masked value back keeps the secret.
    client.post("/api/setup", json={"values": {"SHOPIFY_ACCESS_TOKEN": "••••t123"}})
    assert settings_store.saved()["SHOPIFY_ACCESS_TOKEN"] == "shpat_secret123"
    monkeypatch.setenv("SHOPIFY_SHOP", "fromenv.myshopify.com")
    assert client.post("/api/setup", json={"values": {"SHOPIFY_SHOP": "other"}}).json()["kept_from_env"] == ["SHOPIFY_SHOP"]
    assert get_connector("shopify").setting("SHOPIFY_SHOP") == "fromenv.myshopify.com"


def test_connection_test_endpoint(client, monkeypatch):
    assert not client.post("/api/setup/test/telegram").json()["ok"]  # missing settings
    sent = []

    def handler(req):
        if req.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"result": {"username": "jocor_ops_bot"}})
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(Connector, "transport", httpx.MockTransport(handler))
    client.post("/api/setup", json={"values": {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-1001"}})
    r = client.post("/api/setup/test/telegram").json()
    assert r["ok"] and "@jocor_ops_bot" in r["message"] and sent[0]["chat_id"] == "-1001"
    monkeypatch.setattr(Connector, "transport", httpx.MockTransport(lambda req: httpx.Response(401, text="Unauthorized")))
    bad = client.post("/api/setup/test/telegram").json()
    assert not bad["ok"] and bad["message"].startswith("401")


def test_google_web_sign_in(client, monkeypatch):
    client.post("/api/setup", json={"values": {"GOOGLE_OAUTH_CLIENT_ID": "cid.apps", "GOOGLE_OAUTH_CLIENT_SECRET": "sec"}})
    start = client.get("/setup/google/start", follow_redirects=False)
    q = parse_qs(urlparse(start.headers["location"]).query)
    assert q["client_id"] == ["cid.apps"] and q["redirect_uri"] == ["http://testserver/setup/google/callback"]
    assert "https://www.googleapis.com/auth/calendar" in q["scope"][0]
    monkeypatch.setattr(google_auth, "exchange_code", lambda *a: {"refresh_token": "rt-xyz", "access_token": "a"})
    assert "expired" in client.get("/setup/google/callback", params={"code": "c", "state": "wrong"},
                                   follow_redirects=False).headers["location"]
    done = client.get("/setup/google/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert "Google%20connected" in done.headers["location"]
    assert google_auth.method(settings_store.effective_env()) == "oauth"


def test_google_auto_setup_creates_and_saves(client, monkeypatch):
    calls = []
    made = {"n": 0}

    def handler(req: httpx.Request):
        calls.append((req.method, str(req.url)))
        url = str(req.url)
        if url.startswith(google_auth.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if "/drive/v3/about" in url:
            return httpx.Response(200, json={"user": {"emailAddress": "dustin@jocordistro.com"}})
        if req.method == "GET" and "/drive/v3/files" in url and "q=" in url:
            return httpx.Response(200, json={"files": []})
        if req.method == "GET" and "/drive/v3/files/" in url:
            return httpx.Response(200, json={"parents": ["root"]})
        if req.method in ("POST", "PATCH") and "/drive/v3/files" in url:
            made["n"] += 1
            return httpx.Response(200, json={"id": f"folder{made['n']}"})
        if req.method == "POST" and url.endswith("/v4/spreadsheets"):
            made["n"] += 1
            return httpx.Response(200, json={"spreadsheetId": f"sheet{made['n']}"})
        if "values:batchUpdate" in url:
            return httpx.Response(200, json={})
        if "calendarList" in url:
            return httpx.Response(200, json={"items": []})
        if url.endswith("/calendar/v3/calendars"):
            return httpx.Response(200, json={"id": "cal@group.calendar.google.com"})
        return httpx.Response(404)

    monkeypatch.setattr(Connector, "transport", httpx.MockTransport(handler))
    client.post("/api/setup", json={"values": {"GOOGLE_OAUTH_CLIENT_ID": "c", "GOOGLE_OAUTH_CLIENT_SECRET": "s"}})
    google_auth.store_refresh_token("rt")
    r = client.post("/api/setup/google/init").json()
    assert r["account"] == "dustin@jocordistro.com"
    saved = settings_store.saved()
    for key in ("GOOGLE_DRIVE_FOLDER_ID", "SHOPIFY_IMPORT_FOLDER_ID", "RQ_DRIVE_FOLDER_ID", "MUSE_DRIVE_OUTBOX_FOLDER_ID",
                "GOOGLE_SHEETS_MASTER_ID", "GOOGLE_SHEETS_REPORT_ID"):
        assert saved.get(key), key
    assert saved["GOOGLE_CALENDAR_ID"] == "cal@group.calendar.google.com"
    assert get_connector("iqmetrix_rq").mode(True) == "files"  # Google + folder = export-file mode
    assert get_connector("muse").mode(True) == "live"


# --- export files --------------------------------------------------------------------------------------

def test_shopify_order_export(settings):
    from opshub.services.files import read_table

    kind, b = parse_export(read_table("orders_export.csv", SHOPIFY_CSV), source="shopify", tz="Pacific/Honolulu")
    assert kind == "orders"
    by = {o.number: o for o in b.orders}
    o = by["#3001"]
    assert o.status == "open" and o.customer_name == "Ship A" and o.tags == ["priority", "gift"]
    assert o.attributes == {"Delivery Date": "2026-10-26"} and [l.sku for l in o.lines] == ["CBC-32", "MUG-TRV"]
    assert o.placed_at.isoformat() == "2026-10-21T00:03:11"  # -1000 offset honoured
    assert by["#3002"].status == "shipped" and by["#3003"].status == "cancelled"
    assert {s.external_id.split(":")[1] for s in b.sales} == {"#3001", "#3002"}  # cancelled order isn't a sale


def test_extensiv_lot_export_uses_facility_not_bin():
    rows = [["Facility", "SKU", "Description", "Lot Number", "Expiration Date", "Received Date", "Location", "On Hand",
             "Available"],
            ["Riverside", "CBC-32", "Cold brew", "L1", "12/31/2026", "10/01/2026", "A-01-02", "30", "28"],
            ["Riverside", "CBC-32", "Cold brew", "L1", "12/31/2026", "10/01/2026", "A-01-03", "6", "6"],
            ["Riverside", "BAR-ENR", "Bars", "L9", "2027-03-01", "2026-09-01", "B-02-01", "12", "12"]]
    kind, b = parse_export(rows, source="extensiv", tz="Pacific/Honolulu")
    assert kind == "lots" and {l.location for l in b.lots} == {"Riverside"}
    l1 = next(l for l in b.lots if l.lot_number == "L1")
    assert l1.qty_on_hand == 36 and l1.best_by.isoformat() == "2026-12-31"
    cbc = next(s for s in b.stock if s.sku == "CBC-32")
    assert (cbc.on_hand, cbc.available, cbc.allocated) == (36, 34, 2)


def test_lightspeed_sales_export():
    rows = [["Date", "Sale ID", "Shop", "Custom SKU", "Description", "Qty", "Subtotal"],
            ["10/22/2026 3:45 PM", "901", "Downtown Flagship", "CBC-32", "Cold brew", "2", "29.98"],
            ["10/22/2026 4:00 PM", "902", "Downtown Flagship", "MUG-TRV", "Mug", "1", "21.99"]]
    kind, b = parse_export(rows, source="lightspeed", tz="Pacific/Honolulu")
    assert kind == "sales" and [s.qty for s in b.sales] == [2, 1] and b.sales[0].location == "Downtown Flagship"


def test_status_words():
    assert [status_from(x) for x in ("Unfulfilled", "fulfilled", "Partially fulfilled", "Complete", "On Hold",
                                     "Allocated", "Picked")] == \
        ["open", "shipped", "partial", "shipped", "on_hold", "allocated", "processing"]


def test_upload_then_api_takes_over_without_duplicates(synced, settings):
    """Run Shopify on exports today, connect the API tomorrow: orders are adopted, sales not double counted."""
    from opshub.connectors.base import NOrder, NOrderLine, NSale, SyncBatch

    shop = get_connector("shopify", env={})
    batch, _ = shop.import_files([("orders.csv", SHOPIFY_CSV)], settings.timezone)
    ingest.apply_batch(synced, batch, "shopify", settings)
    synced.flush()
    file_order = synced.scalar(select(Order).where(Order.number == "#3001"))
    assert file_order.external_id == "file:#3001"
    file_sales = synced.scalar(select(func.count()).select_from(SaleLine).where(SaleLine.external_id.like("file:#3001:%")))
    assert file_sales == 2
    api = SyncBatch(orders=[NOrder(external_id="gid://shopify/Order/9001", number="#3001", status="shipped",
                                   lines=[NOrderLine(sku="CBC-32", qty=2)])],
                    sales=[NSale(external_id="gid://shopify/Order/9001:0", sku="CBC-32", location="online",
                                 sold_at=file_order.placed_at, qty=2)])
    stats = ingest.apply_batch(synced, api, "shopify", settings)
    synced.flush()
    assert stats["orders_adopted_from_files"] == 1
    assert synced.scalar(select(func.count()).select_from(Order).where(Order.number == "#3001")) == 1
    assert synced.get(Order, file_order.id).external_id == "gid://shopify/Order/9001"
    assert synced.scalar(select(func.count()).select_from(SaleLine).where(SaleLine.external_id.like("file:#3001:%"))) == 0


def test_import_endpoint_any_source(client):
    client.post("/api/sync")
    r = client.post("/api/import/shopify?filename=orders_export.csv", content=SHOPIFY_CSV.encode()).json()
    assert r["imported"] and r["files"][0]["kind"] == "orders" and r["files"][0]["orders"] == 3
    watch = client.get("/api/orders?view=watch").json()
    o = next(x for x in watch if x["number"] == "#3001")
    assert o["order_type"] == "priority" and o["required_by_source"] in ("note", "attribute")
    lots_csv = "Facility,SKU,Lot Number,Expiration Date,On Hand\n1,MTE-50,L2510,2026-11-17,10\n"
    assert client.post("/api/import/extensiv?filename=lots.csv", content=lots_csv.encode()).json()["imported"]
    assert client.post("/api/import/telegram?filename=x.csv", content=b"a").status_code == 400
    with db.session_scope() as s:
        lot = s.scalar(select(InventoryLot).where(InventoryLot.lot_number == "L2510"))
        assert lot.qty_on_hand == 10
