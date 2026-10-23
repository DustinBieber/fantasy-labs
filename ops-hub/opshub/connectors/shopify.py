"""Shopify: online orders, notes and custom attributes, warehouse stock, parcel tracking.

Live mode uses the GraphQL Admin API with a custom-app access token
(Settings > Apps > Develop apps; scopes read_orders, read_inventory,
read_locations, read_fulfillments).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..demo import fixtures
from ..timeutil import parse_iso, utcnow
from .base import Connector, NOrder, NOrderLine, NSale, NShipment, NStock, SyncBatch, register
from .files import FileImports

ORDERS_QUERY = """
query Orders($cursor: String, $query: String) {
  orders(first: 100, after: $cursor, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt updatedAt cancelledAt
      displayFulfillmentStatus displayFinancialStatus
      note tags
      customAttributes { key value }
      customer { displayName }
      lineItems(first: 100) { nodes { sku name quantity unfulfilledQuantity } }
      fulfillments(first: 10) { createdAt status displayStatus trackingInfo(first: 5) { company number url } }
    }
  }
}
"""

LOCATIONS_QUERY = """
query Locations { locations(first: 50) { nodes { id name } } }
"""

LEVELS_QUERY = """
query Levels($id: ID!, $cursor: String) {
  location(id: $id) {
    id name
    inventoryLevels(first: 250, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { quantities(names: ["on_hand", "committed", "available"]) { name quantity } item { sku } }
    }
  }
}
"""

STATUS_MAP = {
    "UNFULFILLED": "open", "OPEN": "open", "PENDING_FULFILLMENT": "open", "SCHEDULED": "open",
    "IN_PROGRESS": "processing", "PARTIALLY_FULFILLED": "partial", "ON_HOLD": "on_hold",
    "FULFILLED": "shipped", "RESTOCKED": "cancelled",
}
FULFILLMENT_STATUS_MAP = {"DELIVERED": "delivered", "OUT_FOR_DELIVERY": "out_for_delivery",
                          "IN_TRANSIT": "in_transit", "FAILURE": "exception", "ATTEMPTED_DELIVERY": "exception"}


@register
class ShopifyConnector(FileImports, Connector):
    key = "shopify"
    name = "Shopify"
    category = "commerce"
    capabilities = ("orders", "inventory", "sales", "shipments")
    settings_keys = ("SHOPIFY_SHOP", "SHOPIFY_ACCESS_TOKEN")
    import_folder_key = "SHOPIFY_IMPORT_FOLDER_ID"
    import_query_key = "SHOPIFY_IMPORT_GMAIL_QUERY"
    docs_url = "https://shopify.dev/docs/api/admin-graphql"
    status_note = "Live sync built (GraphQL Admin API); not yet run against your store."

    # --- live ---------------------------------------------------------------------

    def _graphql(self, client, query: str, variables: dict) -> dict:
        version = self.setting("SHOPIFY_API_VERSION", "2026-07")
        url = f"https://{self.setting('SHOPIFY_SHOP')}/admin/api/{version}/graphql.json"
        resp = client.post(url, json={"query": query, "variables": variables},
                           headers={"X-Shopify-Access-Token": self.setting("SHOPIFY_ACCESS_TOKEN")})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]

    def test(self) -> str:
        with self.http() as client:
            shop = self._graphql(client, "{ shop { name myshopifyDomain } }", {})["shop"]
        return f"Connected to {shop['name']} ({shop['myshopifyDomain']})"

    def pull_live(self, since: datetime | None) -> SyncBatch:
        since = since or utcnow() - timedelta(days=int(self.setting("SHOPIFY_BACKFILL_DAYS", "90")))
        orders: list[dict] = []
        locations: list[dict] = []
        with self.http() as client:
            cursor = None
            query = f"updated_at:>='{since.replace(microsecond=0).isoformat()}Z'"
            while True:
                page = self._graphql(client, ORDERS_QUERY, {"cursor": cursor, "query": query})["orders"]
                orders += page["nodes"]
                if not page["pageInfo"]["hasNextPage"]:
                    break
                cursor = page["pageInfo"]["endCursor"]
            for loc in self._graphql(client, LOCATIONS_QUERY, {})["locations"]["nodes"]:
                levels, cursor = [], None
                while True:
                    data = self._graphql(client, LEVELS_QUERY, {"id": loc["id"], "cursor": cursor})["location"]
                    page = data["inventoryLevels"]
                    levels += page["nodes"]
                    if not page["pageInfo"]["hasNextPage"]:
                        break
                    cursor = page["pageInfo"]["endCursor"]
                locations.append({"id": loc["id"], "name": loc["name"], "inventoryLevels": {"nodes": levels}})
        return self.normalize(orders, locations)

    # --- demo ------------------------------------------------------------------------

    def pull_demo(self) -> SyncBatch:
        now = utcnow()
        return self.normalize(fixtures.shopify_orders(now), fixtures.shopify_inventory(now))

    # --- mapping -----------------------------------------------------------------------

    def normalize(self, orders: list[dict], locations: list[dict]) -> SyncBatch:
        batch = SyncBatch()
        # Shopify doesn't say which location an order shipped from without extra queries;
        # sales are booked to SHOPIFY_SALES_LOCATION_ID, else the first location.
        sales_location = self.setting("SHOPIFY_SALES_LOCATION_ID") or (locations[0]["id"] if locations else "online")
        for o in orders:
            status = "cancelled" if o.get("cancelledAt") else STATUS_MAP.get(o.get("displayFulfillmentStatus"), "open")
            placed = parse_iso(o.get("createdAt"))
            lines = [NOrderLine(sku=li.get("sku") or None, title=li.get("name"), qty=int(li.get("quantity") or 0),
                                qty_fulfilled=int(li.get("quantity") or 0) - int(li.get("unfulfilledQuantity") or 0))
                     for li in o.get("lineItems", {}).get("nodes", [])]
            batch.orders.append(NOrder(
                external_id=o["id"], number=o["name"], status=status, raw_status=o.get("displayFulfillmentStatus"),
                channel="online", customer_name=(o.get("customer") or {}).get("displayName"),
                placed_at=placed, updated_at=parse_iso(o.get("updatedAt")), notes=o.get("note"),
                tags=list(o.get("tags") or []),
                attributes={a["key"]: a["value"] for a in o.get("customAttributes") or [] if a.get("key")},
                lines=lines, ship_from=sales_location,
            ))
            if status != "cancelled" and placed:
                for i, line in enumerate(lines):
                    if line.sku and line.qty:
                        batch.sales.append(NSale(external_id=f"{o['id']}:{i}", sku=line.sku, location=sales_location,
                                                 sold_at=placed, qty=line.qty))
            for f in o.get("fulfillments") or []:
                for t in f.get("trackingInfo") or []:
                    if not t.get("number"):
                        continue
                    batch.shipments.append(NShipment(
                        reference=f"SHOP-{o['name'].lstrip('#')}-{t['number'][-6:]}", direction="outbound",
                        mode="parcel", carrier_name=t.get("company") or "Unknown carrier", tracking_ref=t["number"],
                        status=FULFILLMENT_STATUS_MAP.get(f.get("displayStatus") or "", "in_transit"),
                        destination=(o.get("customer") or {}).get("displayName"), order_number=o["name"],
                        booked_at=parse_iso(f.get("createdAt")), origin="Shopify fulfillment",
                    ))
        for loc in locations:
            for lvl in loc.get("inventoryLevels", {}).get("nodes", []):
                sku = (lvl.get("item") or {}).get("sku")
                if not sku:
                    continue
                q = {x["name"]: int(x["quantity"]) for x in lvl.get("quantities") or []}
                batch.stock.append(NStock(sku=sku, location=loc["id"], on_hand=q.get("on_hand", 0),
                                          allocated=q.get("committed", 0), available=q.get("available")))
        return batch
