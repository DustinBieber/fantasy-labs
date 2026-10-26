# Ops Hub on a Mac mini, MacBook Air and Galaxy S24 Ultra

| Device | Its job |
| --- | --- |
| **Mac mini** | The server. Runs Ops Hub around the clock: syncing every 15 minutes, sending alerts, answering the Telegram bot, the 6:00 a.m. Muse handoff, nightly backups. Stays plugged in and awake. |
| **MacBook Air** | Where you set things up and do desk work: the dashboard, invoice audits, the pick planner, Screen Sharing into the Mac mini, dropping export files into Drive. |
| **S24 Ultra** | Alerts (Telegram / WhatsApp / Google Chat), the dashboard as a home-screen app, asking the bot ("order #2041"), and passing on carrier updates. |

**Tailscale** (free) links all three privately, so the MacBook and phone reach the Mac mini from
the office, home or the road without opening it to the internet.

Only the Mac mini runs Ops Hub. Never run a second copy on the MacBook against the same accounts:
both would send every alert and fight over the Telegram bot.

---

## Part 1: Prepare the Mac mini (20 min, at the Mac mini once)

1. **Update macOS** (System Settings → General → Software Update) and log in as your admin user.
2. **Name it and allow remote access**: System Settings → General → **Sharing**:
   * **Local hostname** → Edit → `opshub-mini`
   * Turn on **Screen Sharing**
3. **Keep it awake**: System Settings → **Energy**:
   * *Prevent automatic sleeping when the display is off*: **on**
   * *Wake for network access*: **on**
   * *Start up automatically after a power failure*: **on**
