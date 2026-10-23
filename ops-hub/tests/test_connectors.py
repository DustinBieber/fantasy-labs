import json

import httpx
import pytest

from opshub.connectors import REGISTRY, ai_providers, get_connector, sync_keys
from opshub.connectors.base import Connector
from opshub.connectors.lightspeed import LightspeedConnector
from opshub.connectors.shopify import ShopifyConnector

EXPECTED = {"shopify", "extensiv", "lightspeed", "iqmetrix_rq", "google_sheets", "gmail", "google_drive",
            "google_calendar", "google_chat", "telegram", "whatsapp", "claude", "gemini", "openai", "muse"}


def test_every_requested_system_is_registered():
    assert set(REGISTRY) == EXPECTED
    assert sync_keys()[0] == "google_sheets"  # master data first


def test_modes():
    shopify = get_connector("shopify", env={})
    assert shopify.mode(demo_mode=True) == "demo"
    assert shopify.mode(demo_mode=False) == "disabled"
    live = get_connector("shopify", env={"SHOPIFY_SHOP": "x.myshopify.com", "SHOPIFY_ACCESS_TOKEN": "t"})
    assert live.mode(demo_mode=True) == "live"
    assert get_connector("muse", env={}).mode(True) == "planned"


def test_lightspeed_quirks():
    batch = LightspeedConnector(env={}).normalize(
        shops={"Shop": {"shopID": "1", "name": "Main"}},  # single object, not a list
        items={"Item": [{"itemID": "7", "customSku": "SKU-A", "description": "A", "ItemShops": {"ItemShop": [
            {"shopID": "1", "qoh": "5"}, {"shopID": "0", "qoh": "5"}]}}]},
        sales={"Sale": [
            {"saleID": "1", "shopID": "1", "completed": "true", "voided": "false", "timeStamp": "2026-10-01T10:00:00+00:00",
             "SaleLines": {"SaleLine": {"saleLineID": "11", "itemID": "7", "unitQuantity": "2", "calcTotal": "9.98"}}},
            {"saleID": "2", "shopID": "1", "completed": "true", "voided": "true", "timeStamp": "2026-10-01T11:00:00+00:00",
             "SaleLines": {"SaleLine": {"saleLineID": "12", "itemID": "7", "unitQuantity": "1"}}},
        ]})
    assert [s.location for s in batch.stock] == ["1"]  # shopID 0 (all-shops total) skipped
    assert len(batch.sales) == 1 and batch.sales[0].sku == "SKU-A" and batch.sales[0].qty == 2


def test_shopify_mapping():
    c = ShopifyConnector(env={})
    order = {"id": "gid://shopify/Order/1", "name": "#1", "createdAt": "2026-10-01T00:00:00Z", "cancelledAt": None,
             "displayFulfillmentStatus": "PARTIALLY_FULFILLED", "note": "n", "tags": ["rush"],
             "customAttributes": [{"key": "Need By", "value": "2026-10-05"}], "customer": {"displayName": "C"},
             "lineItems": {"nodes": [{"sku": "A", "name": "A", "quantity": 3, "unfulfilledQuantity": 1}]},
             "fulfillments": [{"createdAt": "2026-10-02T00:00:00Z", "displayStatus": "DELIVERED",
                               "trackingInfo": [{"company": "UPS", "number": "1Z1"}]}]}
    cancelled = {**order, "id": "gid://shopify/Order/2", "name": "#2", "cancelledAt": "2026-10-02T00:00:00Z",
                 "fulfillments": []}
    batch = c.normalize([order, cancelled], [{"id": "gid://shopify/Location/9", "name": "WH", "inventoryLevels": {
        "nodes": [{"item": {"sku": "A"}, "quantities": [{"name": "on_hand", "quantity": 4},
                                                         {"name": "committed", "quantity": 1},
                                                         {"name": "available", "quantity": 3}]}]}}])
    assert [o.status for o in batch.orders] == ["partial", "cancelled"]
    assert batch.orders[0].attributes == {"Need By": "2026-10-05"}
    assert batch.orders[0].lines[0].qty_fulfilled == 2
    assert len(batch.sales) == 1  # cancelled order is not a sale
    assert batch.shipments[0].status == "delivered" and batch.shipments[0].tracking_ref == "1Z1"
    assert (batch.stock[0].on_hand, batch.stock[0].allocated, batch.stock[0].available) == (4, 1, 3)


