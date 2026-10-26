# LightSpeed frontend access: diagnosis and plan

Written 2026-10-26. Split: Claude designed this, Pak (the Muse agent) runs the tests and pulls data, Dustin decides.
Scope: how we **reach** the LightSpeed R-Series back office for purchase order (PO) work. It does not change what we
do there. Nothing in this plan edits a PO or any other record.

## Summary

1. **Check this first: the API probably does have purchase orders.** In the R-Series V3 API the PO resource is
   called `Order` (lines are `OrderLines`), not `PurchaseOrder`. A 404 on `PurchaseOrder` is what any unknown
   resource name returns. Ops Hub's Lightspeed connector already reads POs from `Order`
   ([`opshub/connectors/lightspeed.py`](../opshub/connectors/lightspeed.py)). If the existing API client has the
   PO read scope, reading what's on order, what's on the way, and per-PO lines needs no frontend login at all.
   **Step 0** below settles it in about 10 minutes. Only entering draft POs would still need the frontend, and that
   stays a human task.
2. **Two different failures have been logged as one.**
   * *Agent clicks on a live Turnstile fail by design.* Cloudflare says browser automation is not supported for
     solving production challenges, and Turnstile detects Selenium, Playwright and similar tools as bots. That
     explains 2026-09-30: the VM passed for Dustin's click that day, but not for ten agent attempts. This is not
     something to fix. It is a line we don't cross.
   * *Since then, the VM itself has fallen below the pass mark.* On 2026-10-26 even Dustin's own click failed. The
     most likely cause is the VM's **network identity**: a datacenter egress IP, probably shared or rotating, whose
     reputation changes over time. It stacks on an always-on handicap from the managed Chromium (automation
     flags, a virtual-machine fingerprint, and possibly a TLS-intercepting proxy). Confidence: medium. Steps 1 to 5
     confirm or kill each part.
3. **Recommended architecture:** don't log in on the VM at all. Use a **dedicated Chrome profile on the Mac Mini**:
   residential IP, real Mac Chrome, and Dustin solves the check himself. One harvest pass then pulls every PO
   list and line export into the Drive folder Ops Hub already ingests (route a). Start with **scheduled
   human exports** (route e), which work today with no terms questions. Agent-driven frontend use waits on one
   terms question for LightSpeed (route d). Their Acceptable Use Policy appears to restrict "unapproved automation".
4. **Ranked out:** anything that makes the agent pass the human check (stealth or anti-detect browsers, fingerprint
   spoofing, CAPTCHA-solving services), copying session cookies between machines, paid residential proxies, and
   routing the whole VM through Dustin's home connection.

---

## Step 0: does the API already cover purchase orders?

This isn't a redesign of the API route. It checks whether the route we already have can read the PO resource
under its real name.

**Why it's likely:** LightSpeed's R-Series docs describe the purchase-order print template on the `Order` resource
with `OrderLines` and `Vendor` relations. They list `employee:purchase_orders` as the scope for reading purchase
orders. A third-party map of the R-Series data model lists `Order` with `OrderedDate`, `ReceivedDate`, `RefNum` and
`VendorID`. Ops Hub's connector reads `Order` with `OrderLines` and `Vendor`, and treats a missing scope as a
warning rather than a failure. *Not verified against our account.*

**Rotation warning:** LightSpeed rotates the refresh token every time it's used. Whichever tool refreshes it must
save the new one, or the tool that holds it stops working. **Run this test inside the tool that already holds the
working API credentials. Never paste the refresh token into a second tool.**

| If the working credentials live in… | Pak runs |
| --- | --- |
| Ops Hub | First run `python -m opshub connectors`. `lightspeed` must show `live`; otherwise the sync below reads sample data and proves nothing. Then run `cd ops-hub && python -m opshub sync lightspeed` and read the JSON: a `warnings` entry starting with `Order:` means the PO read failed and shows why. No `Order:` warning means it worked. The POs then show in `GET /api/trackers/inbound?days=90` (Inbound tab). |
| Pak's own LightSpeed client | One read-only GET through that client: `GET https://api.lightspeedapp.com/API/V3/Account/{accountID}/Order.json?limit=5&load_relations=["OrderLines","Vendor"]` |

