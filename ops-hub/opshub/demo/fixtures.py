"""Sample payloads in each vendor's own response shape, dated relative to now.

Connectors in demo mode feed these through the same normalizers they use for
live API responses, so the demo exercises the real mapping code. The business
is fictional: a brand selling online (Shopify), in its own stores (Lightspeed)
and through wireless-retail partner stores (RQ), with overflow stock at a
partner 3PL (Extensiv).
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

HQ_GID = "gid://shopify/Location/1001"
EXTENSIV_FACILITY = "1"
LS_SHOPS = {"1": "Downtown Flagship", "2": "Market Street"}
RQ_STORES = {"101": "Wireless Partner - Eastgate", "102": "Wireless Partner - Westfield"}
VENDOR_3PL = "Riverside 3PL"

# sku, name, upc, category, hazmat, hazmat class, UN number, seasonal, season, shelf life days, unit cost
PRODUCTS = [
    ("CBC-32", "Cold Brew Concentrate 32 oz", "850001000011", "Beverage", "", "", "", "", "", "180", "6.40"),
    ("CBC-12PK", "Cold Brew Cans 12-pack", "850001000028", "Beverage", "", "", "", "", "", "270", "11.20"),
    ("MTE-50", "Matcha Tea Sticks 50 ct", "850001000035", "Beverage", "", "", "", "", "", "365", "8.10"),
    ("BAR-ENR", "Energy Bar Box 12 ct", "850001000042", "Snack", "", "", "", "", "", "300", "9.75"),
    ("PMK-SYR", "Pumpkin Spice Syrup 750 ml", "850001000059", "Beverage", "", "", "", "yes", "Fall 2026", "240", "4.30"),
    ("HOL-GBX", "Holiday Gift Box", "850001000066", "Gift", "", "", "", "yes", "Holiday 2026", "120", "18.50"),
    ("PWR-20K", "Power Bank 20,000 mAh", "850001000073", "Electronics", "yes", "9", "UN3480", "", "", "", "14.00"),
    ("AER-CLN", "Screen Cleaner Aerosol 6 oz", "850001000080", "Accessories", "yes", "2.1", "UN1950", "", "", "", "2.10"),
    ("CHG-65W", "65W USB-C Charger", "850001000097", "Electronics", "", "", "", "", "", "", "9.60"),
    ("CASE-UNI", "Universal Phone Case", "850001000103", "Accessories", "", "", "", "", "", "", "3.20"),
    ("MUG-TRV", "Travel Mug 16 oz", "850001000110", "Merch", "", "", "", "", "", "", "5.50"),
]
PRICE = {"CBC-32": 14.99, "CBC-12PK": 29.99, "MTE-50": 24.99, "BAR-ENR": 27.99, "PMK-SYR": 12.99,
         "HOL-GBX": 59.99, "PWR-20K": 39.99, "AER-CLN": 7.99, "CHG-65W": 29.99, "CASE-UNI": 19.99,
         "MUG-TRV": 21.99}
NAME = {p[0]: p[1] for p in PRODUCTS}

# Average units sold per day, by location and SKU. Seasonal items only sell for part of the window.
DAILY_RATE = {
    HQ_GID: {"CBC-32": 3.0, "CBC-12PK": 1.5, "MTE-50": 1.2, "BAR-ENR": 1.0, "PMK-SYR": 0.8,
             "HOL-GBX": 0.6, "PWR-20K": 0.6, "CHG-65W": 0.5, "CASE-UNI": 0.4, "MUG-TRV": 0.7, "AER-CLN": 0.2},
    "1": {"CBC-32": 2.0, "CBC-12PK": 1.0, "MTE-50": 0.8, "BAR-ENR": 1.2, "PMK-SYR": 0.6, "HOL-GBX": 0.3,
          "MUG-TRV": 0.5, "CASE-UNI": 0.3},
    "2": {"CBC-32": 1.2, "CBC-12PK": 0.8, "MTE-50": 0.5, "BAR-ENR": 0.9, "PMK-SYR": 0.4, "HOL-GBX": 0.2,
          "MUG-TRV": 0.3, "CASE-UNI": 0.2},
    "101": {"PWR-20K": 1.0, "CHG-65W": 1.2, "CASE-UNI": 1.5, "AER-CLN": 0.6},
    "102": {"PWR-20K": 0.7, "CHG-65W": 0.9, "CASE-UNI": 1.1, "AER-CLN": 0.4},
}
SELLING_DAYS = {"PMK-SYR": 50, "HOL-GBX": 20}  # days back the seasonal item went on sale
HISTORY_DAYS = 90

ON_HAND = {
    HQ_GID: {"CBC-32": 140, "CBC-12PK": 60, "MTE-50": 85, "BAR-ENR": 30, "PMK-SYR": 220, "HOL-GBX": 40,
             "PWR-20K": 6, "CHG-65W": 45, "CASE-UNI": 50, "MUG-TRV": 70, "AER-CLN": 25},
    "1": {"CBC-32": 24, "CBC-12PK": 10, "MTE-50": 8, "BAR-ENR": 3, "PMK-SYR": 30, "HOL-GBX": 15,
          "MUG-TRV": 12, "CASE-UNI": 9},
    "2": {"CBC-32": 18, "CBC-12PK": 9, "MTE-50": 6, "BAR-ENR": 14, "PMK-SYR": 22, "HOL-GBX": 10,
          "MUG-TRV": 8, "CASE-UNI": 7},
    "101": {"PWR-20K": 4, "CHG-65W": 30, "CASE-UNI": 40, "AER-CLN": 12},
    "102": {"PWR-20K": 15, "CHG-65W": 22, "CASE-UNI": 35, "AER-CLN": 10},
}

# Partner 3PL lots: sku, lot, received days ago, best-by in days (None = not dated), qty, allocated
LOTS_3PL = [
    ("CBC-32", "L2604A", 140, 40, 30, 0),
    ("CBC-32", "L2607B", 60, 120, 36, 0),
    ("CBC-32", "L2609C", 10, 170, 60, 0),
    ("BAR-ENR", "L2602", 230, 70, 50, 0),
    ("BAR-ENR", "L2609", 100, 200, 10, 0),
    ("CBC-12PK", "L2605", 120, 150, 120, 0),
    ("CBC-12PK", "L2608", 30, 240, 80, 0),
    ("MTE-50", "L2510", 340, 25, 150, 0),
    ("PMK-SYR", "L2607", 40, 200, 300, 0),
    ("HOL-GBX", "L2609H", 15, 110, 400, 24),
    ("PWR-20K", "PB-2408", 60, None, 240, 2),
    ("AER-CLN", "AC-2603", 45, None, 96, 0),
]


def _z(dt: datetime | None) -> str | None:
    return None if dt is None else dt.replace(microsecond=0).isoformat() + "Z"


def _md(d: datetime | date) -> str:
    return f"{d:%b} {d.day}"


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _daily_sales(now: datetime, location: str, seed: int) -> list[tuple[int, str, int]]:
    """(days ago, sku, qty) for every day in the history window with a sale."""
    rng = random.Random(seed)
    out = []
    for days_ago in range(HISTORY_DAYS, 0, -1):
        for sku, rate in DAILY_RATE[location].items():
            if days_ago > SELLING_DAYS.get(sku, HISTORY_DAYS):
                continue
            qty = _poisson(rng, rate)
            if qty:
                out.append((days_ago, sku, qty))
    return out


# --- Google Sheets: master data the ops team maintains ---------------------------

def sheets_master(now: datetime) -> dict[str, list[list[str]]]:
    """Values for each tab, as the Sheets API ``values`` arrays (header row first)."""
    d = lambda days: (now + timedelta(days=days)).date().isoformat()  # noqa: E731
    t = lambda days: _z(now + timedelta(days=days))  # noqa: E731
    return {
        "Products": [["sku", "name", "upc", "category", "hazmat", "hazmat_class", "un_number", "seasonal",
                      "season", "shelf_life_days", "unit_cost"], *[list(p) for p in PRODUCTS]],
        "Locations": [
            ["code", "name", "kind", "source", "external_id", "address"],
            ["HQ-WH", "HQ Warehouse", "warehouse", "shopify", HQ_GID, "1200 Commerce Way"],
            ["P3PL-01", "Riverside 3PL (overflow)", "partner_3pl", "extensiv", EXTENSIV_FACILITY, "88 Riverside Dr"],
            ["STR-DTN", "Downtown Flagship", "store", "lightspeed", "1", "14 Main St"],
            ["STR-MKT", "Market Street", "store", "lightspeed", "2", "301 Market St"],
            ["RQ-101", "Wireless Partner - Eastgate", "store", "iqmetrix_rq", "101", "Eastgate Mall"],
            ["RQ-102", "Wireless Partner - Westfield", "store", "iqmetrix_rq", "102", "Westfield Center"],
        ],
        "Carriers": [
            ["name", "aliases", "hazmat_approved", "tracking_url_template", "contact"],
            ["UPS", "United Parcel Service", "yes", "https://www.ups.com/track?tracknum={ref}", ""],
            ["FedEx", "Federal Express", "yes", "https://www.fedex.com/fedextrack/?trknbr={ref}", ""],
            ["Pacific Star Line", "PSL", "yes", "", "bookings@pacificstar.example"],
            ["BlueWave Forwarding", "BlueWave", "yes", "", "ops@bluewave.example"],
            ["Harbor Drayage Co.", "Harbor Drayage", "yes", "", "dispatch@harbordrayage.example / +1 555 0142"],
            ["QuickHaul Logistics", "QuickHaul; QHL", "no", "", "+1 555 0177 (WhatsApp)"],
            ["Central Plains Freight", "CPF", "yes", "", "track@cpf.example"],
            ["Ridgeway Trucking", "Ridgeway", "no", "", "Dale (dispatcher) +1 555 0190"],
            ["Tri-State Express", "TSX; Tri State", "no", "", "office@tristate.example"],
        ],
        "Shipments": [
            ["reference", "direction", "mode", "carrier", "agent", "tracking_ref", "origin", "destination",
             "destination_location", "order_number", "eta", "hazmat", "un_numbers", "priority", "status",
             "booked_at", "notes"],
            ["IN-PO-7781", "inbound", "ocean", "Pacific Star Line", "BlueWave Forwarding", "MSCU7781234",
             "Ningbo, CN", "Riverside 3PL", "P3PL-01", "", t(4), "yes", "UN3480", "", "in_transit",
             t(-24), "PO 7781: 240 cartons power banks + chargers"],
            ["OUT-WH-88213", "outbound", "ltl", "QuickHaul Logistics", "", "4471983", "Riverside 3PL",
             "Corner Market Co-op, Springfield", "", "WH-PO-88213", t(2), "", "", "", "in_transit", t(-3),
             "6 pallets wholesale"],
            ["TR-HQ-0921", "transfer", "ftl", "Ridgeway Trucking", "", "RW-30017", "HQ Warehouse",
             "Riverside 3PL", "P3PL-01", "", t(-1), "", "", "", "in_transit", t(-7),
             "Overflow move: 18 pallets seasonal"],
            ["IN-PO-7790", "inbound", "ltl", "Tri-State Express", "", "TSX-88120", "Supplier - Columbus, OH",
             "HQ Warehouse", "HQ-WH", "", t(3), "yes", "", "", "booked", t(-1),
             "Aerosol screen cleaner restock"],
        ],
        "RateCard": [
            ["vendor", "charge_code", "description", "unit", "rate", "min_charge", "requires_approval",
             "effective_from", "effective_to"],
            [VENDOR_3PL, "STOR-PLT", "Pallet storage", "per pallet / month", "18.00", "", "", "2026-01-01", ""],
            [VENDOR_3PL, "RCV-PLT", "Receiving", "per pallet", "8.50", "", "", "2026-01-01", ""],
            [VENDOR_3PL, "PICK-ORD", "Order pick & pack", "per order", "2.75", "", "", "2026-01-01", ""],
            [VENDOR_3PL, "PICK-UNIT", "Additional unit", "per unit", "0.35", "", "", "2026-01-01", ""],
            [VENDOR_3PL, "HAZ-HNDL", "Hazmat handling", "per order", "5.00", "", "", "2026-01-01", ""],
            [VENDOR_3PL, "LABEL", "Retail labeling", "per unit", "0.25", "", "", "2026-01-01", ""],
            [VENDOR_3PL, "LABOR-HR", "Special project labor", "per hour", "42.00", "", "yes", "2026-01-01", ""],
        ],
        "StandingOrders": [
            ["code", "customer_name", "cadence_days", "next_due", "min_days_to_best_by", "ship_from", "lines",
             "notes", "active"],
            ["SO-GVG", "Green Valley Grocers", "14", d(5), "90", "P3PL-01", "CBC-32:48; BAR-ENR:24",
             "Standing biweekly. Rejects anything under 90 days to best-by.", "yes"],
            ["SO-CMC", "Corner Market Co-op", "30", d(21), "60", "P3PL-01", "CBC-12PK:40; MTE-50:20",
             "Monthly. Appointment delivery.", "yes"],
        ],
    }


# --- Shopify (GraphQL Admin API shapes) ---------------------------------------------

def _shopify_order(oid: int, name: str, created: datetime, status: str, lines: list[tuple[str, int, int]], *,
                   note: str | None = None, tags: tuple[str, ...] = (), attrs: dict[str, str] | None = None,
                   customer: str = "Online customer", updated: datetime | None = None,
                   cancelled: datetime | None = None, tracking: tuple[str, str, str] | None = None,
                   fulfilled: datetime | None = None, delivered: datetime | None = None) -> dict:
    fulfillments = []
    if tracking or fulfilled:
        display = tracking[2] if tracking else ("DELIVERED" if delivered else "IN_TRANSIT")
        fulfillments.append({
            "createdAt": _z(fulfilled or updated or created), "deliveredAt": _z(delivered),
            "inTransitAt": _z((fulfilled or updated or created) + timedelta(hours=6)), "status": "SUCCESS",
            "displayStatus": display,
            "trackingInfo": [{"company": tracking[0], "number": tracking[1], "url": None}] if tracking else []})
    return {
        "id": f"gid://shopify/Order/{oid}",
        "name": name,
        "createdAt": _z(created),
        "updatedAt": _z(updated or created),
        "cancelledAt": _z(cancelled),
        "displayFulfillmentStatus": status,
        "displayFinancialStatus": "PAID",
        "note": note,
        "tags": list(tags),
        "customAttributes": [{"key": k, "value": v} for k, v in (attrs or {}).items()],
        "customer": {"displayName": customer},
        "lineItems": {"nodes": [{"sku": sku, "name": NAME.get(sku, sku), "quantity": qty,
                                 "unfulfilledQuantity": open_qty} for sku, qty, open_qty in lines]},
        "fulfillments": fulfillments,
    }


def shopify_orders(now: datetime) -> list[dict]:
    h = lambda hours: now - timedelta(hours=hours)  # noqa: E731
    dd = lambda days: (now + timedelta(days=days)).date().isoformat()  # noqa: E731
    sat = now + timedelta(days=(5 - now.weekday()) % 7 or 7)
    orders = [
        _shopify_order(5001, "#2041", h(120), "UNFULFILLED", [("CBC-12PK", 1, 1), ("MUG-TRV", 1, 1)],
                       note=f"Need by {_md(now + timedelta(days=1))} for a birthday - please gift wrap",
                       customer="Maya Chen"),
        _shopify_order(5002, "#2042", h(48), "UNFULFILLED", [("HOL-GBX", 2, 2)], tags=("priority",),
                       attrs={"Delivery Date": dd(2)}, customer="Sam Ortiz"),
        _shopify_order(5003, "#2043", h(26), "UNFULFILLED", [("PWR-20K", 2, 2), ("CASE-UNI", 1, 1)],
                       note="Customer asked: call before delivery, gate code 4412", customer="Priya Raman"),
        _shopify_order(5004, "#2044", h(96), "FULFILLED", [("CBC-32", 2, 0)], customer="Leo Brandt",
                       updated=h(30), fulfilled=h(80), delivered=h(30),
                       tracking=("UPS", "1Z999AA10123456784", "DELIVERED")),
        _shopify_order(5005, "#2045", h(22), "UNFULFILLED", [("CBC-32", 2, 2), ("BAR-ENR", 1, 1)],
                       tags=("subscription", "Subscription Recurring Order"), customer="Dana Whitfield"),
        _shopify_order(5006, "#2046", h(144), "UNFULFILLED", [("HOL-GBX", 24, 24)], tags=("seasonal", "preorder"),
                       attrs={"Ship By": dd(9)}, customer="Northwind Legal LLP",
                       note="Corporate gifts - include 24 printed note cards (attached to email)"),
        _shopify_order(5007, "#2047", h(70), "ON_HOLD", [("MTE-50", 1, 1)],
                       note="Address change requested by customer - hold until confirmed", customer="Ana Sousa"),
        # Need By two days back, so it reads as overdue in any timezone.
        _shopify_order(5008, "#2048", h(190), "UNFULFILLED", [("PMK-SYR", 3, 3)], attrs={"Need By": dd(-2)},
                       customer="Rosa Delgado"),
        _shopify_order(5009, "#2049", h(168), "PARTIALLY_FULFILLED", [("CBC-32", 1, 0), ("CHG-65W", 1, 1)],
                       note="Charger backordered, ships separately", customer="Ivan Petrov", updated=h(150)),
        _shopify_order(5010, "#2050", h(50), "UNFULFILLED", [("MUG-TRV", 2, 2)], customer="Kim Lee",
                       cancelled=h(30)),
        _shopify_order(5011, "#2051", h(3), "UNFULFILLED", [("CASE-UNI", 2, 2)], customer="Omar Haddad"),
        _shopify_order(5012, "#2052", h(1), "UNFULFILLED", [("CBC-12PK", 6, 6)], tags=("rush",),
                       note=f"RUSH - wedding favors, must arrive by {sat:%A}", customer="Jess Morgan"),
        _shopify_order(5013, "#2053", h(50), "FULFILLED", [("CHG-65W", 1, 0), ("CASE-UNI", 1, 0)],
                       customer="Tom Becker", updated=h(40), fulfilled=h(40),
                       tracking=("UPS", "1Z999AA10123456791", "IN_TRANSIT")),
        _shopify_order(5014, "#2054", h(25), "IN_PROGRESS", [("MTE-50", 2, 2)], customer="Nia Okafor",
                       updated=h(5)),
    ]
    # Fulfilled history for sell-through and outbound times, two SKUs per order.
    history = _daily_sales(now, HQ_GID, seed=11)
    rng = random.Random(23)
    oid, num = 6000, 1001
    by_day: dict[int, list[tuple[str, int]]] = {}
    for days_ago, sku, qty in history:
        by_day.setdefault(days_ago, []).append((sku, qty))
    for days_ago in sorted(by_day, reverse=True):
        items = by_day[days_ago]
        for i in range(0, len(items), 2):
            created = now - timedelta(days=days_ago, hours=(i * 3) % 20)
            lines = [(sku, qty, 0) for sku, qty in items[i:i + 2]]
            fulfilled = created + timedelta(hours=rng.uniform(3, 34))
            delivered = fulfilled + timedelta(days=rng.choice((1, 2, 2, 3, 3, 4, 5, 7)), hours=rng.uniform(0, 8))
            delivered = delivered if delivered < now else None
            orders.append(_shopify_order(oid, f"#{num}", created, "FULFILLED", lines,
                                         updated=delivered or fulfilled, fulfilled=fulfilled, delivered=delivered))
            oid += 1
            num += 1
    return orders


def shopify_inventory(now: datetime) -> list[dict]:
    levels = []
    for sku, qty in ON_HAND[HQ_GID].items():
        committed = {"PWR-20K": 2, "HOL-GBX": 2, "CBC-12PK": 7}.get(sku, 0)
        levels.append({"item": {"sku": sku},
                       "quantities": [{"name": "on_hand", "quantity": qty},
                                      {"name": "committed", "quantity": committed},
                                      {"name": "available", "quantity": qty - committed}]})
    return [{"id": HQ_GID, "name": "HQ Warehouse", "inventoryLevels": {"nodes": levels}}]


# --- Extensiv 3PL Warehouse Manager (REST API shapes) --------------------------------------

WH_STAFF = ("Kalani A.", "Jose R.", "Mele S.", "Tran N.")


def _ext_order(oid: int, ref: str, status: int, allocated: bool, created: datetime, items: list[tuple[str, int]], *,
               company: str = "", notes: str = "", shipping_notes: str = "", cancel: datetime | None = None,
               modified: datetime | None = None, po: str = "", tracking: str = "", carrier: str = "",
               picked: datetime | None = None, packed: datetime | None = None, by: str | None = None) -> dict:
    return {
        "readOnly": {"orderId": oid, "status": status, "fullyAllocated": allocated, "isClosed": status != 0,
                     "creationDate": _z(created), "lastModifiedDate": _z(modified or created),
                     "processDate": _z(modified or created) if status == 1 else None,
                     "pickDoneDate": _z(picked), "packDoneDate": _z(packed),
                     "lastModifiedByIdentifier": {"name": by} if by else None,
                     "facilityIdentifier": {"id": int(EXTENSIV_FACILITY), "name": "Riverside"}},
        "referenceNum": ref,
        "poNum": po,
        "notes": notes,
        "shippingNotes": shipping_notes,
        "shipCancelDate": _z(cancel),
        "shipTo": {"companyName": company, "name": company},
        "routingInfo": {"carrier": carrier, "trackingNumber": tracking},
        "_embedded": {"http://api.3plCentral.com/rels/orders/item": [
            {"itemIdentifier": {"sku": sku}, "qty": qty} for sku, qty in items]},
    }


def extensiv_orders(now: datetime) -> list[dict]:
    h = lambda hours: now - timedelta(hours=hours)  # noqa: E731
    orders = [
        _ext_order(7101, "#2046", 0, True, h(120), [("HOL-GBX", 24)], company="Northwind Legal LLP",
                   notes="Kit with printed note cards before shipping", modified=h(20), by="Mele S."),
        _ext_order(7102, "#2043", 0, False, h(24), [("PWR-20K", 2), ("CASE-UNI", 1)], company="Priya Raman",
                   shipping_notes="Lithium batteries - ground only, DG label required"),
        _ext_order(7103, "WH-PO-88213", 1, True, h(100), [("CBC-12PK", 60), ("MTE-50", 40)],
                   company="Corner Market Co-op", po="88213", modified=h(72), carrier="QuickHaul Logistics",
                   tracking="4471983", picked=h(80), packed=h(76), by="Jose R."),
        _ext_order(7104, "WH-PO-88240", 0, False, h(30), [("CBC-32", 36), ("BAR-ENR", 24)],
                   company="Harbor Foods Distribution", po="88240", cancel=now + timedelta(days=2),
                   notes="Retailer requires 90+ days remaining to best-by. Appointment required - call "
                         "receiving 24h ahead."),
        _ext_order(7106, "#2044", 1, True, h(95), [("CBC-32", 2)], company="Leo Brandt", modified=h(80),
                   carrier="UPS", tracking="1Z999AA10123456784", picked=h(88), packed=h(86), by="Kalani A."),
    ]
    return orders + extensiv_order_history(now)


def extensiv_order_history(now: datetime, days: int = 45) -> list[dict]:
    """Closed overflow orders (online overflow and wholesale) for outbound and productivity history."""
    rng = random.Random(31)
    skus = [p[0] for p in PRODUCTS]
    out, oid = [], 72000
    for days_ago in range(days, 0, -1):
        day = now - timedelta(days=days_ago)
        if day.weekday() == 6:  # the 3PL doesn't ship on Sundays
            continue
        for _ in range(rng.randint(2, 9) if day.weekday() < 5 else rng.randint(0, 3)):
            oid += 1
            created = day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=rng.uniform(15, 30))
            picked = created + timedelta(hours=rng.uniform(1.5, 26))
            packed = picked + timedelta(minutes=rng.uniform(10, 150))
            shipped = packed + timedelta(hours=rng.uniform(0.5, 7))
            if shipped >= now:
                continue
            wholesale = rng.random() < 0.15
            items = [(sku, rng.randint(12, 60) if wholesale else rng.randint(1, 3))
                     for sku in rng.sample(skus, rng.randint(1, 3))]
            out.append(_ext_order(oid, f"WH-PO-8{oid % 10000:04d}" if wholesale else f"OVF-{oid}", 1, True, created,
                                  items, company="Wholesale account" if wholesale else "Online overflow",
                                  po=str(oid) if wholesale else "", modified=shipped, picked=picked, packed=packed,
                                  by=rng.choice(WH_STAFF)))
    return out


def extensiv_receivers(now: datetime) -> list[dict]:
    """Receivers (inbound receipts), as /inventory/receivers returns them."""
    rng = random.Random(41)
    rel = "http://api.3plCentral.com/rels/inventory/receiveitem"

    def receiver(rid, ref, status, created, items, *, expected=None, arrived=None, stocked=None, po="", carrier="",
                 notes="", by=None, pallets=0):
        return {
            "readOnly": {"receiverId": rid, "status": status, "creationDate": _z(created),
                         "lastModifiedDate": _z(stocked or arrived or created),
                         "processDate": _z(stocked or arrived) if status == 1 else None,
                         "facilityIdentifier": {"id": int(EXTENSIV_FACILITY), "name": "Riverside"},
                         "lastModifiedByIdentifier": {"name": by} if by else None},
            "referenceNum": ref, "poNum": po, "expectedDate": _z(expected), "arrivalDate": _z(arrived),
            "shipCarrier": carrier, "notes": notes,
            "_embedded": {rel: [{"itemIdentifier": {"sku": sku}, "qty": qty,
                                 "palletIdentifier": {"id": rid * 100 + i % max(pallets, 1)} if pallets else None}
                                for i, (sku, qty) in enumerate(items)]},
        }

    out = []
    rid = 3300
    for days_ago in range(56, 3, -4):
        rid += 1
        exp = now - timedelta(days=days_ago)
        arrived = exp + timedelta(hours=rng.choice((-20, 0, 4, 10, 26, 52)))
        items = [(sku, rng.choice((48, 96, 120, 240))) for sku in rng.sample([p[0] for p in PRODUCTS], 2)]
        out.append(receiver(rid, f"PO {7600 + rid % 100}", 1, exp - timedelta(days=6), items, expected=exp,
                            arrived=arrived, stocked=arrived + timedelta(hours=rng.uniform(3, 40)),
                            po=str(7600 + rid % 100), carrier=rng.choice(("Ridgeway Trucking",
                            "Tri-State Express", "Harbor Drayage Co.")), by=rng.choice(WH_STAFF),
                            pallets=rng.randint(2, 9)))
    # Open: the container from Ningbo (ASN in, not arrived) and the HQ overflow move (on the dock).
    out.append(receiver(3390, "PO 7781", 0, now - timedelta(days=20), [("PWR-20K", 240), ("CHG-65W", 180)],
                        expected=now + timedelta(days=4), po="7781", carrier="Harbor Drayage Co.",
                        notes="Hazmat UN3480 - lithium. Container MSCU7781234."))
    out.append(receiver(3391, "TR-HQ-0921", 0, now - timedelta(days=7), [("PMK-SYR", 360), ("HOL-GBX", 288)],
                        expected=now - timedelta(days=1), arrived=now - timedelta(hours=14), carrier="Ridgeway Trucking",
                        notes="Overflow move from HQ: 18 pallets seasonal", pallets=18))
    out.append(receiver(3392, "PO 7795", 0, now - timedelta(days=9), [("BAR-ENR", 144)],
                        expected=now - timedelta(days=2), po="7795", carrier="Tri-State Express"))
    return out


def extensiv_lots(now: datetime) -> list[dict]:
    out = []
    for i, (sku, lot, recv, best_by, qty, allocated) in enumerate(LOTS_3PL):
        out.append({
            "receiveItemId": 9000 + i,
            "itemIdentifier": {"sku": sku},
            "lotNumber": lot,
            "receivedDate": _z(now - timedelta(days=recv)),
            "expirationDate": _z(now + timedelta(days=best_by)) if best_by is not None else None,
            "onHandQty": qty,
            "availableQty": qty - allocated,
            "facilityIdentifier": {"id": int(EXTENSIV_FACILITY), "name": "Riverside"},
            "palletIdentifier": {"id": 4000 + i, "nameKey": {"name": f"PLT-{4000 + i}"}},
            "locationIdentifier": {"nameKey": {"name": f"A-{i + 1:02d}-01"}},
        })
    return out


def extensiv_stock_summaries(now: datetime) -> list[dict]:
    totals: dict[str, list[int]] = {}
    for sku, _lot, _recv, _bb, qty, allocated in LOTS_3PL:
        t = totals.setdefault(sku, [0, 0])
        t[0] += qty
        t[1] += allocated
    return [{"facilityId": int(EXTENSIV_FACILITY), "itemIdentifier": {"sku": sku}, "onHand": on_hand,
             "allocated": alloc, "available": on_hand - alloc} for sku, (on_hand, alloc) in totals.items()]


def _month_start(d: date, back: int) -> date:
    months = d.year * 12 + d.month - 1 - back
    return date(months // 12, months % 12 + 1, 1)


def _month_end(start: date) -> date:
    nxt = _month_start(start, -1)
    return nxt - timedelta(days=1)


def extensiv_billing_activity(now: datetime) -> list[dict]:
    """Billable activity rows. Extensiv has no customer-side billing endpoint; live sync
    would build these from receivers, closed orders and month-end stock."""
    rows = []
    for back, scale in ((1, 1.0), (2, 0.85)):
        start = _month_start(now.date(), back)
        end = _month_end(start)
        period = f"{start:%Y-%m}"
        orders = round(198 * scale)
        rows += [
            {"chargeCode": "STOR-PLT", "date": end.isoformat(), "qty": "46" if back == 1 else "40",
             "reference": f"month-end pallets {period}"},
            {"chargeCode": "PICK-ORD", "date": end.isoformat(), "qty": str(orders), "reference": f"orders {period}"},
            {"chargeCode": "PICK-UNIT", "date": end.isoformat(), "qty": "412" if back == 1 else "350",
             "reference": f"additional units {period}"},
            {"chargeCode": "HAZ-HNDL", "date": end.isoformat(), "qty": "14" if back == 1 else "11",
             "reference": f"hazmat orders {period}"},
        ]
        receipts = [(start + timedelta(days=4), 8), (start + timedelta(days=12), 6), (start + timedelta(days=21), 8)]
        if back == 2:
            receipts = receipts[:2]
        for i, (day, pallets) in enumerate(receipts):
            rows.append({"chargeCode": "RCV-PLT", "date": day.isoformat(), "qty": str(pallets),
                         "reference": f"receipt R-{period}-{i + 1}"})
        if back == 1:
            rows.append({"chargeCode": "LABEL", "date": (start + timedelta(days=15)).isoformat(), "qty": "300",
                         "reference": f"retail labels {period}"})
    return rows


def invoice_csv(now: datetime, back: int) -> tuple[str, str]:
    """(filename, csv text) of the partner 3PL's monthly invoice."""
    start = _month_start(now.date(), back)
    end = _month_end(start)
    inv_date = end + timedelta(days=3)
    if back == 1:
        number = f"INV-{start:%Y-%m}-114"
        lines = [
            ("STOR-PLT", f"Pallet storage - {start:%B}", "46", "20.00", "920.00"),
            ("RCV-PLT", "Receiving per pallet", "22", "8.50", "187.00"),
            ("PICK-ORD", "Order pick & pack", "212", "2.75", "583.00"),
            ("PICK-UNIT", "Additional units", "412", "0.35", "144.20"),
            ("HAZ-HNDL", "Hazmat handling", "14", "5.00", "70.00"),
            ("HAZ-HNDL", "Hazmat handling", "14", "5.00", "70.00"),
            ("", "Fuel surcharge", "1", "65.00", "65.00"),
            ("LABOR-HR", "Special project labor - relabel", "6", "42.00", "252.00"),
            ("LABEL", "Retail labeling", "300", "0.25", "80.00"),
        ]
    else:
        number = f"INV-{start:%Y-%m}-097"
        lines = [
            ("STOR-PLT", f"Pallet storage - {start:%B}", "40", "18.00", "720.00"),
            ("RCV-PLT", "Receiving per pallet", "14", "8.50", "119.00"),
            ("PICK-ORD", "Order pick & pack", "168", "2.75", "462.00"),
            ("PICK-UNIT", "Additional units", "350", "0.35", "122.50"),
            ("HAZ-HNDL", "Hazmat handling", "11", "5.00", "55.00"),
        ]
    total = sum(float(x[4]) for x in lines)
    rows = [
        f"Vendor,{VENDOR_3PL}",
        f"Invoice Number,{number}",
        f"Invoice Date,{inv_date.isoformat()}",
        f"Billing Period,{start.isoformat()},{end.isoformat()}",
        "",
        "Charge Code,Description,Quantity,Rate,Amount",
        *[",".join(x) for x in lines],
        f"Total,,,,{total:.2f}",
    ]
    return f"{number}.csv", "\n".join(rows) + "\n"


