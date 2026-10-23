# Ops Hub ⇄ Muse AI daily handoff

Two shared Google Drive folders:

| Folder | Who writes | Who reads | Setting |
| --- | --- | --- | --- |
| **Ops Hub → Muse** | Ops Hub, every day at `MUSE_EXPORT_TIME` (default 06:00 Hawaii time) | Muse | `MUSE_DRIVE_OUTBOX_FOLDER_ID` |
| **Muse → Ops Hub** | Muse, whenever it has something | Ops Hub, on every sync (default every 15 min) | `MUSE_DRIVE_INBOX_FOLDER_ID` |

Share both folders with the Google account Ops Hub is logged in as and with the account Muse uses.

## What Ops Hub sends each morning

Two files, named by the local date:

* `opshub-brief-YYYY-MM-DD.md`: a readable brief covering open alerts, orders to watch,
  low stock, lots near best-by, freight in transit and 3PL invoices to review.
* `opshub-to-muse-YYYY-MM-DD.json`: the same data, structured:

```json
{
  "kind": "opshub_to_muse", "version": 1, "generated_at": "2026-10-23T16:00:00Z",
  "timezone": "Pacific/Honolulu", "local_date": "2026-10-23",
  "alerts": [{"severity": "urgent", "kind": "order_deadline", "title": "...", "detail": "..."}],
  "orders_watch": [{"number": "#2041", "customer": "...", "status": "open", "order_type": "priority",
                    "required_by": "2026-10-25T09:59:59Z", "hours_left": 42.1, "note_flags": ["gift"],
                    "notes": "...", "hazmat": false, "lines": [{"sku": "CBC-12PK", "qty": 1}]}],
  "inventory": {
    "locations": [{"code": "HQ-WH", "name": "HQ Warehouse", "kind": "warehouse"}],
    "stock": [{"sku": "CBC-32", "name": "...", "on_hand": 308, "available": 308, "by_location": {"HQ-WH": 140}}],
    "sell_through_days": 30,
    "sell_through": [{"sku": "CBC-32", "sold": 157, "on_hand": 308, "sell_through_pct": 33.8,
                      "per_day": 5.23, "days_of_supply": 58.9, "seasonal": false}],
    "lots_near_best_by": [{"sku": "MTE-50", "location": "P3PL-01", "lot_number": "L2510", "best_by": "2026-11-17",
                           "days_left": 25, "qty": 150, "at_risk_units": 123}],
    "low_stock": [{"sku": "PWR-20K", "location": "RQ-101", "available": 4, "per_day": 1.8, "days_of_supply": 2.2}]
  },
  "freight": [{"reference": "IN-PO-7781", "status": "in_transit", "carrier": "Harbor Drayage Co.", "eta": "..."}],
  "standing_orders": [{"code": "SO-GVG", "customer": "...", "next_due": "2026-10-28", "lines": [...],
                       "min_days_to_best_by": 90}],
  "billing": [{"invoice": "INV-2026-09-114", "vendor": "Riverside 3PL", "billed": "2371.20",
               "expected": "1848.70", "overbilled": "522.50", "issues": 6}]
}
```

## What Muse can send back

Drop a file named like `muse-to-opshub-YYYY-MM-DD.json` in the **Muse → Ops Hub** folder.
Every key is optional:

```json
{
  "kind": "muse_to_opshub", "version": 1, "date": "2026-10-23",
  "alerts": [
    {"severity": "warn", "title": "Holiday Gift Box selling slower than plan",
     "detail": "7% sell-through vs 20% plan; bundle with cold brew?", "sku": "HOL-GBX"}
  ],
  "tasks": [
    {"title": "Confirm note cards are at the 3PL before kitting", "due": "2026-10-25", "order": "#2046"},
    {"title": "Get Tri-State's DG certificate or rebook", "due": "2026-10-23", "shipment": "IN-PO-7790"}
  ],
  "order_updates": [
    {"order": "#2054", "deadline": "2026-10-27", "note": "Customer needs it before Monday's opening."}
  ],
  "forecasts": [
    {"sku": "PWR-20K", "location": "RQ-101", "units_per_day": 1.8, "note": "Partner store promo"},
    {"sku": "CBC-32", "units_per_day": 7.5}
  ],
  "notes": "Free-text summary for the team."
}
```

What Ops Hub does with each:

| Key | Becomes |
| --- | --- |
| `alerts` | Alerts on the dashboard and in chat, routed by severity (`urgent`, `warn`, `info`). Each new file's alerts **replace** Muse's earlier alerts. Alerts expire after 7 days. |
| `tasks` | Task alerts. They turn `warn` when due within a day, and stay until someone clicks **Done**. |
| `order_updates` | The order's deadline (if earlier than what it already has) and a note shown on the order. |
| `forecasts` | Expected units/day, per location or for all locations. Low-stock alerts plan on the faster of recent sales and the forecast. |
| `notes` | An info item on the dashboard. |

Linking: `order` is an order number (`#2041`, `WH-PO-88240`), `sku` is a SKU from the product sheet,
`shipment` is a shipment reference or tracking/PRO number, and `location` is a location code (`HQ-WH`, `RQ-101`).

Other formats are accepted too:

* **CSV / Excel** with columns `type` (alert, task, order_update, forecast), `severity`, `title`,
  `detail`, `due`, `order`, `sku`, `location`, `units_per_day`, `deadline`, `note`.
* **Text / Markdown**: if an AI key is set, Ops Hub reads it into the structure above. Without one
  it is shown as a note.
