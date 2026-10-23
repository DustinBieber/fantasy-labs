# Family Hub

One place for the whole family: chores with points and rewards, a shared calendar, homework,
milestones, money, health records, live location with arrive/leave alerts, and smart-lock door alerts.
It's a small server that runs **free on an always-on Mac**, plus a web app that family members add to
their iPhone or Android home screen. **Nothing to buy and no App Store apps.**

| Area | What it does |
|---|---|
| **Chores & rewards** | One-off, daily, weekly or monthly chores. Kids check them off, parents approve, points are added. Kids spend points in a reward shop (points held while waiting, refunded if declined). Parents can give bonus points or take points away. |
| **Calendar** | Family events tagged with people. Two-way sync with **Google Calendar** and **iCloud Calendar**. |
| **Homework** | Per-kid assignments, due dates, optional bonus points, photo or file attachments. |
| **Milestones** | A timeline of firsts and big days, with photos. |
| **Money** | Household income and expenses, monthly budgets (alerts at 80% and when over), kid wallets, automatic weekly allowance. |
| **Health** | Appointments, medications, allergies, immunizations, growth and vitals. "Next due" dates appear in the daily digest. Kids only see their own records. |
| **Location** | Live map, places with arrive/leave alerts, "who's home." |
| **Doors** | Smart-lock status, activity log, lock/unlock. Alerts for a jammed bolt, unlock or open during quiet hours, a door left unlocked or open too long, low battery, and **"nobody's home and the door is unlocked."** |
| **Alerts** | **Free push notifications** to each phone, **iMessage** from the Mac, Gmail, and a daily summary of tomorrow. |

## What's free, and how each connection works

