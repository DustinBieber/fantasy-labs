# Connecting Ops Hub to your systems

Work top to bottom. Each step makes one connector switch from **Demo data** to **Live** on the
Connectors page. Everything not yet connected keeps running on sample data. Put settings in
`ops-hub/.env` (copy `.env.example`) and restart.

Check progress any time with `python -m opshub connectors`.

## 0. Where it runs

Ops Hub has to run around the clock to sync, send alerts, answer chat and do the daily Muse handoff.

* **Docker** (any small cloud VM, or an always-on office PC/Mac): `docker compose up -d` in `ops-hub/`.
  Data lives in the `opshub-data` volume.
* **Without Docker**: `pip install -e .`, then keep `python -m opshub serve --host 0.0.0.0` running
  (e.g. as a systemd service).

Set `OPSHUB_PASSWORD` before the dashboard is reachable from the internet. A public HTTPS address
(e.g. behind Caddy or Cloudflare Tunnel) is only needed for WhatsApp replies (step 9).
Business time is Hawaii (`BUSINESS_TIMEZONE=Pacific/Honolulu`).

## 1. Google (Sheets, Drive, Gmail, Calendar, RQ files, Muse)

One Google login covers all of these.

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create a project, and under
   **APIs & Services → Library** enable **Google Sheets API, Google Drive API, Gmail API, Google Calendar API**.
2. **OAuth consent screen**:
   * Google Workspace account: choose **Internal**.
   * Personal Gmail: choose **External**, add yourself as a test user, then **Publish app**.
     Approve the "unverified app" warning for your own account. Without publishing, Google expires
     the login every 7 days.
3. **Credentials → Create credentials → OAuth client ID → Desktop app**. Put the ID and secret in
   `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`.
4. On a computer with a browser, run `python -m opshub google-login` and approve.
   The refresh token is saved in the database. If Ops Hub runs on a server, either run the login
   there and paste back the address the browser lands on, or log in locally and copy the token
   into `GOOGLE_OAUTH_REFRESH_TOKEN`.

   Workspace alternative: a service account key file in `GOOGLE_SERVICE_ACCOUNT_JSON`. Add
   `GOOGLE_DELEGATED_USER` (needs domain-wide delegation) to act as the ops mailbox.

Then fill in the IDs. Each comes from the item's URL in your browser:

| Setting | What |
| --- | --- |
| `GOOGLE_SHEETS_MASTER_ID` | The master-data sheet. Create it with `python -m opshub sheet-template ./tpl`, then import each CSV as a tab with the same name. Replace the sample rows with yours. |
| `GOOGLE_SHEETS_REPORT_ID` | Optional: a sheet Ops Hub rewrites after every sync (Watchlist, Stock, SellThrough, Lots, Alerts tabs). |
| `GOOGLE_DRIVE_FOLDER_ID` | Folder where 3PL invoices, BOLs and packing lists land. |
| `GOOGLE_CALENDAR_ID` | Calendar for deadlines and ETAs (Calendar settings → *Integrate calendar* → Calendar ID). Use a dedicated calendar: Ops Hub removes events it created that are no longer current. |
| `GMAIL_QUERY` | Which mail to read. Default: last 14 days. Narrow it, e.g. `newer_than:14d (from:riverside3pl.com OR subject:(PRO OR BOL OR tracking OR invoice))`. |
| `GMAIL_TO` | Who gets the emailed digest. |

**Locations tab, the important bit.** Each row maps a system's own store or warehouse id to your
location code:

| source | external_id is |
| --- | --- |
| `shopify` | Location gid, e.g. `gid://shopify/Location/123` |
| `extensiv` | Facility id |
| `lightspeed` | shopID |
| `iqmetrix_rq` | The store name or number **exactly as it appears in your RQ reports** |

## 2. Shopify

Admin → **Settings → Apps and sales channels → Develop apps → Create an app**. If your admin no
longer offers this, create the app in the Shopify **Dev Dashboard** and install it on your store.

Grant these Admin API scopes: `read_orders`, `read_all_orders`, `read_inventory`, `read_locations`,
`read_fulfillments`. Install the app and copy the **Admin API access token**:

```
SHOPIFY_SHOP=yourstore.myshopify.com
SHOPIFY_ACCESS_TOKEN=shpat_...
SHOPIFY_SALES_LOCATION_ID=gid://shopify/Location/...   # where online sales draw stock from
```

Deadlines are picked up from order note attributes named like *Need By, Delivery Date, Ship By*,
from tags like `need-by:2026-11-03`, and from the order note itself.

## 3. Lightspeed Retail (R-Series)

1. Register an API client in the Lightspeed developer portal and set its redirect URI to
   `http://127.0.0.1:8766/`.
2. `LIGHTSPEED_CLIENT_ID` / `LIGHTSPEED_CLIENT_SECRET` in `.env`, then run `python -m opshub lightspeed-login`.
   It stores the refresh token (Lightspeed rotates it on every use; Ops Hub keeps the newest) and
   prints your account id for `LIGHTSPEED_ACCOUNT_ID`.
3. SKUs are matched on `customSku`. Set `LIGHTSPEED_SKU_FIELD=systemSku` or `manufacturerSku` if your
   SKUs live elsewhere.