4. Plug it into the router with an **Ethernet cable** if you can.
5. **Install the tools.** Open **Terminal** and paste the install line from [brew.sh](https://brew.sh),
   then:
   ```bash
   brew install python@3.12 git gh
   ```
6. **Install Tailscale** from the Mac App Store and sign in with **dustin@jocordistro.com**.
   Set the machine name to `opshub-mini` (Tailscale menu → this device).

From here on you can do everything from the MacBook Air. In Finder → **Network** → `opshub-mini` →
**Share Screen**, or press ⌘K in Finder and enter `vnc://opshub-mini.local`.

## Part 2: Install Ops Hub on the Mac mini (10 min)

In Terminal on the Mac mini:

```bash
gh auth login                          # GitHub: choose "Login with a web browser"
cd ~ && gh repo clone DustinBieber/fantasy-labs -- -b ccr-a698dc74-4nq2oa
cd ~/fantasy-labs/ops-hub && ./run/setup-mac.sh
```

`setup-mac.sh` does the rest:

1. Asks you to choose a dashboard password (8+ characters, no spaces).
2. Asks for your Mac login password once.
3. Installs Ops Hub as a service that starts with the Mac and is reachable from the MacBook and
   phone. The first run takes a minute or two.
4. Opens **http://localhost:8000/#setup**. Log in as `ops` with the dashboard password.

If macOS asks whether *python* may accept incoming connections, click **Allow**. You'll see sample
data until your systems are connected.

## Part 3: Connect your systems (about an hour)

Follow **[CONNECT-TODAY.md](CONNECT-TODAY.md)**. One rule matters for these devices:

* **The Google and Lightspeed sign-in buttons must be clicked on the Mac mini, at
  `http://localhost:8000`.** Google only accepts `localhost` for this kind of login. From the MacBook,
  open Screen Sharing to `opshub-mini`, open Safari *on the Mac mini*, go to
  `http://localhost:8000/#setup`, and click **Sign in with Google** → **Set up Drive & Sheets**, then
  **Connect Lightspeed**.
* Everything else (pasting tokens, **Test** buttons, master sheet) works straight from the MacBook at
  `http://opshub-mini.local:8000/#setup`.

When the Connectors tab shows everything as **Live** or **Live: export files**, switch off sample
data. Edit `.env` on the Mac mini to set `DEMO_MODE=false`, then restart:

```bash
sudo launchctl kickstart -k system/com.opshub.server
```

## Part 4: MacBook Air

1. **Install Tailscale** (Mac App Store) and sign in with the same account. Now
   **http://opshub-mini:8000** works anywhere. On the office network, `http://opshub-mini.local:8000`
   also works.
2. Open it in Safari, log in, then **File → Add to Dock** so Ops Hub opens like an app.
3. Install **Google Drive for desktop** and sign in as dustin@jocordistro.com. The Ops Hub folders
   then show in Finder:
   * Drop Shopify, Lightspeed and Extensiv exports into **Ops Hub/Imports/…**, RQ reports into
     **Imports/RQ reports**, and 3PL invoices into **3PL invoices & documents**. They're picked up
     within 15 minutes.
   * Or use **Import file** on the Connectors tab.
4. Keep a Screen Sharing shortcut to `opshub-mini` for maintenance (Part 7).

## Part 5: Galaxy S24 Ultra

1. **Install** from the Play Store: Tailscale, Telegram, Google Drive, and WhatsApp and Google Chat
   if you'll use them.
2. **Tailscale**: sign in with the same account and switch it on.
3. **Ops Hub on the home screen**:
   1. In Chrome, open `http://opshub-mini:8000` and log in.
   2. Tap **⋮ → Add to home screen → Install**.

   The Ops Hub icon opens it full-screen with no browser bars. Every tab works on the phone.
4. **Alerts that actually arrive.** Samsung delays background apps to save battery:
   * Settings → Apps → **Telegram** → Battery → **Unrestricted**. Same for WhatsApp, Google Chat
     and Tailscale.
   * Settings → Battery → Background usage limits → **Never auto sleeping apps** → add the same apps.
   * In Telegram, open the ops group → ⋮ → Notifications: **on**, a distinct sound, and pin the group.
5. **Ask the bot** in the Telegram ops group:
   * `alerts`
   * `order #2041`
   * `stock CBC-32`
   * `ship 4471983` (a shipment ref, tracking or PRO number)
   * `lots`
   * `digest`

   One-time step: in **@BotFather** send `/setprivacy` → pick the bot → **Disable**, or it won't see
   plain messages in groups.
6. **Freight updates on the go.** When a driver or agent texts you, forward or paste it into the ops
   group. As long as it contains the PRO, tracking or BOL number, the bot logs it on the right
   shipment, handoffs to a new carrier included. In the app you can also open **Freight** →
   the shipment → **Log an update**, or **Book shipment** for new freight.
7. **Files from the phone.** From an email attachment, tap **Share → Drive → Ops Hub/Imports/…**
   (or *3PL invoices & documents*). Or use **Import file** in the app.
8. **WhatsApp**: alerts come *to* your normal WhatsApp *from* the business number. The business
   (Cloud API) number has to be a separate phone number that isn't used in any WhatsApp app. Don't
   use the S24's own number for it.

## Part 6: WhatsApp replies and carrier texts (optional, 10 min)

Meta needs a public `https` address to deliver WhatsApp messages to the Mac mini. Tailscale Funnel
provides one for free. On the Mac mini:

```bash
tailscale funnel --bg 8000
```

If `tailscale` isn't found, use the Tailscale menu → Settings → enable the command-line tool. Approve
Funnel in the Tailscale admin page if asked. It prints an address like
`https://opshub-mini.tailXXXX.ts.net`.

* In Meta → WhatsApp → Configuration, set the webhook to `https://opshub-mini.tailXXXX.ts.net/webhooks/whatsapp`,
  with the verify token from the Setup page, and subscribe to **messages**.
* Funnel also makes the dashboard reachable from the internet, so the password from Part 2 matters.
  Keep it long.
* Optional: add `OPSHUB_PUBLIC_URL=https://opshub-mini.tailXXXX.ts.net` to `.env`, then restart.

## Part 7: Day to day and maintenance

**What happens on its own** (Hawaii time):

| When | What |
| --- | --- |
| Every 15 min | Sync, check rules, send new alerts |
| Every ~20 s | Answer the Telegram bot |
| 2:30 a.m. | Database backup |
| 6:00 a.m. | Muse brief and data file |
| 7:00 a.m. | Daily digest |

**Common commands**, in Terminal on the Mac mini (or via Screen Sharing):

```bash
cd ~/fantasy-labs/ops-hub
./run/update-mac.sh                                   # get the latest version and restart
tail -f logs/opshub.log                               # watch what it's doing (Ctrl+C to stop)
sudo launchctl kickstart -k system/com.opshub.server  # restart
.venv/bin/python -m opshub backup                     # back up right now
sudo ./run/install-mac-service.sh uninstall           # stop and remove the service
```

**Backups**: the last 14 nightly copies are in `~/fantasy-labs/ops-hub/backups`. Turn on Time
Machine on the Mac mini. For an off-site copy, set `BACKUP_DIR` in `.env` to a folder inside
Google Drive for desktop. To restore:

1. `sudo ./run/install-mac-service.sh uninstall`
2. Copy a backup over `opshub.db`.
3. Run the install command from Part 2 again.

## If the Mac mini isn't ready today

Run Ops Hub on the MacBook Air for now: double-click `run/start-mac.command` (after Part 1 steps 5–6
on the MacBook). Keep the lid open, plugged in, and set to not sleep.

To move it later:

1. Close the Terminal window to stop it.
2. Copy the whole `ops-hub` folder, including `opshub.db` and `.env`, to `~/fantasy-labs/ops-hub` on
   the Mac mini.
3. Run the install command from Part 2.

All logins and data come with it.

## When something's off

| Symptom | Fix |
| --- | --- |
| `opshub-mini.local` won't open | Not on the office network? Use `http://opshub-mini:8000` with Tailscale on. Still nothing: check `logs/opshub.log` on the Mac mini. |
| Google says `redirect_uri_mismatch` | The sign-in wasn't done at `http://localhost:8000` on the Mac mini. Use Screen Sharing (Part 3). |
| Google says *access blocked by admin* | Google Admin → Security → API controls → mark the Ops Hub OAuth client as trusted. |
| Phone alerts arrive late | Battery settings in Part 5, step 4. |
| Bot ignores plain messages in the group | `/setprivacy` → Disable in @BotFather. |
| A connector says *error* | Setup tab → **Test** on that system shows the exact reason. |
