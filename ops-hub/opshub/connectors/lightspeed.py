"""Lightspeed Retail POS (R-Series): store stock and sales.

Live mode uses the R-Series V3 API with OAuth. Refresh tokens rotate: each refresh
returns a new one and the old one stops working, so the newest is stored in the
database (key ``lightspeed_refresh_token``) and used from then on.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from ..demo import fixtures
from ..timeutil import parse_iso, utcnow
from ._util import as_list, to_dec, to_int, truthy
from .base import Connector, NLocation, NProduct, NSale, NStock, SyncBatch, register
from .files import FileImports

API = "https://api.lightspeedapp.com/API/V3/Account/{account}"
TOKEN_URL = "https://cloud.lightspeedapp.com/auth/oauth/token"
KV_REFRESH = "lightspeed_refresh_token"

_token_cache: dict[str, tuple[str, float]] = {}


def _kv_get(key: str) -> str | None:
    try:
        from ..services import kv

        return kv.get(key)
    except Exception:
        return None


def _kv_set(key: str, value: str) -> None:
    from ..services import kv

    kv.set(key, value)


@register
class LightspeedConnector(FileImports, Connector):
    key = "lightspeed"
    name = "Lightspeed Retail POS"
    category = "pos"
    capabilities = ("inventory", "sales")
    settings_keys = ("LIGHTSPEED_ACCOUNT_ID", "LIGHTSPEED_CLIENT_ID", "LIGHTSPEED_CLIENT_SECRET",
                     "LIGHTSPEED_REFRESH_TOKEN")
    import_folder_key = "LIGHTSPEED_IMPORT_FOLDER_ID"
    import_query_key = "LIGHTSPEED_IMPORT_GMAIL_QUERY"
    docs_url = "https://developers.lightspeedhq.com/retail/"
    status_note = "Live: R-Series V3 Shop, Item (with ItemShops) and Sale (with SaleLines), OAuth with token rotation."

    def missing_settings(self) -> list[str]:
        missing = [k for k in self.settings_keys if not self.env.get(k)]
        if "LIGHTSPEED_REFRESH_TOKEN" in missing and _kv_get(KV_REFRESH):
            missing.remove("LIGHTSPEED_REFRESH_TOKEN")
        return missing

    # --- auth & requests ---------------------------------------------------------------

    def _access_token(self, client) -> str:
        account = self.setting("LIGHTSPEED_ACCOUNT_ID")
        cached = _token_cache.get(account)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        refresh = _kv_get(KV_REFRESH) or self.setting("LIGHTSPEED_REFRESH_TOKEN")
        resp = client.post(self.setting("LIGHTSPEED_TOKEN_URL", TOKEN_URL), data={
            "client_id": self.setting("LIGHTSPEED_CLIENT_ID"), "client_secret": self.setting("LIGHTSPEED_CLIENT_SECRET"),
            "refresh_token": refresh, "grant_type": "refresh_token"})
        resp.raise_for_status()
        body = resp.json()
        if body.get("refresh_token") and body["refresh_token"] != refresh:
            _kv_set(KV_REFRESH, body["refresh_token"])
        _token_cache[account] = (body["access_token"], time.time() + int(body.get("expires_in", 1800)))
        return body["access_token"]

    def _get(self, client, url: str, params: dict | None = None) -> dict:
        for attempt in range(6):
            resp = client.get(url, params=params, headers={"Authorization": f"Bearer {self._access_token(client)}",
                                                           "Accept": "application/json"})
            if resp.status_code == 429 and attempt < 5:  # leaky-bucket rate limit
                time.sleep(min(float(resp.headers.get("Retry-After") or 2 ** attempt), 30))
                continue
            if resp.status_code == 401 and attempt == 0:
                _token_cache.clear()
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()

    def _all(self, client, resource: str, params: dict) -> list[dict]:
        """Every record of a resource, following V3 cursor pagination (@attributes.next)."""
        base = API.format(account=self.setting("LIGHTSPEED_ACCOUNT_ID"))
        url, query, out = f"{base}/{resource}.json", {"limit": 100, **params}, []
        while url:
            body = self._get(client, url, query)
            out += as_list(body.get(resource))
            attrs = body.get("@attributes") or {}
            nxt = attrs.get("next")
            if nxt:
                url, query = nxt, None
            elif "next" not in attrs and attrs.get("count") is not None:  # older offset paging
                offset = int(attrs.get("offset", 0)) + int(attrs.get("limit", 100))
                if offset >= int(attrs["count"]):
                    break
                query = {**(query or {"limit": 100, **params}), "offset": offset}
            else:
                break
        return out

    def test(self) -> str:
        with self.http() as client:
            body = self._get(client, API.format(account=self.setting("LIGHTSPEED_ACCOUNT_ID")) + ".json")
        acct = body.get("Account") or {}
        acct = acct[0] if isinstance(acct, list) else acct
        return f"Connected to account {acct.get('accountID', '?')} ({acct.get('name', '')})"

    def pull_live(self, since: datetime | None) -> SyncBatch:
        since = since or utcnow() - timedelta(days=int(self.setting("LIGHTSPEED_BACKFILL_DAYS", "90")))
        stamp = since.replace(microsecond=0).isoformat() + "+00:00"
        with self.http() as client:
            shops = self._all(client, "Shop", {})
            items = self._all(client, "Item", {"load_relations": '["ItemShops"]', "archived": "false"})
            sales = self._all(client, "Sale", {"load_relations": '["SaleLines"]', "completed": "true",
                                               "timeStamp": f">,{stamp}"})
        return self.normalize({"Shop": shops}, {"Item": items}, {"Sale": sales})

    # --- demo & mapping -----------------------------------------------------------------

    def pull_demo(self) -> SyncBatch:
        now = utcnow()
        return self.normalize(fixtures.lightspeed_shops(), fixtures.lightspeed_items(), fixtures.lightspeed_sales(now))

    def normalize(self, shops: dict, items: dict, sales: dict) -> SyncBatch:
        batch = SyncBatch()
        sku_field = self.setting("LIGHTSPEED_SKU_FIELD", "customSku")
        for shop in as_list(shops.get("Shop")):
            batch.locations.append(NLocation(code=f"LS-{shop['shopID']}", name=shop.get("name") or f"Shop {shop['shopID']}",
                                             kind="store", external_id=str(shop["shopID"])))
        sku_by_item: dict[str, str] = {}
        for item in as_list(items.get("Item")):
            sku = item.get(sku_field) or item.get("customSku") or item.get("systemSku") or item["itemID"]
            sku_by_item[str(item["itemID"])] = sku
            batch.products.append(NProduct(sku=sku, name=item.get("description") or sku, upc=item.get("upc") or None,
                                           external_id=str(item["itemID"])))
            for s in as_list((item.get("ItemShops") or {}).get("ItemShop")):
                if str(s.get("shopID")) == "0":  # all-shops total row
                    continue
                qoh = to_int(s.get("qoh"))
                batch.stock.append(NStock(sku=sku, location=str(s["shopID"]), on_hand=qoh, available=qoh))
        for sale in as_list(sales.get("Sale")):
            if not truthy(sale.get("completed")) or truthy(sale.get("voided")):
                continue
            sold_at = parse_iso(sale.get("timeStamp"))
            for line in as_list((sale.get("SaleLines") or {}).get("SaleLine")):
                item_id = str(line.get("itemID"))
                if item_id in ("", "0", "None"):  # non-inventory lines (e.g. misc charges)
                    continue
                batch.sales.append(NSale(external_id=str(line["saleLineID"]), sku=sku_by_item.get(item_id, item_id),
                                         location=str(sale["shopID"]), sold_at=sold_at,
                                         qty=to_int(line.get("unitQuantity")), net_amount=to_dec(line.get("calcTotal"))))
        return batch
