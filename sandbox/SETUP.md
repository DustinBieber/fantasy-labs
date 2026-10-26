# Setup: Mac mini + MacBook Air + Galaxy S24 Ultra

Who does what:

| Device | Role |
|---|---|
| **Mac mini** | The server. Runs the clipper sandbox all day, renders clips, posts on schedule. Stays plugged in and awake. |
| **MacBook Air** | Your control center. Developer-app setup in the browser, SSH or Screen Sharing into the mini, code changes. |
| **S24 Ultra** | Your remote. Review and approve clips from anywhere, download samples for pitches. |

All three join one private network with **Tailscale** (free), so the phone and laptop can reach the Mac mini from anywhere
without opening ports on your router.

Total time: about an hour, most of it waiting on installs. Total cost: $0. An Anthropic API key is optional and needs a $5 minimum credit.

---

## Part 1: MacBook Air (10 min)

1. **Install Tailscale.** Get it from the Mac App Store (search "Tailscale"), open it, and sign in with Google or GitHub. This
   creates your tailnet. Leave it running.
2. **Optional: get a Claude API key** for better clip picks (skip it to run 100% free).
   - Go to **platform.claude.com**, sign in, open **Billing**, and add $5 in credit. At these volumes that should last months.
   - Under **Limits**, set a monthly spend limit (say $10). That's a second safety net on top of the app's own $5/month cap.
   - Under **API keys**, create a key and paste it when the installer asks (Part 2, step 3).

## Part 2: Mac mini (about 20 min, sitting at it once)

1. **System Settings:**
   - **General → Software Update:** install updates.
   - **Energy:** turn on *Prevent automatic sleeping when the display is off*, *Wake for network access*, and *Start up
     automatically after a power failure*.
   - **General → Sharing:** turn on **Remote Login** (SSH) and **Screen Sharing**, so you can manage it from the MacBook Air.
   - Optional: **Users & Groups → Automatically log in as** your user. This only works with FileVault off. With FileVault on,
     after a power outage you'll have to log in at the Mac mini before the sandbox starts again.
2. **Tailscale:** install it from the Mac App Store, sign in with the **same account** as the MacBook Air, and turn on
   *Launch at login* in its menu-bar settings. If you do this first, the installer sets up phone access for you.
3. **Run the installer.** Open Terminal and paste:
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/DustinBieber/fantasy-labs/main/sandbox/bootstrap.sh)"
   ```
   It takes about 15 minutes and asks two questions up front: your Claude key (press Return to run free) and whether to keep
   the Mac awake. Then it handles the rest:
   - Installs Homebrew, ffmpeg-full, Python and the caption font
   - Downloads the code to `~/fantasy-labs` and starts the clipper as a background service that restarts after reboots
   - Turns on hourly self-updates from GitHub
   - Shares the dashboard over Tailscale and starts the first clipping pass
   - Prints your dashboard address and password at the end

   It's safe to run again any time; it updates instead of starting over.

<details><summary>Manual install instead (same result, step by step)</summary>

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # then run its "Next steps"
git clone https://github.com/DustinBieber/fantasy-labs.git ~/fantasy-labs
cd ~/fantasy-labs/sandbox
./sandbox.sh setup native            # or ./sandbox.sh setup for the Docker sandbox (needs Docker Desktop)
open -e ~/sandbox-data/clipper/.env  # optional ANTHROPIC_API_KEY; note DASHBOARD_PASSWORD
./sandbox.sh restart && ./sandbox.sh doctor
./sandbox.sh autoupdate on
tailscale serve --bg 8787            # if not found: /Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8787
./sandbox.sh run-now
```
</details>

4. **Keep it awake on workdays.** The installer offers this. To do it by hand (asks for your Mac password):
   ```bash
   sudo pmset -c sleep 0 disksleep 0
   sudo pmset repeat wakeorpoweron MTWRF 07:45:00
   ```
5. **Phone access.** The installer does this if Tailscale was already signed in. Otherwise run:
   ```bash
   tailscale serve --bg 8787
   ```
   It prints an address like `https://mac-mini.tail1234.ts.net`. Save it.
   - If it says `command not found`, run `alias tailscale=/Applications/Tailscale.app/Contents/MacOS/Tailscale` and try again.
   - If it prints a link asking you to enable HTTPS, open it, click **Enable**, and run the command again.
6. **First run:** the installer starts it. Later you can tap **Run now** on the dashboard or run `~/fantasy-labs/sandbox/sandbox.sh run-now`. The mini pulls The Fantasy
   Footballers' channel and cuts sample clips from their 2 latest episodes. That takes about 10–30 minutes; watch it with
   `~/fantasy-labs/sandbox/sandbox.sh logs`. Episodes appear under **Episodes** and finished clips under **Review**.

