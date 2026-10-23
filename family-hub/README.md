# Family Hub

One place for the whole family: chores with points and rewards, a shared calendar, homework,
milestones, money, health records, live location with place alerts, and smart-lock door alerts.
It runs as a small Python server plus an installable web app that works on iPhone, Android and desktop.

| Area | What it does |
|---|---|
| **Chores & rewards** | One-off, daily, weekly (pick days) or monthly chores. Kids check them off, parents approve, and points are added to an append-only ledger. Kids spend points in a reward shop. Points are held when a kid requests a reward and refunded if a parent declines. Parents can give bonus points or take points away. |
| **Calendar** | Family events tagged with people. Two-way sync with **Google Calendar** (OAuth) and **iCloud Calendar** (CalDAV), pulled every 15 min. New events can be copied to Google and/or iCloud. |
| **Homework** | Per-kid assignments with due dates and optional bonus points (awarded once). Attach worksheets or photos to **Google Drive**. |
| **Milestones** | A timeline of firsts and big days, with photos saved to Drive. |
| **Money** | Household income and expenses, monthly category budgets (alerts at 80% and when over), kid wallets, and a weekly allowance paid automatically. |
| **Health** | Appointments, medications, allergies, immunizations and growth/vitals per person. Anything with a "next due" date appears in the daily digest. Kids only see their own records. |
| **Location** | Live map, places (geofences) with arrive/leave alerts, battery level. Uses the web app while it's open, or **OwnTracks** for background tracking. |
| **Doors** | Smart-lock status, activity log and lock/unlock. Alerts for a jammed bolt, unlock or open during quiet hours, a door left unlocked or open too long, low battery, and **"nobody's home and the door is unlocked."** |
| **Alerts** | In-app feed plus push (ntfy), SMS (Twilio), Gmail, Google Chat or any webhook, filtered by severity. A daily 5 pm digest covers tomorrow's events, homework, health items due, and anything waiting for approval. |

## Quick start

```bash
cd family-hub
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional for local use; fill in later
uvicorn app.main:app --env-file .env --reload
```

Open http://localhost:8000, choose **New family**, then add kids in **Settings**. Each kid gets a PIN.
On a kid's device, choose **Kid**, enter the family code shown in Settings, then tap their name and enter the PIN.
Interactive API docs are at `/docs`.

**Install on phones:** Location access and home-screen install need **HTTPS**, so deploy before installing
(see below). Then, in Safari on iPhone: Share → *Add to Home Screen*. In Chrome on Android: ⋮ → *Install app*.

Run the tests with `pip install -r requirements-dev.txt && pytest`.

## Deploying

The included `Dockerfile` runs everything in a single container. Data lives in `/data`, so mount a volume there.
Put the container behind HTTPS: Fly.io, Railway, Render, a VPS with Caddy, or a Cloudflare Tunnel to a home server
all work. Then set:

- `FAMILY_HUB_BASE_URL` to the public `https://` URL. Google redirects and webhook URLs are built from it.
- `FAMILY_HUB_SECRET_KEY` to a Fernet key (command in `.env.example`). OAuth tokens and passwords for
  integrations are encrypted with it. Back it up.

Keep **one** server process. The background scheduler runs inside the app.

## Connecting your accounts

### What connects how

| You asked for | How it works here | Why |
|---|---|---|
| Google Calendar | ✅ OAuth, two-way sync | Official API |
| Google Drive | ✅ OAuth, `drive.file` scope (only files this app creates, kept in a "Family Hub" folder) | Official API |
| Gmail | ✅ OAuth, `gmail.send` (sends alert emails; cannot read your inbox) | Reading mail needs a restricted scope that requires a Google security assessment |
| Google Chat | ✅ Alerts posted into a Space through an incoming webhook | Webhooks need a Google Workspace account. Personal Gmail accounts can't add them. |
| Google Messages | ✅ Via SMS (Twilio). Texts arrive in Google Messages. | Google Messages has no public API |
| iPhone Calendar | ✅ iCloud CalDAV with an app-specific password, two-way | Apple has no OAuth calendar API |
| iMessage | ✅ Via SMS (Twilio) to the iPhone's Messages app, or **ntfy** push | iMessage has no public API |
| iPhone/Android push | ✅ ntfy app (free) | Uses ntfy instead of building native apps |
| Find My / Google location sharing | ↪ Use the web app or OwnTracks instead | Neither Find My nor Google location sharing has a public API |
| Philips smart deadbolt | ✅ Through Home Assistant (status and control) or a webhook (status only) | Philips doesn't publish a developer API |

### Google (Calendar, Drive, Gmail)
1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the
   **Google Calendar API**, **Google Drive API** and **Gmail API**.
2. Under *OAuth consent screen*, set up an External app and add your family's Google accounts as **test users**.
   No Google review is needed for a family-sized group of test users.
3. Under *Credentials*, create an **OAuth client ID** of type *Web application*. Set the authorized redirect URI to
   `https://YOUR-HUB/api/integrations/google/callback`.