def test_shopify_live_paginates(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append((request.url.path, request.headers["X-Shopify-Access-Token"], body["variables"]))
        q = body["query"]
        if "orders(" in q:
            first = body["variables"]["cursor"] is None
            return httpx.Response(200, json={"data": {"orders": {
                "pageInfo": {"hasNextPage": first, "endCursor": "c1" if first else None},
                "nodes": [{"id": f"gid://shopify/Order/{1 if first else 2}", "name": "#x", "createdAt": None,
                           "displayFulfillmentStatus": "UNFULFILLED", "lineItems": {"nodes": []}}]}}})
        if "locations(" in q:
            return httpx.Response(200, json={"data": {"locations": {"nodes": [{"id": "gid://shopify/Location/1",
                                                                              "name": "WH"}]}}})
        return httpx.Response(200, json={"data": {"location": {"id": "gid://shopify/Location/1", "name": "WH",
                                                               "inventoryLevels": {"pageInfo": {"hasNextPage": False},
                                                                                   "nodes": []}}}})

    monkeypatch.setattr(ShopifyConnector, "transport", httpx.MockTransport(handler))
    c = ShopifyConnector(env={"SHOPIFY_SHOP": "demo.myshopify.com", "SHOPIFY_ACCESS_TOKEN": "tok"})
    batch = c.pull(demo_mode=False)
    assert [o.external_id for o in batch.orders] == ["gid://shopify/Order/1", "gid://shopify/Order/2"]
    assert calls[1][2]["cursor"] == "c1"
    assert all(path == "/admin/api/2026-07/graphql.json" and tok == "tok" for path, tok, _ in calls)


def test_unbuilt_live_sync_says_so():
    from opshub.connectors.base import NotWiredYet

    c = get_connector("lightspeed", env={"LIGHTSPEED_ACCOUNT_ID": "1", "LIGHTSPEED_ACCESS_TOKEN": "t"})
    with pytest.raises(NotWiredYet):
        c.pull(demo_mode=True)


def _capture(monkeypatch, cls, response_json=None):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_json or {"ok": True})

    monkeypatch.setattr(cls, "transport", httpx.MockTransport(handler))
    return seen


def test_notifiers_send(monkeypatch):
    from opshub.connectors.google import GoogleChatConnector
    from opshub.connectors.messaging import TelegramConnector, WhatsAppConnector

    seen = _capture(monkeypatch, TelegramConnector)
    TelegramConnector(env={"TELEGRAM_BOT_TOKEN": "abc", "TELEGRAM_CHAT_ID": "42"}).send("hi")
    assert seen[0].url.path == "/botabc/sendMessage" and json.loads(seen[0].content)["chat_id"] == "42"

    seen = _capture(monkeypatch, GoogleChatConnector)
    GoogleChatConnector(env={"GOOGLE_CHAT_WEBHOOK_URL": "https://chat.googleapis.com/v1/spaces/x/messages?key=k"}).send("hi")
    assert json.loads(seen[0].content) == {"text": "hi"}

    seen = _capture(monkeypatch, WhatsAppConnector)
    env = {"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "99", "WHATSAPP_TO": "15550001, 15550002"}
    WhatsAppConnector(env=env).send("hi")
    assert [json.loads(r.content)["to"] for r in seen] == ["15550001", "15550002"]
    assert seen[0].headers["Authorization"] == "Bearer t" and json.loads(seen[0].content)["type"] == "text"
    seen.clear()
    WhatsAppConnector(env={**env, "WHATSAPP_TEMPLATE": "ops_alert"}).send("hi")
    assert json.loads(seen[0].content)["template"]["name"] == "ops_alert"


def test_ai_providers_parse_responses(monkeypatch):
    from opshub.connectors.ai import ClaudeProvider, GeminiProvider, OpenAIProvider

    _capture(monkeypatch, ClaudeProvider, {"content": [{"type": "text", "text": '{"a": 1}'}]})
    assert ClaudeProvider(env={"ANTHROPIC_API_KEY": "k"}).complete("s", "p") == '{"a": 1}'
    _capture(monkeypatch, GeminiProvider, {"candidates": [{"content": {"parts": [{"text": '{"b": 2}'}]}}]})
    assert GeminiProvider(env={"GEMINI_API_KEY": "k"}).complete("s", "p") == '{"b": 2}'
    _capture(monkeypatch, OpenAIProvider, {"choices": [{"message": {"content": '{"c": 3}'}}]})
    assert OpenAIProvider(env={"OPENAI_API_KEY": "k"}).complete("s", "p") == '{"c": 3}'


def test_ai_falls_back_to_next_provider(monkeypatch, settings):
    from opshub.connectors.ai import ClaudeProvider, GeminiProvider
    from opshub.services import ai

    monkeypatch.setattr(ClaudeProvider, "transport", httpx.MockTransport(lambda r: httpx.Response(529)))
    _capture(monkeypatch, GeminiProvider,
             {"candidates": [{"content": {"parts": [{"text": '```json\n{"deadline": "2026-11-01"}\n```'}]}}]})
    result, provider = ai.complete_json(settings, "p", env={"ANTHROPIC_API_KEY": "k", "GEMINI_API_KEY": "k"})
    assert provider == "gemini" and result == {"deadline": "2026-11-01"}
    assert ai.complete_json(settings, "p", env={}) == (None, None)
    assert set(ai_providers(env={})) == {"claude", "gemini", "openai"}


def test_base_connector_pulls_flag():
    assert get_connector("shopify", env={}).pulls
    assert not get_connector("telegram", env={}).pulls
    assert isinstance(get_connector("gmail", env={}), Connector) and get_connector("gmail", env={}).pulls