# --- Lightspeed Retail (R-Series API shapes; numbers come back as strings) ---------------------

LS_ITEM_IDS = {sku: str(2001 + i) for i, sku in enumerate(NAME)}


def lightspeed_shops() -> dict:
    return {"Shop": [{"shopID": sid, "name": name} for sid, name in LS_SHOPS.items()]}


def lightspeed_items() -> dict:
    items = []
    for sku in NAME:
        shops = [{"shopID": sid, "qoh": str(ON_HAND[sid].get(sku, 0))} for sid in LS_SHOPS]
        if not any(int(s["qoh"]) for s in shops):
            continue
        # shopID 0 is the all-shops total in R-Series; normalizers must skip it.
        shops.append({"shopID": "0", "qoh": str(sum(int(s["qoh"]) for s in shops))})
        items.append({"itemID": LS_ITEM_IDS[sku], "customSku": sku, "description": NAME[sku],
                      "upc": next(p[2] for p in PRODUCTS if p[0] == sku), "ItemShops": {"ItemShop": shops}})
    return {"Item": items}


# Store staff by shop: (employeeID, first, last, shift) - "am" opens, "pm" closes.
LS_STAFF = {"1": [("11", "Keoni", "Kahale", "am"), ("12", "Lani", "Park", "pm"), ("13", "Mika", "Torres", "pm")],
            "2": [("21", "Noa", "Fujita", "am"), ("22", "Ben", "Cruz", "pm")]}