## Part 3: Galaxy S24 Ultra (5 min)

1. **Play Store → Tailscale.** Install it, sign in with the same account, and tap **Connect**. You can leave it on; it's light on
   battery. Optionally, under **Settings → Connections → More connection settings → VPN**, tap the gear next to Tailscale and
   turn on *Always-on VPN*.
2. **Chrome:** open the address from Part 2, step 5, and log in as `admin` with your password. Let Chrome or Samsung Pass save it.
3. **Make it an app icon:** in Chrome, tap **⋮ → Add to Home screen → Add**. Name it "Clipper".
4. **Reviewing clips:** open **Review** and tap play on a clip. Then:
   - Edit the **Hook** (the headline burned into the video) and tap **Save** to re-render it. Edit the **Title** and **Caption**
     (the post text) freely.
   - **Post to** lets you send a clip to a different show's account, like a betting take from the main pod going to the betting page.
   - **Approve** queues the clip. **Reject** drops it.
   - **Download** saves the MP4 to your phone's Downloads, ready to text or email in a pitch.

## Part 4: Running it from the MacBook Air

- **Terminal access:** `ssh <your-mac-username>@mac-mini` works from anywhere over Tailscale. Use the machine name shown in the
  Tailscale menu. Then:
  ```bash
  cd ~/fantasy-labs/sandbox
  ./sandbox.sh status          # running? healthy?
  ./sandbox.sh logs            # live log (Ctrl-C to exit)
  ./sandbox.sh cli review      # clips waiting for you
  nano ~/sandbox-data/clipper/config.yaml   # change settings, then:
  ./sandbox.sh restart
  ```
- **Full desktop:** Finder → **Network** → mac-mini → **Share Screen**, or `open vnc://mac-mini`.
- **Code changes:** make them on the MacBook Air (or with Claude Code), push to GitHub, then run `./sandbox.sh update` on the mini.

## Part 5: Your daily routine

| When | What | Where |
|---|---|---|
| 8:00 AM (HST) | The mini starts the day's work on its own: new episodes, clips, leads | – |
| Breaks / lunch | **Review** tab: approve the good clips, reject the rest (2 minutes) | S24 |
| Evening | **Overview**: anything under *Needs attention*? | S24 |
| Weekly | **Leads** tab: *Draft pitch* on the top few, send from your email | MacBook Air |

The work window, timezone and posting times are all in `config.yaml` (`worker:` and `posting:`).

## Part 6: Going live with The Fantasy Footballers

1. **Pitch with samples.** Download your best 3–5 clips on the S24 and send them along with the pitch. Lead with the angle that
   their DFS, betting and dynasty shows have no short-form home.
2. **When they say yes,** set up the three free developer apps from the MacBook Air's browser (`README.md` §5). Allow an hour or
   two, plus however long each platform's review takes.
3. **On the Mac mini:**
   ```bash
   tailscale funnel --bg 8787          # makes the connect page public
   open -e ~/sandbox-data/clipper/.env # PUBLIC_BASE_URL=https://mac-mini.tail1234.ts.net + the app keys
   ./sandbox.sh restart && ./sandbox.sh doctor
   ```
4. **Dashboard → Creators → Create connect link.** Send the link to them. They sign in to each show's YouTube, TikTok and
   Instagram themselves (they'll likely create new accounts for DFS, Betting and Dynasty).
5. **Flip them on:** in `config.yaml`, change `status: prospect` to `status: client`, then run `./sandbox.sh restart`.
6. From then on, every new episode gets clipped, you approve from your phone, and clips post at 12:15 PM and 7:30 PM Eastern on
   each account.

Before step 5, do a dry run on accounts you own. Add a second creator block in `config.yaml` for a YouTube channel you own,
connect your own test accounts with its connect link, and approve one clip to watch it go all the way through.

## If something's off

| Symptom | Fix |
|---|---|
| Phone can't load the dashboard | Is Tailscale connected on the phone? Run `./sandbox.sh status` on the mini (via SSH from the MacBook Air). |
| No new clips | Check whether it's inside work hours (Overview shows it), or tap **Run now**. Then check the Episodes page for errors. |
| YouTube download or caption errors | `./sandbox.sh update` gets the newest yt-dlp. |
| Mini rebooted and nothing runs | Log in on the mini once (FileVault). In Docker mode, also make sure Docker Desktop starts at login. |
| Anything else | `./sandbox.sh doctor` and `./sandbox.sh logs`. The full reference is in `README.md`. |
