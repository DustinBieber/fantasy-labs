# Ops Hub

One place to watch orders, stock, freight and 3PL bills across Shopify, Extensiv, Lightspeed,
iQmetrix RQ and Google Workspace, with alerts to Google Chat, Telegram and WhatsApp.

This is the **foundation**: the unified data model, every connector's data mapping, the rules
engine, and the dashboard. All of it runs on realistic sample data until credentials go in.
Connectors switch from sample data to live data one at a time (see [Connector status](#connector-status)).

## What it does

| Area | What you get |
| --- | --- |
| **Order watch** | Orders from Shopify and Extensiv in one list. Each warehouse record is linked to its Shopify order, so you see both statuses together. Deadlines are found in note attributes ("Delivery Date"), tags (`need-by:2026-11-03`), warehouse ship-cancel dates, and free-text notes ("need by Nov 3", "must arrive by Saturday"). Notes are flagged for words like *hold, cancel, address change, call before, gift, fragile*. Orders are typed priority / subscription / seasonal / wholesale. Alerts fire for overdue, due-soon, priority-not-started and stale orders. |
| **Inventory** | Stock per location (HQ, partner 3PL, Lightspeed stores, RQ partner stores), a daily on-hand snapshot, and sell-through, units per day and days of supply per SKU and location. Low-stock alerts say where to replenish from. |
| **Best-by & FIFO** | Lots with best-by dates (from Extensiv), in FEFO or FIFO pick order. A projection shows how many units will pass best-by unsold. The pick planner skips lots too close to best-by, and standing orders are checked against each customer's minimum remaining shelf life. |
| **Freight** | Each shipment is made of **legs**. When a carrier hands off to another carrier or agent (port to drayage, LTL interline, broker to local courier), a new leg starts with its own reference. Updates quoting either reference land on the same shipment. Carrier and forwarder emails are read automatically. Updates that come by phone or WhatsApp can be logged by hand from the dashboard. Alerts fire for late, silent (with the carrier's contact) and exception shipments, for hazmat freight on a carrier not approved for hazmat, and for hazmat freight missing UN numbers. |
| **3PL billing** | Each partner-warehouse invoice (CSV from Gmail or Drive) is checked against the rate card and the warehouse's own activity for that period. Flags: rate above contract, more units billed than the activity supports, duplicate lines, charges not on the rate card, arithmetic errors, and labor or project charges with no signed-off work order. Shows expected vs billed per line and writes the dispute note for you. |
| **Alerts** | Severity-routed messages (urgent → Chat + Telegram + WhatsApp by default). Alerts don't repeat: one that clears resolves itself, and one that gets worse re-notifies. Ack or Dismiss from the dashboard. Also a daily digest, a calendar feed of deadlines, ETAs and standing-order dates, and CSV exports for Sheets. |
| **AI** | Claude, Gemini and ChatGPT read messy order notes and carrier emails, tried in `AI_PROVIDERS` order with fallback. With no key set, the built-in rule-based parsers do the work. Results are cached until the notes change. |

## Quick start

```bash
cd ops-hub
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # optional: fill in what you have

python -m opshub sync           # pull from every connector (sample data where not configured)
python -m opshub serve          # http://127.0.0.1:8000
python -m opshub connectors     # which connectors are live / demo / missing settings
python -m pytest                # tests
```

To keep data fresh, set `SYNC_INTERVAL_MINUTES=15` while `serve` runs, or schedule
`python -m opshub sync` with cron. **Set `OPSHUB_PASSWORD`** before the app is reachable from
anywhere but your own machine; it turns on a login prompt for every page and API call.

## How it fits together

```
 Shopify ─┐                                         ┌─ Dashboard (/)
 Extensiv ┤                                         ├─ JSON API (/api, docs at /docs)
 Lightspeed┤   connectors      ingest       rules   ├─ Google Chat / Telegram / WhatsApp
 RQ ──────┼─► (vendor JSON ─► (unified ─► (alerts) ─┼─ Daily digest
 Sheets ──┤    → records)      tables)               ├─ Calendar feed
 Gmail ───┤                       ▲                  └─ CSV exports
 Drive ───┘        Claude / Gemini / ChatGPT read notes & emails
```

* `opshub/connectors/` has one module per system. Each turns that system's own payload
  shape into normalized records (`connectors/base.py`). Demo fixtures (`opshub/demo/fixtures.py`)
  use the vendors' real response shapes, so demo mode runs the same mapping code as live.
* `opshub/services/ingest.py` upserts records by natural key, so re-syncing is safe. It resolves
  each system's location ids and SKUs through the master sheet.
* `opshub/services/rules.py` holds every alert rule, and `notify.py` routes alerts to channels.
* `opshub/models.py` is the unified schema (SQLite by default, Postgres via `DATABASE_URL`).

## Connector status

| System | Provides | Status |
| --- | --- | --- |
| Shopify | orders, notes, attributes, stock, online sales, parcel tracking | **Live sync built** (GraphQL Admin API). Not yet run against your store. |
| Extensiv 3PL WM | partner-3PL orders, stock, lots + best-by, outbound tracking, billable activity | Mapping built. Live calls need API credentials from the 3PL. |
| Lightspeed Retail | store stock, store sales | Mapping built for R-Series. Live calls + OAuth refresh still to do. |
| iQmetrix RQ | partner-store stock and sales | Mapping uses assumed report fields. Need the Data Connect report list. |
| Google Sheets | master data: products (hazmat, UN no., shelf life, seasonal), locations, carriers, freight bookings, rate card, standing orders | Mapping built. Live needs the service-account auth step. |
| Gmail | carrier/forwarder emails, 3PL invoices; digest sending | Parsing built. Live needs the service-account auth step. |
| Google Drive | invoice / BOL folder | Parsing built. Live needs the service-account auth step. |
| Google Calendar | deadlines, ETAs, standing-order dates | Event bodies built (`/api/calendar/events`). Publishing needs auth. |
| Google Chat | alerts | **Live** (incoming webhook) |
| Telegram | alerts | **Live** (bot API) |
| WhatsApp | alerts | **Live** (Cloud API; needs an approved template for messages the business starts) |
| Claude / Gemini / ChatGPT | reading notes and emails | **Live** (API keys) |
| Muse | ? | Placeholder: tell us which product this is |

## Master data sheet

One Google Sheet, one tab each (header row first). The demo values in
`opshub/demo/fixtures.py::sheets_master` are a template.

* **Products**: `sku, name, upc, category, hazmat, hazmat_class, un_number, seasonal, season, shelf_life_days, unit_cost`
* **Locations**: `code, name, kind, source, external_id, address`. This maps each system's own
  location id (Shopify location gid, Extensiv facility id, Lightspeed shopID, RQ StoreID) to
  your location code. `source` is the one system trusted for that location's stock.
* **Carriers**: `name, aliases (;-separated), hazmat_approved, tracking_url_template ({ref}), contact`
* **Shipments**: `reference, direction, mode, carrier, agent, tracking_ref, origin, destination, destination_location, order_number, eta, hazmat, un_numbers, priority, status, booked_at, notes`
* **RateCard**: `vendor, charge_code, description, unit, rate, min_charge, requires_approval, effective_from, effective_to`
* **StandingOrders**: `code, customer_name, cadence_days, next_due, min_days_to_best_by, ship_from, lines (SKU:qty; SKU:qty), notes, active`

## 3PL invoice format

The parser reads CSVs shaped like `Vendor / Invoice Number / Invoice Date / Billing Period`
header rows, then a `Charge Code, Description, Quantity, Rate, Amount` table and a `Total` row.
Send a real invoice from the partner warehouse and the parser can be matched to it (PDF invoices
go through the AI path).