def lightspeed_employees() -> dict:
    return {"Employee": [{"employeeID": eid, "firstName": first, "lastName": last, "archived": "false"}
                         for staff in LS_STAFF.values() for eid, first, last, _ in staff]}


def _shift_hours(shift: str) -> tuple[int, int]:
    return (8, 15) if shift == "am" else (13, 20)  # Hawaii local hours


def _hst(day: datetime, hour: float) -> datetime:
    """A Hawaii-time hour on `day`'s local date, as naive UTC (HST is UTC-10, no DST)."""
    local = (day - timedelta(hours=10)).replace(hour=0, minute=0, second=0, microsecond=0)
    return local + timedelta(hours=hour + 10)


def lightspeed_employee_hours(now: datetime, days: int = 30) -> dict:
    rows, hid = [], 7000
    rng = random.Random(53)
    for days_ago in range(days, -1, -1):
        day = now - timedelta(days=days_ago)
        for sid, staff in LS_STAFF.items():
            for eid, _f, _l, shift in staff:
                if rng.random() < 0.18 and days_ago:  # day off (everyone is in today)
                    continue
                start, end = _shift_hours(shift)
                clock_in = _hst(day, start + rng.uniform(-0.2, 0.25))
                clock_out = _hst(day, end + rng.uniform(-0.3, 0.6))
                if clock_in >= now:
                    continue
                hid += 1
                rows.append({"employeeHoursID": str(hid), "employeeID": eid, "shopID": sid,
                             "checkIn": clock_in.isoformat() + "+00:00",
                             "checkOut": clock_out.isoformat() + "+00:00" if clock_out < now else None})
    return {"EmployeeHours": rows}


