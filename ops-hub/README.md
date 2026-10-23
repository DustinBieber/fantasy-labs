# Ops Hub

One place to watch orders, stock, freight and 3PL bills across Shopify, Extensiv, Lightspeed,
iQmetrix RQ and Google Workspace. Alerts go to Google Chat, Telegram, WhatsApp and Gmail; there is
a daily two-way handoff with Muse AI, and Claude, Gemini and ChatGPT read the messy text.

Every connector runs on realistic sample data until it is connected. Systems without API access yet
run on their own **export files** (Drive folder, report emails or upload) and switch to the API
automatically once credentials arrive. Connect everything from the dashboard's **Setup** tab, which
has paste-and-test forms and Google and Lightspeed sign-in buttons.
**[CONNECT-TODAY.md](CONNECT-TODAY.md)** is the checklist; [docs/SETUP.md](docs/SETUP.md) has the detail.

## What it does

| Area | What you get |
| --- | --- |
| **Order watch** | Shopify and Extensiv orders in one list. Each warehouse record is linked to its Shopify order, so you see both statuses together. Deadlines are found in note attributes ("Delivery Date"), tags (`need-by:2026-11-03`), warehouse ship-cancel dates, free-text notes ("need by Nov 3", "must arrive by Saturday") and Muse updates. Notes are flagged for words like *hold, cancel, address change, call before, gift, fragile*. Orders are typed priority / subscription / seasonal / wholesale. Alerts fire for overdue, due-soon, priority-not-started and stale orders. |
| **Inventory** | Stock per location (HQ, partner 3PL, Lightspeed stores, RQ partner stores) with a daily snapshot, plus sell-through, units per day, Muse forecasts and days of supply. Low-stock alerts plan on the faster of recent sales and the forecast, and say where to replenish from. |
| **Best-by & FIFO** | Lots with best-by dates in FEFO or FIFO pick order. A projection shows units that will pass best-by unsold. The pick planner skips lots too close to best-by, and standing orders are checked against each customer's minimum remaining shelf life. |
| **Freight** | Each shipment is made of legs. When a carrier hands off to another carrier or agent, a new leg starts with its own reference, and updates quoting any of the references land on the same shipment. Updates come from carrier/forwarder emails, WhatsApp or Telegram texts from drivers and agents, or the dashboard (where you can also book a shipment). Alerts fire for late, silent (with the carrier's contact) and exception shipments, for hazmat on a carrier not approved for hazmat, and for hazmat missing UN numbers. |
| **3PL billing** | Each partner-warehouse invoice (CSV, Excel, or PDF read by AI, from Gmail or Drive) is checked against the rate card and the warehouse's own activity. Flags: rate above contract, more units billed than the activity supports, duplicates, charges not on the rate card, arithmetic errors, labor with no work order. The dispute note is written for you. |
| **Muse AI** | Every morning a brief + data file goes to a shared Drive folder. Muse's replies come back as alerts, tasks, order deadlines/notes and forecasts. Format: [MUSE_HANDOFF.md](MUSE_HANDOFF.md). |
| **Chat** | Severity-routed alerts that don't repeat: one that clears resolves itself, and one that gets worse re-notifies. A daily digest (07:00 Hawaii time). The Telegram/WhatsApp bot answers `alerts`, `order #2041`, `stock CBC-32`, `ship 4471983`, `lots`, `digest`, and logs carriers' texted updates. |
| **Google** | The master data sheet (products with hazmat/shelf life/seasonal, locations, carriers, freight bookings, rate card, standing orders), report tabs rewritten after each sync, a calendar of deadlines, ETAs and standing-order dates, and Drive folders for invoices, RQ reports and Muse. |

## Quick start

```bash
cd ops-hub
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env          # fill in what you have (docs/SETUP.md)

python -m opshub serve        # http://127.0.0.1:8000 (syncs every 15 min, daily jobs on Hawaii time)
python -m opshub connectors   # what's live, what's still on sample data, what each one needs
python -m pytest              # tests
```

No terminal needed: double-click `run/start-mac.command` (Mac) or `run/start-windows.bat` (Windows).
For the Mac mini, `run/install-mac-service.sh` keeps it running in the background.
Or with Docker: `docker compose up -d`. **Set `OPSHUB_PASSWORD`** before the app is reachable from
anywhere but your own machine.

Other commands: `sync [connector]`, `rules`, `job <digest|muse_export|calendar|sheets_report|telegram_poll>`,
`google-login`, `lightspeed-login`, `telegram-chats`, `sheet-template DIR`, `scheduler`.

## Connector status

| System | How | Status |
| --- | --- | --- |
| Shopify | GraphQL Admin API | **Live client built**: orders, notes, attributes, stock, online sales, parcel tracking |
| Extensiv 3PL WM | REST API (credentials from the 3PL) | **Live client built**: orders, stock + lots/best-by, outbound tracking, derived billable activity. Verify on first run. |
| Lightspeed Retail R-Series | V3 API, OAuth with rotating refresh token | **Live client built**: shops, items with shop stock, sales |
| iQmetrix RQ | Report exports (CSV/Excel) via Drive folder, scheduled email, or dashboard upload | **Built**: no API purchase needed |
| Google Sheets | Sheets API | **Live**: master data in, report tabs out |
| Gmail | Gmail API | **Live**: reads carrier mail and invoices (CSV/XLSX/PDF), sends digests |
| Google Drive | Drive API | **Live**: invoice/BOL folder, RQ folder, Muse folders |
| Google Calendar | Calendar API | **Live**: all-day events for deadlines, ETAs, standing orders; stale ones removed |
| Google Chat | Incoming webhook | **Live** (Workspace) |
| Telegram | Bot API, polling | **Live**: alerts and two-way commands |
| WhatsApp | Cloud API + webhook | **Live**: template alerts, two-way replies, signature-checked webhook |
| Muse AI | Drive folders | **Live**: daily brief out, alerts/tasks/updates/forecasts in |
| Claude / Gemini / ChatGPT | APIs | **Live**: notes, carrier emails, PDF invoices, free-text Muse notes |

"Live client built" means the API calls are implemented and tested against mocked responses;
each one gets its first real run when you add its credentials.

## How it fits together

```
 Shopify ─┐                                           ┌─ Dashboard (/), API (/api, docs at /docs)
 Extensiv ┤   connectors       ingest        rules    ├─ Google Chat / Telegram / WhatsApp / Gmail
 Lightspeed┤  (vendor JSON ─► (unified ─► (alerts) ───┼─ Daily digest, Muse brief
 RQ files ┼─► → records)      tables)                 ├─ Calendar, Sheets reports
 Sheets ──┤                       ▲                    └─ CSV exports
 Gmail ───┤      Claude / Gemini / ChatGPT read notes, emails, PDFs
 Drive ───┤
 Muse ────┘                  Telegram / WhatsApp commands & carrier updates ⇄ chatops
```

* `opshub/connectors/`: one module per system. Each turns that system's payload into normalized records
  (`base.py`). Demo fixtures (`opshub/demo/fixtures.py`) use the vendors' real response shapes.
* `opshub/services/ingest.py` upserts by natural key, so re-syncing is safe. It maps each system's
  location ids and SKUs through the master sheet.
* `opshub/services/rules.py` holds the alert rules, `notify.py` routing, `jobs.py` the scheduler,
  `chatops.py` chat commands, and `muse.py` the handoff.
* `opshub/models.py` is the schema (SQLite by default, Postgres via `DATABASE_URL`). New columns are
  added to existing databases automatically.

## Open items

* **3PL invoice format**: the CSV/Excel reader follows a common layout. Send a real invoice to match
  it exactly. PDFs are read by AI when a key is set.
* **Extensiv / Lightspeed first live run**: field names come from Extensiv's API (via an
  open-source client) and Lightspeed's V3 docs. The first sync with real credentials should be
  checked against their screens.