| Result | Means | Next |
| --- | --- | --- |
| 200 with `Order` records that have `OrderLines` | **The premise was wrong.** PO reads are on the API. | Check one PO against the screen: ordered quantity, checked-in quantity (`numReceived`/`checkedIn`), vendor, dates. If it matches, PO reads and Jason's status feed move to the API. This plan shrinks to the draft-PO entry path plus a fallback. |
| 200, empty | Credentials work, but no rows matched | Retry with `archived=true` and without filters before concluding anything |
| 401 / 403 | The resource exists, but the client lacks the PO scope | **Dustin decision D1b:** re-authorize the existing client with the PO read scope (`employee:purchase_orders`, or `employee:all`, which is Ops Hub's default). No cost. |
| 404 on `Order` too | The API really has no PO access for us | Continue with this plan as written |

Status mapping to verify against the screens: Open = no `orderedDate`; Ordered = ordered, nothing checked in;
Check In (in progress) = some lines checked in, not complete; Check In (done) = `complete` is true.

---

## Diagnosis

### What each event tells us

| Event | Rules in | Rules out |
| --- | --- | --- |
| 09-30: agent fails ~10 ways, Dustin's click passes in the **same** VM browser | That day the VM's IP and browser were good enough for a human. The agent's interaction was the failing part. | "The VM can never pass" (on that day) |
| 10-07: ShipStation shows a "token fetch error" and nothing renders | The widget never ran: a blocked or altered path to `challenges.cloudflare.com`, a clock or caching problem, or a hard fail before render | Clicking technique (there was nothing to click) |
| 10-26: Dustin's own click fails on the VM | The environment's score dropped between 09-30 and 10-26, **or** his click didn't reach the widget through the remote view | Agent behavior as the only cause |
| FedEx: instant block, no challenge, survives clearing site data, fine on the Mac with the same account, lifted on the evening of 10-08 | An identity check on the machine or network, varying over time | Cookies, the account, the credentials |
| Google sessions persist in the VM profile | The profile isn't being wiped | Profile resets as a cause |
| Google sign-in also fails on Dustin's own Chrome | An account-linking issue, separate from the VM | (Out of scope. It doesn't change the diagnosis.) |

**Why the network identity is the lead suspect for the environment part:**

* It shows up on two sites with different kinds of checks, so the common factor is the machine and its network,
  not one vendor's script.
* A fixed browser build produces a fixed fingerprint, and a fixed fingerprint gives a fixed verdict. A verdict
  that flips back and forth (FedEx lifting on 10-08; LightSpeed passing on 09-30 and failing on 10-26) needs an
  input that changes. IP reputation changes, especially on a shared or rotating datacenter egress. That holds
  unless Chromium updated between those dates (see U4).
* Cloudflare's own troubleshooting lists IP reputation, "often seen with shared VPNs or corporate proxies", among
  the main reasons real people get challenged. It also says to try a different network.

### Hypotheses, and what confirms or kills each

| ID | Hypothesis | Confirmed if | Killed if | Test |
| --- | --- | --- | --- | --- |
| H-NET | The path from the VM browser to `challenges.cloudflare.com` is blocked or altered (proxy, filter, cache) | The always-pass test widget fails to load or errors on the VM (no script, `200500`, `200100`) but works on the Mac | The test widget gets a token on the VM | Step 2 |
| H-TLS | A TLS-intercepting proxy sits under the VM browser, so Cloudflare sees a Chrome User-Agent on a non-Chrome TLS handshake | The VM browser's certificate issuer for a public site is a private CA, **or** its JA4 / HTTP/2 fingerprint differs from Mac Chrome of the same major version | The public CA chain shows, and the fingerprint matches stock Chrome | Step 1 |
| H-IP | The egress IP's reputation is failing the score | Turnstile's error on the VM is in the `600*`/`300*` family, **and** the same VM browser passes a human click once its traffic exits through the home connection | It still fails with home egress (Step 5), **or** the IP never changes while results flip | Steps 1, 4, 5 |
| H-FP | The browser environment fails the score: automation flags (`navigator.webdriver`), a SwiftShader/no-GPU fingerprint, a timezone that doesn't match the IP, CDP attached | It fails with home egress (Step 5) while the Mini passes. The Step 2 page shows `webdriver: true` or SwiftShader. | It passes with home egress | Steps 2, 5 |
| H-INPUT | On 10-26, Dustin's click through the VM's remote view never reached the widget | The forced-checkbox test widget doesn't complete from his click on the VM | It completes | Step 2, widget 2 |
| H-CLOCK | The VM clock is off, which breaks challenges (`110600`, `200100`) | The `ts=` in Cloudflare's trace differs from the VM clock by more than about 30 s | The clocks agree | Step 1 |

