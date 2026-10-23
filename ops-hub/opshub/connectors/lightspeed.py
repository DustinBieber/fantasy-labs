"""Lightspeed Retail POS: store stock and sales.

Mapped to the R-Series API (api.lightspeedapp.com/API/V3). If you are on
X-Series (formerly Vend) the normalizer needs an X-Series variant.
"""
from __future__ import annotations

from datetime import datetime

from ..demo import fixtures
from ..timeutil import parse_iso, utcnow
from ._util import as_list, to_dec, to_int, truthy
from .base import Connector, NLocation, NProduct, NSale, NStock, SyncBatch, register


@register
class LightspeedConnector(Connector):
    key = "lightspeed"
    name = "Lightspeed Retail POS"
    category = "pos"
    capabilities = ("inventory", "sales")
    settings_keys = ("LIGHTSPEED_ACCOUNT_ID", "LIGHTSPEED_ACCESS_TOKEN")
    docs_url = "https://developers.lightspeedhq.com/retail/"
    status_note = ("Mapping built for R-Series Shop, Item (ItemShops) and Sale (SaleLines). Live calls and "
                   "OAuth token refresh still to do. Confirm R-Series vs X-Series.")

    def pull_demo(self) -> SyncBatch:
        now = utcnow()
        return self.normalize(fixtures.lightspeed_shops(), fixtures.lightspeed_items(), fixtures.lightspeed_sales(now))

    def normalize(self, shops: dict, items: dict, sales: dict) -> SyncBatch:
        batch = SyncBatch()
        for shop in as_list(shops.get("Shop")):
            batch.locations.append(NLocation(code=f"LS-{shop['shopID']}", name=shop.get("name") or f"Shop {shop['shopID']}",
                                             kind="store", external_id=str(shop["shopID"])))
        sku_by_item: dict[str, str] = {}
        for item in as_list(items.get("Item")):
            sku = item.get("customSku") or item.get("systemSku") or item["itemID"]
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

    def pull_live(self, since: datetime | None) -> SyncBatch:
        # GET https://api.lightspeedapp.com/API/V3/Account/{id}/Shop.json
        # GET .../Item.json?load_relations=["ItemShops"]   (paginate with the "next" URL)
        # GET .../Sale.json?load_relations=["SaleLines"]&timeStamp=>,<since>
        return super().pull_live(since)