def lightspeed_sales(now: datetime) -> dict:
    sales = []
    sale_id, line_id = 90000, 500000
    rng = random.Random(61)
    for sid in LS_SHOPS:
        by_day: dict[int, list[tuple[str, int]]] = {}
        for days_ago, sku, qty in _daily_sales(now, sid, seed=int(sid) * 7):
            by_day.setdefault(days_ago, []).append((sku, qty))
        for days_ago, items in sorted(by_day.items(), reverse=True):
            day = now - timedelta(days=days_ago)
            # A few tickets a day, up to two items each, rung up by whoever is on shift.
            for t in range(0, len(items), 2):
                lines = []
                for sku, qty in items[t:t + 2]:
                    line_id += 1
                    lines.append({"saleLineID": str(line_id), "itemID": LS_ITEM_IDS[sku], "unitQuantity": str(qty),
                                  "calcTotal": f"{qty * PRICE[sku]:.2f}"})
                hour = rng.uniform(9, 19.5)
                on_shift = [e for e in LS_STAFF[sid] if _shift_hours(e[3])[0] <= hour < _shift_hours(e[3])[1]]
                sale_id += 1
                sales.append({
                    "saleID": str(sale_id), "shopID": sid, "completed": "true", "voided": "false",
                    "employeeID": rng.choice(on_shift or LS_STAFF[sid])[0],
                    "timeStamp": _hst(day, hour).replace(microsecond=0).isoformat() + "+00:00",
                    # A sale with one line comes back as an object, not a list.
                    "SaleLines": {"SaleLine": lines[0] if len(lines) == 1 else lines},
                })
    return {"Sale": sales}