## 4. Extensiv (partner warehouse)

Ask the 3PL to set up **REST API access for your customer account**. You need:

```
EXTENSIV_CLIENT_ID=...
EXTENSIV_CLIENT_SECRET=...
EXTENSIV_USER_LOGIN_ID=...       # the user the API acts as
EXTENSIV_CUSTOMER_ID=...         # your customer id in their WMS
EXTENSIV_FACILITY_ID=...         # comma-separated if you're in several of their buildings
EXTENSIV_TPL_KEY=...             # if they give you a "3PL key" / tpl GUID
EXTENSIV_VENDOR_NAME=Riverside 3PL   # must match the vendor on your RateCard tab
```

Billable activity (for the invoice audit) is derived as follows:

* one `PICK-ORD` per shipped order
* `PICK-UNIT` for each unit after the first
* `RCV-PLT` for each pallet received
* `STOR-PLT` for pallets in storage at month end
* `HAZ-HNDL` for each order containing a hazmat SKU

If your rate card uses other codes, map them, e.g.
`EXTENSIV_ACTIVITY_CODES={"order": "PP-ORDER", "storage_pallet": ""}` (empty turns one off).
The first live sync should be checked against the 3PL's own screens.

## 5. iQmetrix RQ (no API needed)

Ops Hub reads the reports you already export from RQ (CSV or Excel). Use an *inventory listing*
(store, SKU, on hand) and a *sales by product/invoice* report (date, store, SKU, quantity, total).
Columns are matched by name, so most layouts work. Any of these:

* **Scheduled email**: schedule the reports in RQ to email the ops mailbox, then set
  `RQ_GMAIL_QUERY`, e.g. `from:(reports@iqmetrix.net) subject:(Inventory OR Sales) newer_than:3d`.
* **Drive folder**: save exports to a folder and set `RQ_DRIVE_FOLDER_ID`.
* **By hand**: Inventory page → **Import RQ report**.

An inventory report counts as the full stock of the stores in it: SKUs it leaves out are set to 0
at those stores. If reports have no store column, set `RQ_DEFAULT_STORE`.

## 6. Muse AI

Create two Drive folders, **Ops Hub → Muse** and **Muse → Ops Hub**, shared with Ops Hub's Google
account and with Muse. Then set:

```
MUSE_DRIVE_OUTBOX_FOLDER_ID=...   # Ops Hub -> Muse
MUSE_DRIVE_INBOX_FOLDER_ID=...    # Muse -> Ops Hub
MUSE_EXPORT_TIME=06:00
```

Give Muse [`MUSE_HANDOFF.md`](../MUSE_HANDOFF.md): it describes both files.

## 7. Google Chat

Google Workspace only. In the space: **Apps & integrations → Webhooks → Add webhook**. Copy the
URL into `GOOGLE_CHAT_WEBHOOK_URL`.

## 8. Telegram

1. Message **@BotFather**, send `/newbot`, and copy the token to `TELEGRAM_BOT_TOKEN`.
2. Add the bot to your ops group and send `/start` there.
3. Run `python -m opshub telegram-chats` and put the group's id (it starts with `-`) in `TELEGRAM_CHAT_ID`.
   That group gets alerts and can ask questions (`alerts`, `order #2041`, `stock CBC-32`, `ship 4471983`).
4. Optional: `TELEGRAM_STAFF_CHAT_IDS` for more chats with full access, and `TELEGRAM_FREIGHT_CHAT_IDS`
   for drivers and agents who can only post shipment updates.

## 9. WhatsApp (Cloud API)

1. Create an app at [developers.facebook.com](https://developers.facebook.com) and add **WhatsApp**.
2. Get a **permanent token** (Business Settings → System users → generate a token with
   `whatsapp_business_messaging`). Copy it and the **phone number ID** into `WHATSAPP_TOKEN` and
   `WHATSAPP_PHONE_NUMBER_ID`.
3. `WHATSAPP_TO` is the comma-separated staff numbers that get alerts.
4. Create and get approved a **message template** with one body variable (`{{1}}`), e.g. named
   `ops_alert`, and set `WHATSAPP_TEMPLATE=ops_alert`. WhatsApp only allows free text to people who
   messaged you in the last 24 hours.
5. To receive messages (staff questions, carrier updates):
   * Under WhatsApp → Configuration, set the webhook to `https://YOUR-HOST/webhooks/whatsapp`, with
     a verify token of your choice in `WHATSAPP_VERIFY_TOKEN`.
   * Subscribe to `messages`, and put the app secret (App settings → Basic) in `WHATSAPP_APP_SECRET`.
   * `WHATSAPP_FREIGHT_NUMBERS` lists carrier and driver numbers whose texts are logged as shipment
     updates. A message must quote the PRO/tracking/BOL to match.

## 10. AI (optional but useful)

Any of these. They are tried in `AI_PROVIDERS` order, and the built-in rules cover anything left out.

* Claude: `ANTHROPIC_API_KEY` (console.anthropic.com)
* Gemini: `GEMINI_API_KEY` (aistudio.google.com)
* ChatGPT: `OPENAI_API_KEY` (platform.openai.com)

AI reads messy order notes and carrier emails, PDF invoices, and Muse notes written in free text.
