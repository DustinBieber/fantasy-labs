# Family Hub setup: Mac mini + MacBook Air + Galaxy S24 Ultra

This guide sets the hub up on these three devices:

| Device | Its job |
|---|---|
| **Mac mini** | The hub. It runs 24/7: stores everything, sends alerts, checks the lock, runs reminders. |
| **MacBook Air** | Your setup and admin computer. Pasting long codes and links is easier here. It can also get alerts. |
| **Galaxy S24 Ultra** | The everyday app: notifications, chores, photos, medicine reminders, location, door lock. |

Plan on about **1 hour** in total. Do the parts in order. Menu names can differ slightly between macOS
and One UI versions; the guide uses macOS 15 (Sequoia) and One UI 6/7.

---

## Part 1: Mac mini (about 30 minutes)

### 1.1 Make the Mac mini an always-on server
1. **System Settings → General → Software Update**: install any updates and restart.
2. **System Settings → Energy** (on older macOS: *Energy Saver*):
   - Turn **on** "Prevent automatic sleeping when the display is off".
   - Turn **on** "Start up automatically after a power failure".
   - Turn **on** "Wake for network access".
3. **System Settings → Users & Groups → Automatically log in as** → choose your account.
   - If it's greyed out, FileVault is on (System Settings → Privacy & Security → FileVault).
     Either turn FileVault off, or log in by hand after every restart. The hub only runs while you're logged in.
4. Keep the Mac mini on **ethernet or strong Wi-Fi** at home.

### 1.2 Install Tailscale (gives the hub a free, secure web address)
Phones only let a web app install, notify and use location over `https://`. Tailscale gives the Mac mini a
permanent `https://` address for free. Nothing needs to be installed on the S24.
1. On the Mac mini, go to **https://tailscale.com/download/mac** and download the **standalone** version
   (not the App Store one), then install it.
2. Open Tailscale from the menu bar, then **Log in**. Your Google account works. The Personal plan is free.
3. Tailscale menu → **Settings** → turn on **Launch at login** (or "Run at login").

### 1.3 Download Family Hub
1. On the Mac mini, open Safari and go to **https://github.com/dustinbieber/fantasy-labs**, signed in to GitHub.
2. Click the green **Code** button → **Download ZIP**.
3. Open **Terminal** (Spotlight: ⌘ Space, type *Terminal*) and paste:
   ```bash
   cd ~ && unzip -q ~/Downloads/fantasy-labs-main.zip && mv fantasy-labs-main fantasy-labs
   ```
   If you prefer git, use `git clone https://github.com/dustinbieber/fantasy-labs.git ~/fantasy-labs` instead.
   macOS may offer to install "command line developer tools" first; click Install.

### 1.4 Run the installer
In Terminal:
```bash
bash ~/fantasy-labs/family-hub/mac/setup.sh
```
It will:
- Install a private copy of Python if your Mac's is too old (no password needed).
- Ask about saving attachments in iCloud Drive. Answer **N**: without an iPhone, the Mac's own folder is simpler.
- Start the hub as a background service that starts at login and restarts itself.
- Turn on **Tailscale Funnel**. If it prints a link that says Funnel isn't enabled, open the link, approve, and run the
  same command again.
- Ask to turn off sleep. Answer **y** and enter your Mac password.
- Print your hub address, e.g. **`https://mac-mini.tail1234.ts.net`**. Write it down; it's permanent.

### 1.5 Create your family
1. On the Mac mini, open the address in Safari.
2. Tap **New family** and enter the family name, your name, email and a password.
3. **Settings → Add a family member** for each person:
   - Kids get a **PIN**, an optional weekly allowance and a color.
   - Another parent gets an email and password.
4. Note the **family code** shown in Settings; kids use it to sign in.

### 1.6 Allow the macOS permission prompts
Over the next day, small macOS boxes may appear on the Mac mini's screen saying "python … would like to …".
Click **Allow / OK** every time; this happens only once per permission. If you clicked Don't Allow by mistake, go to
System Settings → Privacy & Security → **Local Network** (for Wi-Fi presence) or **Automation** and switch python on.

---

## Part 2: Galaxy S24 Ultra (about 10 minutes)

### 2.1 Install the app
1. Open **Chrome**, not Samsung Internet, and go to your hub address.
2. Sign in: **Parent** with email and password, or **Kid** with the family code and PIN.
3. Tap **⋮** (top right) → **Add to home screen** → **Install**. A **Family Hub** icon appears.
4. **From now on, always open it from that icon.**

### 2.2 Turn on notifications
1. Open Family Hub → **More → Settings** → **🔔 Turn on notifications** → **Allow**.
2. A test notification arrives straight away. If it doesn't, check
   **Settings → Notifications → App notifications** and make sure **Family Hub** and **Chrome** are on.