| You asked for | How it works | Cost | Extra apps on phones |
|---|---|---|---|
| Phone notifications (iPhone & Android) | **Web Push** to the home-screen app (iPhone needs iOS 16.4+) | Free | None |
| iMessage | The Mac's **Messages** app sends them | Free | None |
| Text messages to Android / Google Messages | The Mac's Messages app sends SMS through an iPhone with *Text Message Forwarding* turned on. Android phones also get Web Push. | Free (normal texting plan) | None |
| Google Calendar | Two-way sync (OAuth) | Free | None |
| Google Drive | Attachments saved to a "Family Hub" folder (app can only see files it creates) | Free | None |
| Gmail | Alert emails sent from your Gmail account (can't read your inbox) | Free | None |
| Google Chat | ❌ Not possible on personal Gmail: Google only allows Chat webhooks on paid Workspace accounts. Push and iMessage cover the same need. | — | — |
| iPhone Calendar | iCloud Calendar sync with an app-specific password | Free | None |
| iCloud Drive | Attachments can be saved in iCloud Drive on the Mac, so they appear in the Files app on everyone's iPhone | Free | None |
| Location | ① Home Wi-Fi presence detected by the Mac ② built-in iPhone **Shortcuts** arrive/leave automations ③ live location while the app is open | Free | None (Shortcuts is built in) |
| Find My / Google location sharing | ❌ No public access for other apps. ①–③ above replace them. | — | — |
| Philips smart deadbolt | **Apple Home** (Matter models such as the 4000 series): the Mac runs Shortcuts to lock and unlock, and Home automations report status instantly. Other models: webhook or Home Assistant. | Free | None |
| HTTPS address for phones | **Tailscale Funnel** on the Mac gives a permanent `https://…ts.net` address | Free | None (only the Mac runs Tailscale) |

The one piece of extra software is **Tailscale, on the Mac only**. Phones only allow a web app to install,
send notifications and share location when it's served over `https://`. Tailscale Funnel provides that
for free without buying a domain. The phones themselves only need Safari or Chrome.

## Set up on your Mac mini (about 20 minutes)

1. **Get the code onto the Mac.** In Terminal:
   ```bash
   git clone https://github.com/dustinbieber/fantasy-labs.git ~/fantasy-labs
   ```
   You can also download the ZIP from GitHub and unzip it into your home folder.
2. **Install Tailscale** from [tailscale.com/download/mac](https://tailscale.com/download/mac) and sign in.
   The Personal plan is free.
3. **Run the installer:**
   ```bash
   bash ~/fantasy-labs/family-hub/mac/setup.sh
   ```
   It installs Python if needed, copies the app to `~/FamilyHub`, and sets it to start automatically and
   restart if it crashes. It turns on Tailscale Funnel (open the link it prints if Funnel needs enabling),
   offers to turn off sleep, and prints your address, such as `https://mac-mini.tail1234.ts.net`.
4. **Keep the Mac logged in.** Go to System Settings → Users & Groups → *Automatically log in as…*. This needs
   FileVault off. If you keep FileVault on, log in once after every restart. The hub runs in your user
   session so it can use Messages and Shortcuts.
5. **Open your address**, choose **New family**, and add the kids (each gets a PIN) in **Settings**.

To update later, pull the new code (`git -C ~/fantasy-labs pull`) and run `setup.sh` again.
To uninstall, run `mac/uninstall.sh`; your data in `~/FamilyHub` is kept.
Logs are in `~/FamilyHub/logs/server.log`. Daily database backups go to `~/FamilyHub/backups` (the last 14 are kept).

### On each family member's phone
1. Open the hub address in **Safari** (iPhone) or **Chrome** (Android).
   Kids tap **Kid**, enter the family code from Settings, and use their PIN.
2. iPhone: Share → **Add to Home Screen**. Android: ⋮ → **Install app**. From then on, open it from the icon.
3. In the app go to **Settings → Turn on notifications** and allow them. A test notification arrives straight away.

### macOS permission prompts (one time, on the Mac's screen)
The first time the hub sends an iMessage, runs a Shortcut, scans the Wi-Fi, or saves into iCloud Drive, macOS asks
whether "python" may do so. Click **OK / Allow**. If you missed one, go to System Settings → Privacy & Security →
**Automation** (Messages), **Local Network**, or **Files & Folders**, and turn it on for python.

## Connecting things

### iMessage and text alerts (free, from the Mac)
1. On the Mac, open **Messages** and sign in with an Apple ID (a family one is fine).
2. To reach Android phones by SMS: on an iPhone signed into the same Apple ID, go to Settings → Apps → Messages →
   **Text Message Forwarding** and turn on this Mac.
3. In the hub go to **Settings → Send alerts somewhere → iMessage / text from the Mac**. Enter phone numbers or Apple ID
   emails and choose which alerts (Warning+ is a good default). Press **Test** and approve the macOS prompt.

The hub tries iMessage first and falls back to SMS automatically.

### Location without extra apps
Open the **Map** tab:
1. **Add places.** Tap the map or type coordinates. Name one **Home**.
2. **Home Wi-Fi presence (iPhone and Android).** Turn on *Detect phones on home Wi-Fi* and add each person's phone
   Wi-Fi address. On iPhone: Settings → Wi-Fi → ⓘ → *Private Wi-Fi Address: Fixed*, then copy *Wi-Fi Address*.
   *Show devices on the network now* helps you find them.
   A phone unseen for 15 minutes counts as away.
3. **iPhone Shortcuts (built in).** The Map tab shows step-by-step instructions with ready-to-copy links for each
   person and place. Use *Arrive* and *Leave* automations set to *Run Immediately*. They work anywhere, such as school
   or work, not just at home.
4. While the hub app is open, it also shares the exact location.

### Philips smart deadbolt
Which route depends on your model:

**Matter models (e.g. Philips 4000 series): Apple Home, free, full control**
1. Add the lock in the **Home** app on your iPhone (scan the Matter code). A HomePod or Apple TV acts as the
   home hub and Thread border router.
2. On the Mac, open **Shortcuts** and create `Lock Front Door` (Home action → Front Door → Lock) and
   `Unlock Front Door`. Optionally create `Status Front Door` (*Get state of* Front Door → Current Lock State) so the
   hub can check every minute.
3. In the hub go to **Doors → Add a lock**, choose *Apple Home*, and enter Lock ID `Front Door` (your lock's name in Home).
4. For instant alerts, go to Home app → Automation → **+** → *An Accessory is Controlled* → Front Door → *Unlocks* →
   *Convert to Shortcut* → add **Get Contents of URL** with the link shown on the Doors tab
   (`…/lock?lock=Front%20Door&event=unlocked`). Repeat for *Locks* (`event=locked`).

Apple's Shortcuts Home actions on the Mac were not tested here. If your Mac's Shortcuts app doesn't offer
them, use step 4 for status and alerts, and lock or unlock from the Home or Philips app.

**Wi-Fi-only models (Philips Home Access app)**
- Status and alerts: any automation that sees the lock opens the lock webhook URL
  (`?lock=Front%20Door&event=unlocked`, or a POST with JSON `{"lock","event","actor","battery"}`).
- Full control: [Home Assistant](https://www.home-assistant.io/installation/macos) runs free on the Mac (in a
  UTM virtual machine). The community [`philips_home_access`](https://community.home-assistant.io/t/philips-easykey/996923)
  integration may support these locks but is untested. Connect it under **Settings → Home Assistant** and add the lock with
  the *Home Assistant* provider. An HA automation can call the lock webhook for instant updates.

Valid events are `locked`, `unlocked`, `jammed`, `door_open`, `door_closed` and `battery_low`.
Tune quiet hours, the "left unlocked" timing and the battery threshold on the **Doors** tab.

### Google Calendar, Drive and Gmail (free)
1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the **Google Calendar API**,
   **Google Drive API** and **Gmail API**.
2. Under **OAuth consent screen**, choose External, fill in the app name and your email, and add the three scopes.
   Then set **Publishing status → In production**. Without this, Google disconnects you every 7 days.
   You don't need to submit for verification. When you connect, Google shows "Google hasn't verified this app";
   click **Advanced → Go to Family Hub**. That's expected for a private family app.
3. Under **Credentials**, create an **OAuth client ID** (*Web application*). Set the authorized redirect URI to
   `https://YOUR-MAC.ts.net/api/integrations/google/callback`.
4. Put the ID and secret into `~/FamilyHub/family-hub.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) and re-run `setup.sh`.
5. In the hub, go to **Settings → Connect Google**. You can also choose whether attachments go to Drive or iCloud Drive there.

### iCloud Calendar
At [appleid.apple.com](https://appleid.apple.com), go to *Sign-In and Security → App-Specific Passwords* and create one.
Enter your Apple ID and that password in **Settings → iCloud Calendar**.

### Other automations
`…/api/hooks/TOKEN/alert` (POST `{"title": "Garage left open", "severity": "warning"}`) raises an alert from any
sensor or system. Webhook links contain a secret token. Rotate them in Settings if one leaks.

## Optional extras (not needed)
- **ntfy, Twilio, Google Chat (Workspace), generic webhooks** are still available as alert channels.
- **OwnTracks** gives continuous background GPS if Wi-Fi and Shortcuts aren't enough.
- **Hosting elsewhere:** the included `Dockerfile` runs on any server. iMessage, Shortcuts and Wi-Fi presence need the Mac.

## Privacy & security
- Your data stays on your Mac. Nothing is sent to third parties except the notifications, emails and calendar syncs you set up.
- Parents sign in with email and password, kids with a PIN. Both are hashed with scrypt, and repeated wrong guesses lock the account out for 15 minutes.
- Kids can't see household finances, door controls, or anyone else's health records or location.
- Connected-account secrets are encrypted at rest with the key in `~/FamilyHub/family-hub.env`. Back that file up.
- Attachment links are signed and unguessable.
- The hub is reachable from the internet through Tailscale Funnel, protected by the sign-in.

## For developers
```bash
cd family-hub && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pytest          # 51 tests, outbound calls mocked
uvicorn app.main:app --reload                           # http://localhost:8000, API docs at /docs
```
```
app/            FastAPI app: models, routers (auth, chores, calendar, records, finances, home, hub, push)
app/services/   google, icloud, push, macos (Messages/Shortcuts), presence (Wi-Fi), geo, locks, files, notify, scheduler
static/         installable web app (vanilla JS, no build step) + service worker
mac/            setup.sh / uninstall.sh for an always-on Mac
```