The agent-clicking question (09-30) needs no more testing. Cloudflare documents that automation isn't supported for
production challenges, so whatever a further test showed, we wouldn't build on it.

### Unknowns, and what settles each

| # | Unknown | Settled by |
| --- | --- | --- |
| U1 | How Dustin's clicks reach the VM browser (OS-level input through a remote desktop, or relayed by the agent tooling) | Pak describes the remote-view mechanism; Step 2, widget 2 |
| U2 | Whether the VM browser's traffic goes through an intercepting proxy, and whether the VM can run Tailscale or use a per-browser proxy | Step 1 (certificate issuer, `chrome://version`, `chrome://net-internals/#proxy`); ask the VM's provider |
| U3 | Whether the VM egress IP is static, rotating, or a shared pool | Step 1 run on several days; any past logs Pak has of egress IPs (especially the evening of 10-08) |
| U4 | Whether Chromium updated between 09-30 and 10-26 | Pak's browser update history or package logs |
| U5 | The exact Turnstile error codes for the LightSpeed and ShipStation failures | Step 4 |
| U6 | Whether `Order` returns POs with our token and scope | Step 0 |
| U7 | LightSpeed's session idle timeout and maximum length, and whether a second login ends the first | Step 7; question 4 in route d |
| U8 | The exact wording of LightSpeed's Acceptable Use Policy on bots and automation (a search snippet suggests it restricts "the use of bots to access and use Products as well as other unapproved automation"; I could not open the page to confirm) | Dustin reads lightspeedhq.com/legal/acceptable-use-policy; question 3 in route d |
| U9 | Whether Dustin's MacBook test was on the home network (the same IP the Mini uses) | Ask Dustin |
| U10 | Whose LightSpeed login Jason's PO scraping uses | Ask Jason or Dustin |
| U11 | Whether LightSpeed's login shows Dustin's Mac a checkbox at all, or verifies silently | Step 4 control |

---

## Test plan for Pak

### Ground rules

* **Stop all agent attempts at the LightSpeed checkbox, starting now.** Every failed attempt adds bot signal to the
  same IP and browser, and Cloudflare doesn't support automation solving production challenges.
* A person clicks a live Turnstile **at most twice per session**, then stops.
* Read only: no PO opens in edit mode, no Save, Order, Check In or Delete buttons. Logging in is the only form
  submission.
* Never record cookie *values*, tokens or passwords. Cookie names and expiry times are fine.
* Log every attempt in the results table at the end of this section. Use Hawaii time.

### Step 1: what the VM looks like from outside (Pak, about 20 min, no login, nothing changed)

In the **VM browser** (not the shell):

1. Open `https://www.cloudflare.com/cdn-cgi/trace`. Copy the whole text. `ip=` is what Cloudflare sees, `loc=` is
   its country, `ts=` is Cloudflare's clock (Unix seconds), and `http=`/`tls=` are the protocols.
2. Open `https://ipinfo.io/json`. Record `org` (ASN and owner) and the city. A cloud provider's ASN means a
   datacenter IP.
3. Open `chrome://version`. Copy the version and the whole **Command Line**. Note any of `--enable-automation`,
   `--headless`, `--remote-debugging-port`, `--proxy-server`, `--ignore-certificate-errors`, `--user-agent`.