### 2.3 Stop Samsung from delaying alerts (important)
Samsung's battery saver can hold notifications for minutes or hours.
1. **Settings → Apps → Family Hub → Battery** → choose **Unrestricted**. Do the same for **Chrome**.
2. **Settings → Battery → Background usage limits** (on some versions: *Device care → Battery*):
   - Make sure **Family Hub** and **Chrome** are **not** under *Sleeping apps* or *Deep sleeping apps*.
   - Add both to **Never sleeping apps**.
3. Optional: in Family Hub → **More → Doors**, use a demo lock (Part 4.6) to trigger a test alert with the screen off.

### 2.4 Location
- **While the app is open:** Map tab → **📍 Share my location** → **Allow while using the app**.
- **Automatically, with the app closed:** set up **Home Wi-Fi presence** (Part 4.4). For other Android phones in the family,
  use **Google Maps location sharing** (Part 4.5).

### 2.5 Camera (for chore photos)
The first time you tap a 📷 chore, allow camera access for Chrome / Family Hub.

---

## Part 3: MacBook Air (5 minutes)

1. Open **Safari** → your hub address → sign in as a parent.
2. **File → Add to Dock** (macOS 14 or later). Family Hub opens like an app.
3. In that app: **Settings → 🔔 Turn on notifications** → Allow. The laptop now gets alerts too.
4. Use the MacBook Air for Part 4; it's easier to copy and paste there.

---

## Part 4: Connect everything (🔌 Connections page, on the MacBook Air)

Open Family Hub → **🔌 Connections**. Each row shows its status and has a setup form. Do them in this order.

### 4.1 Gmail alerts (2 min)
1. Make sure your Google account has **2-Step Verification** on: myaccount.google.com → Security.
2. Go to **myaccount.google.com/apppasswords**, name it "Family Hub", and **copy the 16-letter password**.
3. Connections → **Gmail alerts (app password)** → enter your Gmail address, the app password, and who gets emails → **Connect**.
   A test email arrives.

### 4.2 Your Google Calendar in the hub (2 min per calendar)
1. On the MacBook, open **calendar.google.com** → ⚙️ Settings → under *Settings for my calendars* click your calendar →
   **Integrate calendar** → copy **Secret address in iCal format**.
2. Connections → **Calendar links** → add one line per calendar, in the form `Name | link | person`:
   ```
   My Google | https://calendar.google.com/calendar/ical/…/basic.ics
   Lincoln Elementary | webcal://school.example/calendar.ics | Sam
   ```
   → **Connect**. Events appear on the Calendar tab and refresh every 30 minutes.

   These calendars are **read-only** in the hub. For two-way sync, see 4.9.

### 4.3 Hub calendar on the S24 (1 min)
1. Connections → **Family Hub calendar on every phone** → click **Google** next to *Whole family* (or a person).
   Google Calendar opens and asks to add it → **Add**.
2. On the S24: **Samsung Calendar** → ☰ → ⚙️ → **Manage calendars** (or the Google Calendar app) → make sure the new
   "Family Hub" calendar is ticked. Homework due dates, appointments, events and birthdays now show on the phone.

### 4.4 Home Wi-Fi presence: who's home (5 min, nothing to install)
1. On the S24: **Settings → Connections → Wi-Fi** → tap ⚙️ next to your home network → **View more** →
   **MAC address type** → **Phone MAC**.
2. Find the address: **Settings → About phone → Status information → Phone Wi-Fi MAC address** (looks like `a4:30:7a:…`).
3. On the hub: **Map** tab → add a place named exactly **Home** (tap your house on the map) → **Save place**.
4. Same tab → *Home Wi-Fi presence* → tick **Detect phones on home Wi-Fi** → **Add phone** next to your name → paste the address.
5. Within a minute it should say "seen just now". When the phone has been gone 15 minutes, you're "away".
   This drives "arrived home" / "left home" alerts and **"Nobody's home and the door is unlocked."**

### 4.5 Google Maps location sharing for other Android phones (5 min, optional)
This tracks *other* family members' Android phones in the background. A phone can't share with its own account,
so for your own S24 use Wi-Fi presence (4.4) and the app.
1. On each other phone: **Google Maps** → profile picture → **Location sharing** → **New share** → **Until you turn this off** →
   choose **your** Google account.
2. On the MacBook, open Chrome in a **private/incognito window**, sign in at **google.com/maps** with your account.
   Install a "cookies.txt" exporter extension (for example *Get cookies.txt LOCALLY*), export cookies for **google.com**,
   and open the file in TextEdit.
3. Connections → **Google Maps location sharing** → your Gmail + paste the whole file → **Connect** →
   match each shared person to a family member → **Save who is who**. Close the private window **without signing out**.

### 4.6 Philips lock through SmartThings (15 min)
This route is for the **Philips 4000 series (Matter)** lock.

**What you need:** Matter-over-Thread locks need a **Thread border router** on your network. Examples: SmartThings
Station, Aeotec Smart Home Hub (v2/v3), Google Nest Hub (2nd gen) or Nest Wifi Pro. Some recent Samsung TVs and fridges
also act as one. If you don't have one, pairing will fail; get one first.