4. Put the ID and secret in `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and restart the server.
5. In the app, go to **Settings → Connect Google**. Change *Calendar ID* if you want a shared family calendar
   instead of `primary` (find it in Google Calendar → calendar settings → *Integrate calendar*).

### iCloud Calendar (iPhone)
1. At [appleid.apple.com](https://appleid.apple.com), go to *Sign-In and Security → App-Specific Passwords* and generate one.
2. In **Settings → iCloud Calendar**, enter the Apple ID and that password. Optionally enter the calendar's name
   (for example "Family"). Without a name, the first calendar is used.

### Alerts to phones
Go to **Settings → Send alerts somewhere** and pick a minimum severity for each channel:
- **Push (ntfy):** Install the ntfy app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) /
  [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)) and subscribe to a long, random topic
  name. Use the same topic here. You can self-host ntfy and set *Server* if you want full privacy.
- **SMS (Twilio):** Enter the Account SID, auth token, your Twilio number and the family's numbers.
  US numbers need A2P 10DLC registration in Twilio.
- **Email:** Requires Google to be connected. Sends from that account.
- **Google Chat:** In a Space, open *Apps & integrations → Webhooks*, create one and paste its URL here.
- **Webhook:** The hub POSTs JSON `{family, kind, severity, title, body}` to the URL. Use it with Home Assistant,
  IFTTT, Zapier or Pushcut (Pushcut can run iPhone Shortcuts).

Use **Test** on each channel to check it. Kid-only notices, such as "chore approved," stay in the app.

### Background location (OwnTracks)
The web app only shares location while it's open, because iOS doesn't allow web apps to track location in the background.
For always-on tracking, install **OwnTracks** on each phone:
*Settings → Connection → Mode: HTTP*. Set the URL to the location webhook from **Settings → Webhooks** and
replace `<member id>` with that person's ID. OwnTracks sends updates when the person moves significantly, with little battery drain.

On the **Map** tab, add places. Name one **Home** to turn on the "nobody's home and the door is unlocked" alert.

### Philips smart deadbolt
Philips Home Access locks have no public API, so pick one of these:

**A. Home Assistant (full control, recommended).** Matter models such as the Philips 4000 series
(Matter over Thread) pair with Home Assistant's Matter integration. A Thread border router is needed,
for example a HomePod mini, a Nest Hub (2nd gen) or Home Assistant's SkyConnect/ZBT-1.
Wi-Fi models may work with the community [`philips_home_access`](https://community.home-assistant.io/t/philips-easykey/996923)
integration (untested here).
1. In Home Assistant, go to *Profile → Security* and create a **long-lived access token**.
2. In Family Hub, go to **Settings → Home Assistant** and enter your HA URL (Nabu Casa or another URL the
   hub can reach) and the token.
3. In **Doors**, add a lock with *Home Assistant* as the provider and the entity ID, for example `lock.front_door`.
4. Optional, for instant updates instead of the once-a-minute poll, add an HA automation that calls the lock webhook:
   ```yaml
   rest_command:
     family_hub_lock:
       url: "https://YOUR-HUB/api/hooks/YOUR-TOKEN/lock"
       method: POST
       content_type: application/json
       payload: '{"lock": "lock.front_door", "event": "{{ event }}", "actor": "{{ actor }}"}'
   automation:
     - trigger: { platform: state, entity_id: lock.front_door }
       action:
         - service: rest_command.family_hub_lock
           data: { event: "{{ trigger.to_state.state }}", actor: "{{ trigger.to_state.context.user_id or '' }}" }
   ```

**B. Webhook only (status and alerts, no remote control).** Have any automation that can see the lock
(Alexa or Google Home routines through IFTTT, SmartThings, and so on) POST to the lock webhook:
`{"lock": "Front Door", "event": "unlocked", "actor": "Keypad", "battery": 80}`.
Valid events are `locked`, `unlocked`, `jammed`, `door_open`, `door_closed` and `battery_low`.
Locking and unlocking stay in the Philips app.

**C. Demo lock.** Lets you try the Doors tab and the alert rules without hardware.

Tune the rules on the **Doors** tab: minutes before a "left unlocked/open" alert, quiet hours, low-battery
threshold, and whether to alert on every unlock.

### Other automations
`POST /api/hooks/YOUR-TOKEN/alert` with `{"title": "Garage left open", "severity": "warning"}` raises an alert from
any sensor or system. Webhook URLs contain a secret token. Rotate them in Settings if one leaks.

## Privacy & security notes
- Each family's data is isolated in the API, and every endpoint checks family ownership.
- Parents sign in with email and password. Kids sign in with a PIN. Both are hashed with scrypt, and repeated
  failed attempts lock the account out for 15 minutes.
- Kids can't see household finances, door controls, other people's health records, or other people's locations.
- Integration secrets (OAuth tokens, Apple app password, HA token, Twilio token) are encrypted at rest.
- Location history stays on your server. Nothing is sent to third parties except the alerts you route
  through the channels you configure.

## Project layout
```
app/
  main.py            FastAPI app, static files, background scheduler
  models.py          SQLAlchemy models (one SQLite file by default)
  routers/           auth, members, chores+rewards, calendar, homework/milestones/health, finances,
                     location+locks+webhooks, alerts+integrations+dashboard
  services/          google, icloud, locks, geo, chores, notify, scheduler
static/              installable web app (vanilla JS, no build step)
tests/               pytest suite (outbound HTTP is mocked)
```
