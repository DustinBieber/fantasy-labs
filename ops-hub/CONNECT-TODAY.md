# Connect Ops Hub today

All of this happens on the **Setup** tab (`http://localhost:8000/#setup`). Each system has a
**Test** button that makes a real call and says what worked or what's wrong. Anything without API
access yet runs on **export files**, and switches to the API by itself once credentials are saved,
without duplicates.

## Start first: these wait on other people

| Do now | Why first |
| --- | --- |
| Email the partner warehouse: *"Please set up Extensiv REST API access for our customer account: client ID, client secret, user login ID, our customer ID, facility ID, and the TPL key if you use one. We need read access to orders, receivers and stock details."* | They have to do it. Until then, export orders and stock-by-lot reports from their portal into **Ops Hub/Imports/Extensiv exports**. |
| Meta Business Manager → WhatsApp → Message templates: create `ops_alert` (Utility), body `Ops Hub: {{1}}` | Approval takes minutes to hours. |
| Telegram: message **@BotFather**, send `/newbot` | Token in 1 minute. |

## On the office computer (about 1 hour total)

1. **Start Ops Hub.** Copy the `ops-hub` folder to the computer and double-click
   `run/start-mac.command` (Mac) or `run/start-windows.bat` (Windows). The first run installs
   everything and opens the Setup page. It needs Python 3.11+ from python.org.
2. **Google (10 min).** In [console.cloud.google.com](https://console.cloud.google.com), signed in as
   **dustin@jocordistro.com**:
   1. New project **Ops Hub**.
   2. Enable the **Sheets, Drive, Gmail and Calendar** APIs.
   3. OAuth consent screen: **Internal**.
   4. Credentials → **OAuth client ID** → **Web application**, with authorized redirect URI
      `http://localhost:8000/setup/google/callback`.

   Paste the client ID and secret on the Setup page, then **Save** → **Sign in with Google** →
   **Set up Drive & Sheets**. That creates the Ops Hub folders, the master and report sheets and
   the Ops Hub calendar, and fills in all their IDs.
3. **Master sheet (15 min).** Open *Ops Hub master data* in Drive and fill in Products (hazmat,
   shelf life, seasonal) and Locations (map each Shopify location, Lightspeed shop, Extensiv facility
   and RQ store to a code). The Examples tab shows each format. Then RateCard and Carriers.
4. **Shopify (5 min).** Admin → Settings → Apps → Develop apps (or the Dev Dashboard) → create
   **Ops Hub**.
   * Scopes: `read_orders`, `read_all_orders`, `read_inventory`, `read_locations`, `read_fulfillments`.
   * Install it, then paste the store domain and Admin API token. **Test**.
5. **Lightspeed (10 min).** Register an API client with redirect URI
   `http://localhost:8000/setup/lightspeed/callback`, paste its ID and secret, then **Save** →
   **Connect Lightspeed**. If Lightspeed hasn't approved the client yet, export the Item list and
   Sales lines into **Imports/Lightspeed exports** for now.
6. **RQ (5 min).** In RQ, schedule the Inventory listing and Sales-by-invoice reports to email
   dustin@jocordistro.com daily, and put the search (e.g. `from:iqmetrix subject:(Inventory OR Sales)
   newer_than:3d`) in *Report emails search*. Or save exports into **Imports/RQ reports**.
7. **Muse (2 min).** Share the **Ops Hub → Muse** and **Muse → Ops Hub** folders with Muse, and
   give it `MUSE_HANDOFF.md`.
8. **Chat (10 min).**
   * Google Chat: create a webhook in the ops space and paste the URL.
   * Telegram: in @BotFather send `/setprivacy`, pick the bot, and choose **Disable** so it can read
     plain messages in the group. Then paste the bot token, add the bot to the ops group, send
     `/start`, and click **Find chats** → **Use for alerts**.
   * WhatsApp: paste the token, phone number ID, staff numbers and template name.
9. **AI (2 min).** Paste a Claude, Gemini or ChatGPT key.
   For Muse, ChatGPT or Gemini to read Ops Hub directly, click **Create a new key** under
   *Read-only API key* and give them the key with the URL (README, *Polling and API access*).
10. **Sync all** on the top bar. The Connectors tab shows each system as *Live*,
    *Live: export files* or *Sample data*. Once everything is live, set `DEMO_MODE=false` in `.env`
    so nothing falls back to sample data.

## Two-way WhatsApp (needs a public address)

WhatsApp replies and carrier texts need Meta to reach the computer. Install
[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) and
run `cloudflared tunnel --url http://localhost:8000`. Put the `https://….trycloudflare.com/webhooks/whatsapp`
address in Meta's webhook settings, with the verify token from the Setup page. Before opening it to
the internet, set `OPSHUB_PASSWORD` in `.env`. For a permanent address, use a named tunnel on your
own domain.

## Moving to the Mac mini

1. Copy the `ops-hub` folder (including `opshub.db` and `.env`) to the Mac mini.
2. Run `./run/install-mac-service.sh`. It starts at login, restarts if it stops, and logs to `logs/`.
3. Set the Mac mini to never sleep.
4. If the Mac mini opens the Setup page at a different address, add that address's
   `/setup/google/callback` to the Google OAuth client. Logins already done carry over in `opshub.db`.