1. **Pair the lock on the S24:** **SmartThings** app → **+** → **Add device** → **Scan QR code** → scan the Matter code on the
   lock or its manual → follow the steps. Lock and unlock it once from SmartThings to confirm it works.
2. **Create the free SmartThings key on the MacBook:**
   - Install **Node.js LTS** from **nodejs.org**.
   - Open Terminal and run `npx @smartthings/cli apps:create`. The first time, a browser window asks you to sign in to Samsung.
   - Answer: **OAuth-In App** → name *Family Hub* → skip the optional items → scopes **r:devices:\***, **x:devices:\***,
     **r:locations:\*** → redirect URI:
     ```
     https://YOUR-HUB-ADDRESS/api/connectors/smartthings/callback
     ```
     (also shown on the Connections page).
   - Copy the **OAuth Client Id** and **OAuth Client Secret** it prints.
3. Connections → **Samsung SmartThings** → paste both → **Connect** → sign in to Samsung → **Allow**.
4. The lock appears on the **Doors** tab with lock state, battery, door open/closed (if the lock reports it) and
   **Lock / Unlock** buttons. The hub checks it every minute.
5. Doors tab → **Alert rules**: set quiet hours (e.g. 10 PM–6 AM) and the "unlocked longer than" minutes.

**Philips Wi-Fi models** (Philips Home Access app, not Matter) don't connect to SmartThings directly. Use the
**Webhook** option on the Doors tab, or Home Assistant. Not sure which model you have? Look for a Matter QR code on the
lock or in its box, or check the model number (Matter: *DDL240XI5KMT*).

### 4.7 Kids, chores, medicine and savings (10 min)
- **Chores:** Chores tab → *Add a chore*. Tick **"Kid takes a photo to prove it's done 📷"** for rooms, dishes and the like.
  Kids tap 📷, take the picture, and you approve from the photo.
- **Medicine:** Health tab → *Add a record* → Type **Medication** → name and dose → **Times** like `08:00, 20:00`,
  optional days and end date. At each time, that person's phone gets a notification. On the S24 it has a
  **Taken ✓** button right on the notification. If a kid hasn't tapped Taken after 30 minutes, parents are alerted.
  Today's doses also show on the **Today** screen.
- **Savings goals:** Money tab → *New savings goal* (e.g. "New bike", $150). Set a **parent match** (e.g. 50% means you
  add 50¢ for every $1 they save), use **🎁 Gift** for birthdays, and they press **Buy it! 🎉** when they reach it.
- **Rewards:** Rewards tab → add rewards with point costs (e.g. "Pick Friday's movie", 50 points).

### 4.8 What doesn't apply to your devices
- **iMessage / texts from the Mac:** iMessage only reaches Apple devices, and texting from the Mac needs an iPhone. On the S24,
  **push notifications + Gmail** cover this. If you later add an iPhone, see the README.
- **iPhone Shortcuts, Apple Find My, Apple Home:** iPhone-only. Skip them. Your MacBook Air can be found with Find My if you like.
- **Google Chat:** personal Gmail accounts can't receive automated Chat messages (Workspace only).

### 4.9 Optional: two-way Google Calendar + Drive (15 min)
Skip this unless you want events created in the hub to appear in Google Calendar, or attachments saved to Google Drive.
Connections → **Google** shows the steps: create a free Google Cloud project, enable 3 APIs, set the consent screen to
**In production**, and add the redirect URI. Then put the ID and secret in `~/FamilyHub/family-hub.env` on the
Mac mini and run `bash ~/fantasy-labs/family-hub/mac/setup.sh` again.

---

## Part 5: Check it's all working (5 min)

| Check | How |
|---|---|
| Hub is up | Your hub address loads on the S24 with Wi-Fi **off** (mobile data). |
| Push works | Settings → **Send a test** on the S24 and on the MacBook. |
| Door alerts | Unlock the lock in SmartThings during quiet hours → alert on the S24. Or add a **Demo lock** on Doors and unlock it. |
| Who's home | Map tab shows *seen just now* next to your phone at home. |
| Medicine | Add a test med 2 minutes from now → notification → tap **Taken ✓**. |
| Chore photo | Make a 📷 chore for a kid → complete it from their login → photo shows for approval. |
| Connections | 🔌 page: everything you set up is green; anything yellow shows the error. |

## Day to day
- **Update the hub:** download the new ZIP (or `git -C ~/fantasy-labs pull`), then run `bash ~/fantasy-labs/family-hub/mac/setup.sh`.
- **Logs:** `~/FamilyHub/logs/server.log` on the Mac mini.
- **Backups:** `~/FamilyHub/backups` keeps a copy from each of the last 14 days. Also back up `~/FamilyHub/family-hub.env`
  (it holds the encryption key).
- **Mac mini restarted?** It logs in by itself (if you set 1.1-3) and the hub and Tailscale start automatically.
- **Something yellow on Connections?** It shows the error. Copy it, plus the last lines of the log, and send them to me.