4. Open `chrome://net-internals/#proxy`. Record the effective proxy settings.
5. Open `https://www.cloudflare.com`, then DevTools → Security → View certificate. Record **Issued by**. A public CA
   (for example Google Trust Services, Let's Encrypt, DigiCert) is normal. Anything else means TLS is being
   intercepted, which **confirms H-TLS**.
6. Open `https://tls.peet.ws/api/all` (backup: `https://tls.browserleaks.com/json`). Record `ja4`, the HTTP/2
   (Akamai) fingerprint, and `http_version`. Use JA4, not JA3: Chrome shuffles its TLS extensions, so JA3
   changes on every connection.
7. Compare the VM clock with `ts=` from item 1.

Then Dustin, or Pak if it can reach the Mac, repeats items 1, 2 and 6 in **Chrome on the Mac** as the control.

| Result | Reading |
| --- | --- |
| Private-CA issuer, or the VM's JA4 / HTTP/2 fingerprint differs from Mac Chrome of the same major version, or `http=http/1.1` | **H-TLS confirmed.** No browser setting fixes this from inside the VM. Strongly favors moving the login off the VM. |
| Public CA, matching fingerprint | H-TLS killed |
| Clock off by more than about 30 s | H-CLOCK confirmed. Ask the VM's provider to fix time sync, then retest. |
| `ipinfo` org is a cloud provider | Consistent with H-IP; doesn't prove it on its own |

Repeat items 1 and 2 at each later step and on later days. A changing `ip=` answers U3.

### Step 2: can Turnstile run on the VM at all? (Pak, about 10 min, plus one click from Dustin)

Use [`ops-hub/tools/turnstile-probe.html`](../tools/turnstile-probe.html). It renders Turnstile with Cloudflare's
published **test sitekeys**, which never touch a real site and are the documented way to exercise Turnstile from
automated browsers. It also lists what the browser reports about itself. Nothing is sent anywhere.

```bash
cd ops-hub/tools && python3 -m http.server 8765 --bind 127.0.0.1
# then open http://localhost:8765/turnstile-probe.html in the VM browser
```

* **Widget 1** (always passes, no click) should show *token received* within a few seconds.
* **Widget 2** (forces a checkbox) is clicked **once by Dustin**, the same way he clicked LightSpeed on 10-26. The
  agent doesn't click it.
* Press **Copy results** and paste the JSON into the log.

| Result | Reading |
| --- | --- |
| Widget 1: "script did not load", `200500` or `200100` | **H-NET confirmed.** The VM path to Cloudflare's challenge platform is broken. This also explains ShipStation. |
| Widget 1: token received | **H-NET killed.** Turnstile runs, so the LightSpeed failure is scoring (H-IP / H-FP / H-TLS). |
| Widget 2 doesn't complete after Dustin's click | **H-INPUT confirmed.** The remote view isn't delivering his click, so the 10-26 "human" failure wasn't really a human click. |
| Widget 2 completes | H-INPUT killed |
| Environment shows `webdriver: true`, a SwiftShader WebGL renderer, or a timezone that doesn't match `loc=` | Each is a known automation or VM signal (supports H-FP). We record these to explain the result. **We do not change them to pass checks.** That would be evasion, which is ranked out. |

Optional: open `https://debug.challenges.cloudflare.com/` (Cloudflare's compatibility checker) on the VM and the Mac.
Screenshot both.

### Step 3: is the ShipStation failure the same thing? (Pak, about 5 min)

Open the ShipStation login on the VM, wait 20 s without touching anything, and record any error code shown under
the widget and in the console. `200500` or "script not loaded" means the same H-NET path problem. `600*` or `300*`
means scoring, like LightSpeed. Run this only if ShipStation matters to us beyond being evidence.

### Step 4: capture LightSpeed's actual verdict (Pak plus Dustin, about 10 min)

On the VM:

1. Open the LightSpeed login page and wait 20 s **without touching the widget**. A screenshot shows whether it
   verified on its own, showed a checkbox, or showed an error. Copy any code shown under the widget and the console
   errors.
2. Dustin clicks once. Wait 20 s, screenshot, and record the code and the Cloudflare Ray ID if one is shown.
3. Record Step 1 items 1 and 2 at the same moment.

On Dustin's Mac (the control): open the same login page and note whether a checkbox appears or it verifies silently
(U11). Logging in is optional.

| Code | Reading |
| --- | --- |
| `600*` or `300*` | Scored as a bot. Go to Step 5 to split IP from browser. |
| `200500`, or the widget never loads | Path problem. It should match Step 2. |
| `110600` or `200100` | Clock or caching. It should match Step 1, item 7. |
| `110620` | Nobody clicked in time. Not a real failure; repeat once. |

### Step 5: split IP from browser (optional; needs Dustin's OK because it temporarily changes his network setup)

Purpose: run the **same VM browser** through the **home connection** for one test, then turn it off.

Run it only if Steps 1 to 4 left H-IP versus H-FP open **and** we still want the VM to work (for example for
ShipStation or FedEx). If route a works, we don't need this step to make a decision.

1. Pause all other agent browser tasks for the window, so their traffic doesn't run through the home IP.
2. On the Mini, Tailscale → Exit Node → Run Exit Node. Dustin approves it in the Tailscale admin console.
3. On the VM, `tailscale up --exit-node=<mini>`. This needs Tailscale on the VM. If the VM's provider doesn't
   allow it, skip Step 5 and record that route b is not possible.
4. Check Cloudflare's trace in the VM browser. `ip=` must now be the home IP. **If it isn't, the browser's traffic
   is carried by an upstream proxy. Stop: route b is not possible either.**
5. Repeat Step 4 (load, wait, one human click).
6. Turn the exit node off on both ends and resume tasks.

| Result | Reading |
| --- | --- |
| Passes (silently or with one click) | **H-IP confirmed** as the deciding factor. Route b becomes possible. |
| Still `600*`/`300*` | **H-IP killed as sufficient.** The browser environment (H-FP / H-TLS) is deciding, and only moving off the VM fixes it. |

### Step 6: the Mini, which is the deciding test for route a (Dustin, about 15 min)

1. On the Mini, create a dedicated Chrome profile used only for LightSpeed:
   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --user-data-dir="$HOME/Library/Application Support/LightspeedPO-Chrome" --no-first-run
   ```
2. Dustin logs in himself, at the Mini or over Screen Sharing. He notes whether a checkbox appeared.
3. Open the purchase order list and confirm the page loads. Make no changes.
4. DevTools → Application → Cookies, on the LightSpeed and Cloudflare domains: record cookie **names** and
   **Expires** (whether each is "Session" or a date). No values.

Pass means route a is viable. If this fails, we are looking at something beyond IP and fingerprint. Ask LightSpeed
(route d) with the Ray ID.

### Step 7: how long does a session live? (human checks over two days, read only)

Do these on the Mini profile from Step 6 (or on the VM, if a VM login ever works):

| Check | How | Answers |
| --- | --- | --- |
| 7a: cookie type | From Step 6, item 4 | Session cookies die when Chrome quits. Persistent ones state their lifetime. |
| 7b: idle survival | Leave it untouched. Reload the PO list once at +4 h and once the next morning. | Rough idle timeout (or ask LightSpeed, U7) |
| 7c: second login | With the Mini logged in, Dustin logs in on his MacBook, then reload on the Mini | Whether Dustin's own use ends the agent's session |
| 7d: restart | Quit and reopen the dedicated Chrome | Whether a Chrome restart costs a login |

### Results log (one row per attempt)

| Time (HST) | Step | Machine / browser / version | Cloudflare `ip=` | Who clicked | Widget outcome | Error code | Ray ID | Notes or screenshot |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

### Decision tree

```
Step 0: Order returns POs?
 ├─ yes → PO reads go through the API (already in Ops Hub). The frontend is only for draft-PO entry (Dustin, by hand).
 │        Keep route e as the fallback. Steps 1–7 are only needed for other sites.
 └─ no  → Steps 1–4 (cheap, nothing changed) → Step 6 (Mini)
           ├─ Mini passes → build route e now, route a once the terms are clear (D3), with the route c discipline.
           │                Step 5 only if the VM must work for ShipStation or FedEx.
           └─ Mini fails  → route e only, plus the LightSpeed ticket (route d) with Ray IDs.
```

---

## Routes, ranked

| Rank | Route | Reliability | Within terms? | New spend | Effort | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **a. Mini, dedicated profile, human login, harvest in that session** | High: removes every VM factor at once | Login: yes, a human solves it. Agent use afterwards: needs LightSpeed's OK (U8) | None | About a day for Pak, 30 min for Dustin | **Build after Step 6 passes and D3 is settled** |
| 2 | **e. Scheduled human exports from Dustin's Mac** | High per run; limited by Dustin's time | Yes, with no caveats | None | 2–3 h to prebuild; 10–15 min per run | **Start now as the baseline and permanent fallback** |
| 3 | **c. Session longevity discipline** | Multiplies whichever route holds the session; can't fix a login that fails | Yes. No keep-alive pinging without LightSpeed's OK. | None | Small | **Apply to a (and e)** |
| 4 | **d. Ask LightSpeed** | An allowlist is unlikely. High value for scope, terms and timeouts. | Yes | None | 15 min for Dustin | **Send now, in parallel** |
| 5 | **b. VM through the home connection (per-site only)** | Medium at best: fixes the IP only | Yes for a human-solved login; not as a way for the agent to pass the check | None | Medium to high, two machines | **Only if Step 5 says IP is the sole factor and the VM must work** |

### a. The Mac Mini runs the frontend work (recommended)

* **How:** a Chrome profile on the Mini used only for LightSpeed, separate from Dustin's everyday profile, so
  agents never touch his Gmail or banking sessions. Dustin logs in and solves the check himself. A harvester
  running **on the Mini** attaches to that logged-in window and runs the one-pass harvest below. It drops files in
  the Drive folder *Ops Hub/Imports/Lightspeed exports*, which Ops Hub already ingests and Pak and Jason can read.
  Pak triggers a run (for example through a request file in the shared Muse folder) and checks the output.
  It doesn't need a live connection into the Mini.
  * Alternative a2: Pak drives the Mini's Chrome remotely through the DevTools protocol over Tailscale. It's
    workable but weaker: the VM has to join the tailnet, and it opens a full-control channel into a browser that
    holds a live session. Prefer the harvester running on the Mini.
* **Pros:** fixes the IP, the fingerprint, any TLS interception and the shared profile in one move. The human check
  is solved by a human, as Cloudflare intends. The Mini is always on, already runs Ops Hub, and already has the
  Drive plumbing. No new spend. The same setup can take the ShipStation and FedEx logins later.
* **Cons and risks:** the Mini needs a logged-in macOS session and must not sleep. The debugging port gives full
  control of that browser to anything on the Mini that can reach it. Possibly that includes the sandbox's Docker
  containers through `host.docker.internal`. So bind it to localhost, open it only during a run window, and keep
  the profile LightSpeed-only. Chrome 136+ requires a separate `--user-data-dir` for remote debugging anyway.
  Agent use after login may count as "unapproved automation" (U8). Until LightSpeed answers, keep runs read-only,
  low-volume and human-paced, or have Dustin start each run.
* **Effort:** about a day for Pak (harvester and runbook) and 30 min for Dustin (profile, power settings, first
  login).

### e. Human-in-the-loop exports (start now)

* **How:** a 10–15 minute checklist for Dustin on his own Mac (it already works). Export the Ordered and Check In
  lists and the per-PO Items CSVs (and the Received Shipments report if Jason wants check-in history), then save
  them into *Ops Hub/Imports/Lightspeed exports*. Pak's offline merge step (below) never touches LightSpeed. It
  adds the PO number to each Items file and builds one Ops Hub-ready file. Cadence is Dustin's call (for example
  Monday and Thursday, or on Jason's request).
* **Pros:** no terms questions, no spend, works today, and it's also the fallback for every other route.
* **Cons:** costs Dustin's time; data is only as fresh as the last run.

### c. Session longevity (a discipline, not a standalone route)

A good login is the expensive part, so protect it. On the VM this can't stand alone: on 10-26 the login itself
failed.

**What kills sessions, roughly most likely first:**

1. Someone or something **clears cookies broadly**. For example the FedEx fix, "clear site data", run for all
   sites or all time instead of one site.
2. **Another task in the same profile** logs out, opens the login page, or hits a page that resets the session.
   All agent tasks share one profile on the VM.
3. **LightSpeed's idle timeout or maximum session length** (unknown, U7).
4. **A second login by the same user elsewhere**, if LightSpeed enforces one session per user (unknown, 7c).
5. **A browser restart** when the session cookie is session-only (7a/7d).
6. **An egress IP change mid-session**, if the session or Cloudflare clearance is tied to the IP. Cloudflare's
   `cf_clearance` is tied to the visitor and device. On the VM, with a rotating egress, this is a real risk.
7. **A mid-session re-check by Cloudflare.** Cloudflare can re-evaluate clearance from in-session behavior, so
   machine-speed navigation can draw a new challenge.
8. A password change, "sign out everywhere", or a 2FA re-prompt.

**Usage pattern that keeps a session longest:**

* One owner: a lock so only one LightSpeed task runs at a time, in its own profile.
* All work in one batch straight after login (the harvest below), most time-sensitive data first.
* Human pace (a few seconds between page loads). No parallel tabs hammering pages.
* Never log out, never clear cookies outside a named site, never restart the browser mid-window.
* Stable egress (another argument for the Mini).
* Dustin avoids logging in as the same user elsewhere during a run window until 7c says it's safe. Better: a
  separate LightSpeed employee login for harvests (D7).
* **No automated keep-alive pinging** to stretch sessions without LightSpeed's OK. It's exactly the "unapproved
  automation" pattern, and a session kept open indefinitely is a standing security exposure.

### d. Ask LightSpeed support (Dustin sends; draft below)

* **Pros:** free and authoritative. It can make Step 0 a clean yes, give the timeouts (U7) and the terms answer
  (U8), and might turn up an official PO report or export.
* **Cons:** slow. A per-merchant IP allowlist is unlikely. Turnstile sits on a shared login page, and an allowlist
  would need LightSpeed to skip the check for an IP in their own login code. We also have no static IP on the VM
  to give them.

> Subject: R-Series: purchase orders via API, back-office sessions, and the login check
>
> Hi, I'm the account owner for [account name / ID]. A few questions:
>
> 1. Is the V3 API `Order` resource (with `OrderLines`) our purchase orders? Which OAuth scope does it need, and do
>    order lines include checked-in quantities?
> 2. Is there a scheduled or emailed report that lists open purchase orders (Ordered and Check In) with their line
>    items?
> 3. As the account owner, may I use a script to export my own purchase order lists from the back office after I
>    log in myself? Or is the API the only approved route for automation under your Acceptable Use Policy?
> 4. How long does a back-office login stay valid when idle, is there a maximum session length, and does logging in
>    on a second device end the first session?
> 5. The "Verify you are human" check on the login page sometimes fails even when I click it myself. Is there a
>    supported way to allowlist our IP address, or anything else you recommend?
> 6. "Log in with Google" sends me back to the login page. How do I link my login to Google?
>
> Thanks, Dustin

### b. The VM through the home connection

* **Only per site, never the whole VM.** Sending all agent traffic out through Dustin's home IP would put his
  household's reputation behind every site the agents visit. That's the same signal these vendors score on, so
  his Mac and the Mini could start getting challenged. Per-site routing means a proxy auto-config in the VM
  Chromium. It sends only the LightSpeed hosts and `challenges.cloudflare.com` through an SSH tunnel to the Mini
  over Tailscale. That needs macOS Remote Login turned on (a network and security change, D6).
* **Fixes only the IP.** It does nothing for automation flags, the VM fingerprint, TLS interception (if the
  platform's proxy sits under the browser, the route may not even be possible: Step 5, item 4), or the shared
  profile.
* **Terms:** sending your own traffic through your own home connection is ordinary network use, much like a VPN to
  the office. Cloudflare's own advice is to switch to a trusted network. That's fine **when a human solves the
  check**. Using it so an automated browser borrows residential reputation to get past bot checks is the
  residential-proxy pattern. That's out.
* **Effort:** medium to high, plus maintenance on two machines.

### Ranked out

| Approach | Why |
| --- | --- |
| Agent solves Turnstile with stealth or anti-detect browsers, patched automation flags, or fingerprint spoofing | Circumvents a security control LightSpeed placed on its login. Cloudflare doesn't support automation solving production challenges. Evasion, whatever account it is. |
| CAPTCHA-solving services | Excluded by our constraints, and the same circumvention |
| Logging in on the Mac and copying cookies to the VM | Credential and session sharing outside the vault. Cloudflare's clearance is tied to the device anyway. |
| Paid residential proxies | New spend, and the pattern bot checks exist to stop |
| Whole-VM exit node on the home network | Not a terms problem, but it risks the household IP reputation (see b) |
| Sharing Dustin's login with Jason or with agents | Credential sharing. Use separate employee logins (D7). |

---

## One login, maximum PO data: what to prebuild

The same design serves route a (automated on the Mini) and route e (Dustin by hand). If Step 0 passes, the API
replaces most of it.

**The harvest spec, in one pass, ordered so a session that dies halfway has already saved the most urgent data:**

1. **PO lists:** every page of **Check In**, then every page of **Ordered** (optionally **Open** drafts, and
   **Finished** for the last 14 days to catch closures). Save each list as CSV: PO #, vendor, shop, status, ordered
   date, expected arrival, item count, quantity, cost, last updated.
2. **Per-PO Items CSV** for every PO in those lists, in this order: Check In first, then Ordered by soonest
   expected arrival.
3. **Received Shipments report** (Reports → Shipments) for the last 30 days, if Jason needs check-in history.

*The exact export buttons and column headers haven't been confirmed. In the first human session, Pak records which
pages have Export and their headers. That fixes the parser before we build anything automated.*

**Output contract**, written to a dated folder such as `lightspeed-po/2026-10-27T0830-HST/`:

* The raw files exactly as downloaded (never edited).
* `po_lines.csv`: every Items file merged, with one row per PO line. Use these headers so Ops Hub's importer files
  it as purchase orders without changes: `PO #, Vendor, Status, Shop, Arrival Date, Custom SKU, Description, Qty
  Ordered, Qty Checked In`. Don't use a column named `Qty.`; Ops Hub reads that as stock on hand. Drop
  `po_lines.csv` into *Ops Hub/Imports/Lightspeed exports*.
* `manifest.json`: start and end time, who logged in and where, PO counts per status, each PO's status and
  last-updated value, file checksums, and anything skipped and why.

**Delta runs:** always re-pull the lists, which are cheap. Re-export Items only for POs that are new or whose
status, checked-in quantity or last-updated value changed since the last manifest. Do a full refresh weekly. Fewer
page loads means shorter windows and fewer logins.

**Guards built into the harvester:**

* An allow-list of pages and actions: navigate, filter, page through, export. Anything else aborts the run.
* If a login page or any challenge appears, **stop, save what's done, and notify**. It never tries to solve it.
* Human pacing, one run at a time (a lock), and resume from the manifest.
* Credentials stay in the vault and only a human types them. Logs carry no cookies or tokens.

**One harvest, several readers:** Jason reads the order-flow data from the same output. Either he reads the Drive
folder, or he uses Ops Hub's Inbound tracker with a read-only key (`/api/trackers/inbound`, or
`/api/export/inbound.csv` for Sheets). His separate scraping login then goes away. Before automating anything, feed
one hand-made `po_lines.csv` through Ops Hub's **Import file** button and check the Inbound tab against the screens.

---

## Decisions for Dustin

| # | Decision | Cost | Recommendation |
| --- | --- | --- | --- |
| D1 | Let Pak run Step 0 (one read of the API `Order` resource, in the tool that holds the credentials) | None | Yes, first |
| D1b | If Step 0 returns 401/403: re-authorize the existing API client with the PO read scope | None (it touches API config) | Yes |
| D2 | Send the LightSpeed support message (draft in route d) | None | Yes, now |
| D3 | Read LightSpeed's Acceptable Use Policy on bots and automation, then choose: agent harvesting on the Mini, or human-started runs only until LightSpeed answers | None | Wait for LightSpeed's answer before running unattended |
| D4 | Start route e: cadence and roughly 10–15 minutes of your time per run | Your time | Yes, now |
| D5 | Use the Mini for route a: dedicated Chrome profile, no sleep, stays logged in to macOS, harvester runs there | None | Yes, after Step 6 |
| D6 | Allow the one-time Step 5 test (temporary Tailscale exit node on the Mini) and, later, if ever, per-site routing (Remote Login on the Mini) | None (network change) | Skip unless the VM must work for ShipStation or FedEx |
| D7 | A separate, view-only LightSpeed employee login for harvests (and confirm Jason has his own). Check first whether an extra user costs anything. | Possibly; check | Yes if free. It enforces read-only and keeps sessions from colliding. |

Nothing in this plan spends money. If anything turns out to have a cost (an extra LightSpeed user, a static IP, a
VPN), it comes back to Dustin as a decision.

## Sources

* Cloudflare, Turnstile [client-side error codes](https://developers.cloudflare.com/turnstile/troubleshooting/client-side-errors/error-codes/),
  [testing and test sitekeys](https://developers.cloudflare.com/turnstile/troubleshooting/testing/) ("Automated testing
  suites (like Selenium, Cypress, or Playwright) are detected as bots by Turnstile"),
  [supported browsers](https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/) ("Browser
  automation frameworks … are not supported for solving production challenges"),
  [challenge solve issues](https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/),
  [common issues](https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/) (IP reputation, allowlisting
  is a site-owner WAF rule), [clearance](https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/)
  (`cf_clearance` tied to visitor and device, re-evaluated in session),
  [challenge passage](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/).
  Read from Cloudflare's docs repository on GitHub, 2026-10-26.
* LightSpeed: [Managing purchase orders](https://retail-support.lightspeedhq.com/hc/articles/15256357228187) (statuses
  Open, Ordered, Check In; the Received Shipments export),
  [DisplayTemplate Order](https://developers.lightspeedhq.com/retail/endpoints/DisplayTemplate-Order/) (the PO template
  on `Order` with `OrderLines` and `Vendor`; the `employee:purchase_orders` scope),
  [Acceptable Use Policy](https://www.lightspeedhq.com/legal/acceptable-use-policy/) (bots and automation wording
  seen only in a search snippet, unconfirmed: U8). These came from search results; the pages were not reachable
  from the session that wrote this.
* [Chrome: changes to remote debugging switches](https://developer.chrome.com/blog/remote-debugging-port) (Chrome 136+
  needs a non-default `--user-data-dir`). [Tailscale exit nodes](https://tailscale.com/kb/1103/exit-nodes).
