"""What the Setup page asks for, system by system.

Each field: (setting name, label, secret, help). Secrets are never sent back to the browser,
only a masked hint.
"""
from __future__ import annotations

SECTIONS = [
    {
        "key": "google", "title": "Google Workspace", "connectors": ["google_drive", "google_sheets", "gmail",
                                                                     "google_calendar"],
        "intro": ("One login covers Sheets, Drive, Gmail and Calendar, plus the RQ, Muse and import folders. "
                  "Create a Web application OAuth client in Google Cloud with redirect URI "
                  "{base}/setup/google/callback, paste its ID and secret, save, then click Sign in with Google. "
                  "Then click Set up Drive & Sheets to create the folders, sheets and calendar."),
        "actions": [{"id": "google_login", "label": "Sign in with Google", "href": "/setup/google/start"},
                    {"id": "google_init", "label": "Set up Drive & Sheets", "post": "/api/setup/google/init"}],
        "fields": [
            ("GOOGLE_OAUTH_CLIENT_ID", "OAuth client ID", False, "Google Cloud > Credentials > OAuth client ID"),
            ("GOOGLE_OAUTH_CLIENT_SECRET", "OAuth client secret", True, ""),
            ("GOOGLE_SHEETS_MASTER_ID", "Master data sheet ID", False, "Filled in by Set up Drive & Sheets"),
            ("GOOGLE_SHEETS_REPORT_ID", "Report sheet ID", False, "Filled in by Set up Drive & Sheets"),
            ("GOOGLE_DRIVE_FOLDER_ID", "Invoices & documents folder ID", False, "Filled in by Set up Drive & Sheets"),
            ("GOOGLE_CALENDAR_ID", "Calendar ID", False, "Filled in by Set up Drive & Sheets"),
            ("GMAIL_QUERY", "Mail to read", False, "Gmail search, e.g. newer_than:14d (from:3pl.com OR subject:(PRO OR BOL OR invoice))"),
            ("GMAIL_TO", "Email the digest to", False, "Comma-separated addresses"),
        ],
    },
    {
        "key": "shopify", "title": "Shopify", "connectors": ["shopify"],
        "intro": ("Admin > Settings > Apps > Develop apps (or the Dev Dashboard): create an app with read_orders, "
                  "read_all_orders, read_inventory, read_locations, read_fulfillments; install it; paste the Admin "
                  "API access token. Until then: export orders/inventory CSVs into the Shopify exports folder "
                  "or use Import file."),
        "fields": [
            ("SHOPIFY_SHOP", "Store domain", False, "yourstore.myshopify.com"),
            ("SHOPIFY_ACCESS_TOKEN", "Admin API access token", True, "starts with shpat_"),
            ("SHOPIFY_SALES_LOCATION_ID", "Location online sales draw from", False,
             "Shopify location gid, or a location code from the master sheet"),
            ("SHOPIFY_IMPORT_FOLDER_ID", "Exports folder ID", False, "Filled in by Set up Drive & Sheets"),
        ],
    },
    {
        "key": "lightspeed", "title": "Lightspeed Retail (R-Series)", "connectors": ["lightspeed"],
        "intro": ("Register an API client with Lightspeed with redirect URI {base}/setup/lightspeed/callback, paste "
                  "its ID and secret, save, then click Connect Lightspeed. Until then: export item and sales "
                  "reports into the Lightspeed exports folder or use Import file."),
        "actions": [{"id": "lightspeed_login", "label": "Connect Lightspeed", "href": "/setup/lightspeed/start"}],
        "fields": [
            ("LIGHTSPEED_CLIENT_ID", "API client ID", False, ""),
            ("LIGHTSPEED_CLIENT_SECRET", "API client secret", True, ""),
            ("LIGHTSPEED_ACCOUNT_ID", "Account ID", False, "Filled in by Connect Lightspeed"),
            ("LIGHTSPEED_DEFAULT_SHOP", "Shop for exports without a shop column", False, "shopID or shop name"),
            ("LIGHTSPEED_IMPORT_FOLDER_ID", "Exports folder ID", False, "Filled in by Set up Drive & Sheets"),
        ],
    },
    {
        "key": "extensiv", "title": "Extensiv (partner warehouse)", "connectors": ["extensiv"],
        "intro": ("The 3PL issues these. Until then: export orders and stock-by-lot reports from their Extensiv "
                  "portal into the Extensiv exports folder or use Import file."),
        "fields": [
            ("EXTENSIV_CLIENT_ID", "Client ID", False, ""),
            ("EXTENSIV_CLIENT_SECRET", "Client secret", True, ""),
            ("EXTENSIV_USER_LOGIN_ID", "User login ID", False, ""),
            ("EXTENSIV_CUSTOMER_ID", "Customer ID", False, "Your account in their WMS"),
            ("EXTENSIV_FACILITY_ID", "Facility ID(s)", False, "Comma-separated"),
            ("EXTENSIV_TPL_KEY", "3PL key (tpl)", True, "Only if they give you one"),
            ("EXTENSIV_VENDOR_NAME", "Warehouse name on the rate card", False, "Must match RateCard vendor"),
            ("EXTENSIV_IMPORT_FOLDER_ID", "Exports folder ID", False, "Filled in by Set up Drive & Sheets"),
        ],
    },
    {
        "key": "rq", "title": "iQmetrix RQ (report files)", "connectors": ["iqmetrix_rq"],
        "intro": ("No API needed. Schedule RQ's inventory and sales reports to the ops mailbox (set the search "
                  "below) or save them into the RQ reports folder."),
        "fields": [
            ("RQ_DRIVE_FOLDER_ID", "RQ reports folder ID", False, "Filled in by Set up Drive & Sheets"),
            ("RQ_GMAIL_QUERY", "Report emails search", False, "e.g. from:(iqmetrix) subject:(Inventory OR Sales) newer_than:3d"),
            ("RQ_DEFAULT_STORE", "Store for reports without a store column", False, ""),
        ],
    },
    {
        "key": "muse", "title": "Muse AI", "connectors": ["muse"],
        "intro": "Share both folders with Muse and give it MUSE_HANDOFF.md.",
        "fields": [
            ("MUSE_DRIVE_OUTBOX_FOLDER_ID", "Ops Hub → Muse folder ID", False, "Filled in by Set up Drive & Sheets"),
            ("MUSE_DRIVE_INBOX_FOLDER_ID", "Muse → Ops Hub folder ID", False, "Filled in by Set up Drive & Sheets"),
        ],
    },
    {
        "key": "google_chat", "title": "Google Chat", "connectors": ["google_chat"],
        "intro": "In the space: Apps & integrations > Webhooks > Add webhook.",
        "fields": [("GOOGLE_CHAT_WEBHOOK_URL", "Webhook URL", True, "")],
    },
    {
        "key": "telegram", "title": "Telegram", "connectors": ["telegram"],
        "intro": ("Create a bot with @BotFather (/newbot), paste the token and save, add the bot to your ops group, "
                  "send /start there, then click Find chats."),
        "actions": [{"id": "telegram_chats", "label": "Find chats", "get": "/api/setup/telegram/chats"}],
        "fields": [
            ("TELEGRAM_BOT_TOKEN", "Bot token", True, ""),
            ("TELEGRAM_CHAT_ID", "Alerts chat ID", False, "Group ids start with -"),
            ("TELEGRAM_STAFF_CHAT_IDS", "More staff chats", False, "Comma-separated; full access"),
            ("TELEGRAM_FREIGHT_CHAT_IDS", "Driver/agent chats", False, "Can only post shipment updates"),
        ],
    },
    {
        "key": "whatsapp", "title": "WhatsApp (Cloud API)", "connectors": ["whatsapp"],
        "intro": ("Meta developer app with WhatsApp: permanent system-user token and phone number ID. Alerts need "
                  "an approved template with one {{1}} variable. For replies, set the webhook to "
                  "{base}/webhooks/whatsapp (needs a public https address) with the verify token below."),
        "fields": [
            ("WHATSAPP_TOKEN", "Access token", True, ""),
            ("WHATSAPP_PHONE_NUMBER_ID", "Phone number ID", False, ""),
            ("WHATSAPP_TO", "Staff numbers for alerts", False, "Comma-separated, international format"),
            ("WHATSAPP_TEMPLATE", "Alert template name", False, "e.g. ops_alert"),
            ("WHATSAPP_VERIFY_TOKEN", "Webhook verify token", True, "Any string; enter the same in Meta"),
            ("WHATSAPP_APP_SECRET", "App secret", True, "App settings > Basic"),
            ("WHATSAPP_FREIGHT_NUMBERS", "Carrier/driver numbers", False, "Their texts become shipment updates"),
        ],
    },
    {
        "key": "ai", "title": "AI", "connectors": ["claude", "gemini", "openai"],
        "intro": "Any of these. Tried in order; built-in rules cover the rest.",
        "fields": [
            ("ANTHROPIC_API_KEY", "Claude API key", True, "console.anthropic.com"),
            ("GEMINI_API_KEY", "Gemini API key", True, "aistudio.google.com"),
            ("OPENAI_API_KEY", "OpenAI API key", True, "platform.openai.com"),
        ],
    },
    {
        "key": "api", "title": "Read-only API key (Muse, ChatGPT, Gemini, Sheets)", "connectors": [],
        "intro": ("Lets other tools read Ops Hub's data without the dashboard password: send it as "
                  "\"Authorization: Bearer <key>\" on any GET under {base}/api/ (schema: {base}/openapi.json), or add "
                  "?token=<key> to a CSV export URL for Google Sheets IMPORTDATA. It can read, never change anything. "
                  "Creating a new key replaces the old one."),
        "actions": [{"id": "read_token", "label": "Create a new key", "post": "/api/setup/read-token"}],
        "fields": [
            ("OPSHUB_READ_TOKEN", "Read-only API key", True, "Use Create a new key, or paste 24+ random characters"),
        ],
    },
]

ALL_KEYS = {f[0] for s in SECTIONS for f in s["fields"]}
SECRET_KEYS = {f[0] for s in SECTIONS for f in s["fields"] if f[2]}


def mask(value: str) -> str:
    return "••••" + value[-4:] if len(value) > 8 else "••••"