def lightspeed_purchase_orders(now: datetime) -> dict:
    """Purchase orders to the stores (R-Series Order with OrderLines and Vendor)."""
    def po(oid, sid, vendor, ordered, items, *, arrival, received=None, complete=False):
        return {"orderID": str(oid), "shopID": sid, "vendorID": str(400 + len(vendor)), "refNum": f"PO-{oid}",
                "orderedDate": _z(ordered).replace("Z", "+00:00"),
                "arrivalDate": _z(arrival).replace("Z", "+00:00") if arrival else None,
                "receivedDate": _z(received).replace("Z", "+00:00") if received else None,
                "complete": "true" if complete else "false", "archived": "false",
                "Vendor": {"vendorID": str(400 + len(vendor)), "name": vendor},
                "OrderLines": {"OrderLine": [{"orderLineID": f"{oid}{i}", "itemID": LS_ITEM_IDS[sku],
                                              "quantity": str(qty), "numReceived": str(got)}
                                             for i, (sku, qty, got) in enumerate(items)]}}

    d = lambda days: now + timedelta(days=days)  # noqa: E731
    return {"Order": [
        po(301, "1", "Island Coffee Supply", d(-26), [("CBC-32", 48, 48), ("CBC-12PK", 24, 24)], arrival=d(-21),
           received=d(-20), complete=True),
        po(302, "2", "Island Coffee Supply", d(-19), [("CBC-32", 36, 36), ("MTE-50", 24, 24)], arrival=d(-14),
           received=d(-13), complete=True),
        po(303, "1", "Brand HQ Transfer", d(-12), [("HOL-GBX", 20, 20), ("PMK-SYR", 30, 24)], arrival=d(-8),
           received=d(-6), complete=True),
        po(304, "2", "Pacific Merch Co.", d(-9), [("MUG-TRV", 24, 0), ("CASE-UNI", 30, 0)], arrival=d(-2)),
        po(305, "1", "Island Coffee Supply", d(-3), [("CBC-32", 48, 0), ("BAR-ENR", 24, 0)], arrival=d(3)),
    ]}


# --- iQmetrix RQ (report rows; field names assumed until the Data Connect reports are confirmed) -----

def rq_stores() -> list[dict]:
    return [{"StoreID": int(sid), "StoreName": name} for sid, name in RQ_STORES.items()]


def rq_inventory() -> list[dict]:
    return [{"StoreID": int(sid), "ProductSKU": sku, "ProductName": NAME[sku], "QuantityOnHand": qty,
             "QuantityCommitted": 0} for sid in RQ_STORES for sku, qty in ON_HAND[sid].items()]


RQ_REPS = {"101": ("Alana K.", "Devin M."), "102": ("Grace L.", "Pono W.")}


def rq_sales(now: datetime) -> list[dict]:
    rows = []
    for sid in RQ_STORES:
        for n, (days_ago, sku, qty) in enumerate(_daily_sales(now, sid, seed=int(sid))):
            rows.append({"InvoiceIDByStore": f"{sid}-{n + 10000}", "LineID": 1, "StoreID": int(sid),
                         "DateCreated": _z(now - timedelta(days=days_ago, hours=5)), "ProductSKU": sku,
                         "Quantity": qty, "TotalPrice": round(qty * PRICE[sku], 2),
                         "SalesPerson": RQ_REPS[sid][n % 2]})
    return rows


# --- Gmail & Drive: carrier/agent emails and 3PL invoices --------------------------------------

def gmail_messages(now: datetime) -> list[dict]:
    fname, csv_text = invoice_csv(now, back=1)
    eta1 = now + timedelta(days=4)
    eta2 = now + timedelta(days=3)
    return [
        {"id": "msg-1001", "internalDate": _z(now - timedelta(hours=9)), "from": "ops@bluewave.example",
         "subject": "Status update - container MSCU7781234 / PO 7781",
         "body": (f"Hello team,\n\nContainer MSCU7781234 was discharged at Port of Long Beach on "
                  f"{_md(now - timedelta(days=1))}. It has been handed off to Harbor Drayage Co. for final "
                  f"delivery under ref HD-55120.\nRevised ETA: {eta1:%m/%d/%Y}.\n\nBlueWave Forwarding"),
         "attachments": []},
        {"id": "msg-1002", "internalDate": _z(now - timedelta(hours=5)), "from": "dispatch@quickhaul.example",
         "subject": "PRO 4471983 interline notice",
         "body": (f"PRO 4471983 has been interlined to Central Plains Freight at the Dallas terminal. "
                  f"New PRO: CPF-220931. Estimated delivery {_md(eta2)}."),
         "attachments": []},
        {"id": "msg-1003", "internalDate": _z(now - timedelta(days=20)), "from": "billing@riverside3pl.example",
         "subject": f"Riverside 3PL invoice {fname[:-4]}",
         "body": "Please find attached this month's invoice. Payment terms net 30.",
         "attachments": [{"filename": fname, "text": csv_text}]},
        {"id": "msg-1004", "internalDate": _z(now - timedelta(hours=30)), "from": "team@ourco.example",
         "subject": "Lunch order for Friday", "body": "Tacos again?", "attachments": []},
    ]


def drive_files(now: datetime) -> list[dict]:
    fname, csv_text = invoice_csv(now, back=2)
    return [{"id": "drv-501", "name": fname, "mimeType": "text/csv",
             "modifiedTime": _z(now - timedelta(days=50)), "text": csv_text}]
