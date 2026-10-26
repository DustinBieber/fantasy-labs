# Kalshi Edge

A local Kalshi research dashboard with paper trading and an optional AI trader. It runs on your Mac Mini.
You open it from your MacBook Air and your Galaxy S24 Ultra.

It covers:
- the market scanner, with history snapshots;
- the edge engine for NFL moneylines, spreads, totals, and player props, plus the AAA gas ladder;
- NFL parlay tickets and following proven bettors;
- a trade log with simulated maker fills;
- the **AI trader**. It runs on paper by default. It can only trade real money after it passes a 2-week
  go-live test, you add a Kalshi API key, and you switch it to Live yourself. It can never deposit or
  withdraw money, and it never asks for your Kalshi password.

Run the app on **one computer only**: the Mac Mini. Two copies would mean two AI traders placing orders.

## Set it up: Mac Mini, MacBook Air, Galaxy S24 Ultra

The Mac Mini is the server and stays on. It also runs Family Hub: Family Hub uses port 8000, and Kalshi
Edge uses **8100**, so the two don't collide. The MacBook Air and the phone are just screens.

### Part 1: Mac Mini (about 15 minutes, mostly waiting)

If you already set the Mac Mini up for Family Hub (always on, automatic login, Tailscale), skip to step 3.

1. **Keep it on.** Open System Settings → Energy (on older macOS: Energy Saver) and turn on:
   - Prevent automatic sleeping when the display is off;
   - Start up automatically after a power failure.

   Then open System Settings → Users & Groups → **Automatically log in as** → your account. If that's
   greyed out, FileVault is on; either turn FileVault off or log in by hand after every restart.
2. **Optional: install Tailscale** for access away from home. Get it from tailscale.com/download/mac and
   sign in. The installer then gives the dashboard a private https address.
3. **Download.** Open Terminal (⌘ Space, type *Terminal*) and paste:
   ```bash
   cd ~ && rm -rf fantasy-labs && curl -L -o fantasy-labs.zip https://github.com/DustinBieber/fantasy-labs/archive/refs/heads/main.zip && unzip -q fantasy-labs.zip && mv fantasy-labs-main fantasy-labs && rm fantasy-labs.zip
   ```
   This is safe to repeat. Kalshi Edge keeps its data in `~/KalshiEdge` and Family Hub keeps its data in
   `~/FamilyHub`; neither lives in `~/fantasy-labs`.
4. **Install:**
   ```bash
   bash ~/fantasy-labs/kalshi-edge/mac/setup.sh
   ```
   It will:
   - get Python if your Mac's is too old (no password needed);
   - install the app into `~/KalshiEdge` and run the test suite;
   - ask you to **choose a dashboard password**, or press Enter and it makes one and shows it to you once;
   - ask for an optional **Odds API key** (free at the-odds-api.com; it's what prices spreads, totals, and
     props);
   - check every live data source once and print a **VERIFY** list. This takes a few minutes. The Kalshi
     API key and Telegram lines are expected to fail for now.
   - start the background service, which starts at login and restarts itself;
   - offer to turn the AI trader on in **PAPER** mode. Answer **y**.
   - print your addresses;
   - offer to turn off sleep. Answer **y** and type your Mac password if it asks.

   If macOS asks whether Python may accept incoming network connections, click **Allow**.

**Everyday commands** (Terminal on the Mac Mini):

| Command | What it does |
|---|---|
| `~/KalshiEdge/kalshi status` | Is it running? Shows the trader's mode, equity, and go-live checklist. |
| `~/KalshiEdge/kalshi stop` | Emergency stop: cancels its resting orders. Resume on the dashboard. |
| `~/KalshiEdge/kalshi update` | Installs the latest version from GitHub. Your data and settings are kept. It runs the tests first and rolls back if the new version won't start. |
| `~/KalshiEdge/kalshi restart` | Restarts the app. Do this after editing `~/KalshiEdge/kalshi-edge.env`. |
| `~/KalshiEdge/kalshi smoke` | Re-checks the live data sources. |
| `~/KalshiEdge/kalshi logs` | Watches the log (Ctrl+C to stop watching). |
| `~/KalshiEdge/kalshi uninstall` | Removes the service. Your data stays in `~/KalshiEdge`. |

### Part 2: Addresses

- **At home:** `http://<Mac Mini IP>:8100`. The installer prints it.
- **Anywhere:** `https://<your-mac>.<tailnet>.ts.net:9443`, if Tailscale is installed. The installer prints
  this too.
  - This address is **private to your own Tailscale devices**, so the phone and MacBook need the Tailscale
    app, signed in to the same account.
  - Unlike Family Hub, it is deliberately **not** published on the public internet with Funnel, because
    these are trading controls. Port 9443 can't be used by Funnel even by mistake.
  - If the installer says the private address didn't work, turn on **MagicDNS** and **HTTPS
    Certificates** at login.tailscale.com → DNS, then run the installer again.
- Tailscale only connects your own devices. Your Kalshi traffic always comes from the Mac Mini at home in
  Hawaii. Never turn on an "exit node" or any VPN for Kalshi.

### Part 3: MacBook Air (2 minutes)

1. Open Safari or Chrome to one of the addresses above.
2. Sign in with any username and your dashboard password, and let the browser save it.
3. Add a shortcut:
   - Safari: File → Add to Dock.
   - Chrome: ⋮ → Cast, save, and share → Install page as app.

Don't install the app itself on the MacBook. Two copies would mean two AI traders.

### Part 4: Galaxy S24 Ultra (5 minutes)

1. **The dashboard as an app.** Open **Chrome** to your address and sign in, then tap **⋮** → **Add to home
   screen** → **Install**. With the `https://…ts.net:9443` address (Tailscale app on and signed in), it
   opens full screen like an app. With the `http://` home address it's a shortcut.
2. **Alerts (Telegram).** The AI trader texts you fills, stops, errors, a daily summary, and parlay
   tickets.
   1. Install **Telegram** from Google Play.
   2. Message **@BotFather**, send `/newbot`, and copy the token it gives you.
   3. Send your new bot any message.
   4. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":`.
   5. On the Mac Mini, run `open -e ~/KalshiEdge/kalshi-edge.env` and fill in `TELEGRAM_BOT_TOKEN` and
      `TELEGRAM_CHAT_ID`.
   6. Run `~/KalshiEdge/kalshi restart`, then `~/KalshiEdge/kalshi smoke`. Your phone gets "alerts work".
3. Set **Telegram** and **Tailscale** to battery **Unrestricted** (Settings → Apps → the app → Battery), or
   Samsung may silence them.

### Part 5: The AI trader, from paper to real money

1. **Today:** it's in **PAPER** (the installer asked). If you answered no, tap **PAPER** on the AI Trader
   tab or run `~/KalshiEdge/kalshi paper`. It scans every 2 minutes and places pretend maker orders on
   Tier S and Tier G edges (G at half size) at real prices.
2. **For 14 days, let it run.** Check the AI Trader tab, `~/KalshiEdge/kalshi status`, or the daily
   Telegram summary.
3. **When every box is green:**
   1. On kalshi.com, open Account & security → **API Keys** → **Create Key**. If it asks about
      permissions, allow trading.
   2. Move the downloaded private key file:
      ```bash
      mkdir -p ~/.kalshi && mv ~/Downloads/<the key file> ~/.kalshi/kalshi-edge.pem && chmod 600 ~/.kalshi/kalshi-edge.pem
      ```
      Never paste the key into a chat or an email.
   3. In `~/KalshiEdge/kalshi-edge.env`, set `KALSHI_API_KEY_ID` (shown on screen) and
      `KALSHI_PRIVATE_KEY_PATH='/Users/<you>/.kalshi/kalshi-edge.pem'`.
   4. Run `~/KalshiEdge/kalshi restart`, then `~/KalshiEdge/kalshi smoke`. "Kalshi API key signs
      requests" should PASS and show your cash balance.
   5. Deposit the $500 yourself in the Kalshi app. The trader can't move money.
   6. On the AI Trader tab, tap **Run live check**. It places 1¢ orders that can't fill, confirms Kalshi
      recorded them exactly, and cancels them.
   7. Tap **LIVE** and type `LIVE` to confirm.
4. **Anytime: tap STOP ALL**, or run `~/KalshiEdge/kalshi stop`. It also stops itself, with an alert, if:
   - it is down $25 in a day;
   - it is down $50 in total;
   - it hits 3 errors in a row.

### For development: run it in a terminal
`./start.sh` runs the app in the foreground from this folder, on port 8100, with its own `.env` and
`data/`. `./start.sh test` runs the tests. Use the installer above for the real, always-on copy.

### Optional: The Odds API key
1. Get a free key at https://the-odds-api.com. The free tier is 500 credits a month.
2. The installer asks for it. To add it later, set `ODDS_API_KEY` in `~/KalshiEdge/kalshi-edge.env` and
   run `~/KalshiEdge/kalshi restart`.
3. The default pull asks for US and EU books (`us,eu`), which adds Pinnacle. When a sharp book (Pinnacle, Circa, LowVig, BetOnline) is in the pull, its devigged line is the
   fair price; otherwise it's the median of all books. Each pull costs 2 credits, and the default 240-minute
   poll uses about 360 credits a month. Use "Run now" on the Audit tab to refresh right before you trade.

Without a key, the fair price falls back in this order:
1. lines you enter by hand on the Sports tab;
2. ESPN's single sportsbook line, labeled low confidence.

### Prove it on live data
```
~/KalshiEdge/kalshi smoke
```
The installer runs this once on its first install. In a development checkout, use `./start.sh smoke`.
This runs every data job once against the live public sources and prints a sample report:
- markets per board;
- NFL games with Kalshi prices and fair values;
- the AAA value, ladders, and implied median;
- calibration and the gas model check;
- the fee type Kalshi reports for each series;
- the top 10 edges, with the full math for the best one;
- data freshness for each source.

It ends with a **VERIFY** checklist: one PASS/FAIL line for each assumption the app depends on, such as the
API host, price fields, price grid, fee types, NFL and gas tickers, the historical archive, AAA, and ESPN.
Each FAIL comes with a hint about what to do.

It writes into the app's database, so the dashboard is populated afterwards. Add `--temp` to use a throwaway
database instead.

### Run the tests
```
./start.sh test
```
There are 115 tests:
- fee math (fee types, event overrides, $0.0001 rounding), devig math, Kelly sizing, tier logic;
- the maker price on Kalshi's price grid, and sharp-book fair prices;
- gas ladder calibration, implied median, the trap rule, the model, the no-look-ahead history, and the
  model-vs-Kalshi score;
- parsers for each source in Kalshi's current format (`*_dollars`, `*_fp`, `price_ranges`,
  `settlement_value_dollars`, historical candles);
- an end-to-end test of scan, snapshots, NFL edges, gas with the historical archive, paper fill,
  settlement, CSV export, and stale-data handling, run against a mocked Kalshi API in its current format;
- spread/total/prop pricing (exact, vig-scaled, model fallback, sharp-book preference, name matching, one-
  sided TDs left unrated) and an end-to-end run of Kalshi ladders priced from a bulk and per-game Odds API
  pull, rated, capped by game, and used as mixed parlay legs;
- bettor grading (proof rule, market-maker filter, full season), Polymarket v1/v2 parsing, parlay math
  (max price, leg rules, payouts on losses and ties), and an end-to-end run that grades three bettors,
  follows the proven one, builds parlays, and logs and settles one;
- the AI trader:
  - request signing (RSA-PSS and Ed25519, verified on every call) and Kalshi's V2 order mapping;
  - paper cycles that respect every limit and cancel orders whose edge is gone;
  - the daily and total stops, STOP ALL, and Resume;
  - the go-live test (pass, beaten by the S&P, too few trades);
  - live mode staying locked until the key, password, live check, test, and `chmod 600` are all in place;
  - live orders that fill and get killed;
  - sizing from its own $500 with the per-game cap;
  - the tax ledger, the S&P 500 feed with its fallback, and a Telegram token that never reaches the logs.

## What's on screen

| Tab | What it does |
|---|---|
| Top bar | Bankroll, open exposure, today's P&L, and unrealized P&L. One freshness chip per source; a chip turns red with the last good time when its data is stale or failing. |
| AI Trader | Off / Paper / Live switch, **STOP ALL**, equity vs the S&P 500, today's P&L against the stops, the go-live checklist with the live check, hard limits and market switches, what it holds, its activity log, and taxes with a CSV ledger. The header badge shows its mode on every tab. |
| Top Edges | Props first (rated in Phase 2), then NFL game winners, then the AAA gas ladder. Tier badges. Tap a row to see the reason in plain English, the full math, sources with pull IDs, a 24-hour chart, and the trade form. |
| Sports | Spreads, totals & props: strike counts per game, the rated strikes, a per-game **Load alt lines + props** button, and manual line entry. Below that, one card per NFL game: Kalshi bid/ask, fair price, 1-hour and 24-hour moves, sparklines, and ESPN context. |
| Parlays | NFL parlay tickets (Kalshi Combos) from +EV moneyline, spread, total, and prop legs, one per game. Each has the legs, fair %, estimated quote, max quote to accept, size, step-by-step setup for Kalshi's combo builder, a quote checker, and a log form. |
| Follow | Bettors graded from their public Polymarket NFL record. Proven ones (your proof rule) are followed: their open NFL game picks are mapped to Kalshi, compared with our fair price, and graded when the game settles. |
| Commodities & Econ | The deceleration-trap warning, AAA trends over 1/3/7/14 days, every open gas ladder (Kalshi vs model vs fair, implied median), and median calibration against actual AAA prints. |
| Markets & History | Every open market by board. Search, sort, a price and volume history chart, and a ★ to watch a ticker (1-minute snapshots). |
| Trades | Paper and real trade log, open positions, resting maker orders, P&L by tier, closing line value, correlated-exposure warnings, and CSV export. |
| Settings | Bankroll, Kelly fraction, caps, tier gates, fee rates, gas model, staleness limits, watched tickers, news feeds, and the expert roster with proof grades. |
| Audit | Every data job and every HTTP pull, with time (HST), status, rows, and errors. Any price on screen links to the pull it came from. |

All times are Pacific/Honolulu.

## Parlays and following proven bettors

### Spreads, totals, and player props
- **What Kalshi lists:** NFL spreads (`KXNFLSPREAD`, "Denver wins by over 3.5"), totals (`KXNFLTOTAL`,
  "Over 44.5 points"), and player prop ladders ("A.J. Brown: 60+ receiving yards"). Props cover receiving,
  rushing, and passing yards, receptions, passing TDs, and touchdowns. Any `KXNFL…` market with a
  "Player: N+ stat" title is picked up, so new prop series work automatically.
- **How each strike is priced, best first:**
  1. **Exact:** a sportsbook's two-sided line at that exact number (main or alternate), vig removed.
  2. **Scaled (props only):** a one-sided alternate milestone (e.g. "80+ yards +300"), scaled by the same
     book's vig on its two-sided main line for that player. Labeled on screen.
  3. **Model (spreads/totals only):** a normal distribution fitted to the main line (NFL margin sd 13.5,
     total sd 10). It ignores key numbers (3, 7, 10), so a model-priced strike is **capped at Watch**, and
     parlays skip it by default.
  4. Anything else stays unrated. One-sided anytime-TD prices are never devigged by guesswork.
- **Getting the lines:**
  - Spreads and totals for every game come with the regular Odds API pull.
  - Alternate lines and player props are pulled per game with **Load alt lines + props** on the Sports
    tab. It costs about 2 credits plus 2 per prop stat, and only requests stats Kalshi lists for that game.
  - No key? Enter any two-sided spread, total, or prop line by hand on the Sports tab.
- **One game, one risk bucket:** every market on the same game (winner, spread, total, props) shares the
  game's 8% correlated cap. A parlay counts against every game in it.
- **Odds API credits:** the free tier is 500 credits a month. The default bulk pull (h2h, spreads, totals ×
  US + EU) is 6 credits, twice a day, about 360 a month, which leaves little room for prop pulls. If you'll
  play props or spreads every week, the $30/month plan (20,000 credits) lets you poll every 30 minutes and
  load props for every game. Then set Settings → intervals → Odds API to 30 and staleness → Odds API to 60.

### Parlays (Kalshi Combos)
- Kalshi calls parlays **Combos**. A combo pays $1 only if every leg wins. You build it in the Kalshi app,
  and a market maker quotes the price. Requesting and accepting quotes needs a logged-in account, so this
  app never does either. It tells you exactly what to build and what price to accept.
- **Legs:** moneylines, spreads, totals, and player props, **one per game**. Only legs where our fair price
  beats the Kalshi ask are used. A parlay of fair-priced legs is not +EV, just higher variance.
- **Leg pricing rules:** model-priced legs are excluded by default. Vig-scaled prop legs are allowed and
  flagged. Both are switches in Settings.
- **Different games only:** legs from different games are independent, so fair = the product of the leg
  fair prices. Same-game legs (a team's spread with its QB's yards, say) are correlated and stay out until a
  correlation model exists.
- **What each ticket shows:**
  - our estimate of the quote, which is the product of the leg asks;
  - the **max quote to accept**: the highest price where net EV after the taker fee is still at least 10%
    (editable);
  - the tier under your usual rules. G needs a proven bettor agreeing on every leg.
- **Sizing:** quarter Kelly with your caps. Each leg's game counts toward that game's 8% correlated cap.
- **Logging and settlement:** log the parlay at the exact quote you got. It settles as the product of the
  legs' settlement values: $0 as soon as any leg loses, and a $0.50 tie leg halves the payout.

### Following proven bettors
Kalshi trades are anonymous, and Kalshi's social profiles have no public API. Polymarket's records are
public and on-chain, so the app grades bettors there:
1. **Discover** (daily): wallets from Polymarket's sports leaderboard (all-time and month).
2. **Grade:** each wallet's *resolved NFL positions only*: picks, W-L, profit, units (profit ÷ average
   stake), ROI, and seasons covered. Pick weight follows your proof rule: **200+ NFL picks or a full
   season, with positive units**. Proof never transfers from other sports.
3. **Exclude market makers:** wallets that held both sides of the same game in more than 30% of markets look
   like market makers, not bettors, and stay context only. A warning shows when three bets produced most of
   the profit.
4. **Follow** (every 15 minutes): proven wallets' open NFL game-winner positions ($200 and up) are mapped to
   the matching Kalshi market and become expert picks.
   - Tier G needs proven agreement, and a proven bettor on the other side forces Pass.
   - Positions they exit stop counting.
5. **Grade them ourselves:** when a game settles, each followed pick is graded. Our own rolling grade
   turns on after 50 picks.
6. **Never chase:** each pick shows the Kalshi price next to our fair price. If Kalshi has already moved
   past fair, the row says "don't chase."
7. **Add your own:** you can grade any specific Polymarket wallet on the Follow tab.

Polymarket is used only as public data. The app never routes you there.

## AI trader

You asked for this in writing. It is built so that a bug or a bad week can't cost more than the limits
you set.

### What it does every 2 minutes
1. **Live only:** syncs fills from Kalshi.
2. Checks the stops.
3. Recomputes every edge with fresh prices.
4. **Cancels resting orders** that are older than 30 minutes, whose edge is gone, whose market closes within
   15 minutes, or whose price moved.
5. **Places new maker orders** (post-only, so they never pay the taker fee) on Tier S and Tier G edges.
   Sizing is quarter Kelly on the trader's **own** equity, not your manual bankroll. Each order is then
   clipped to the limits below, and each game (or the whole gas ladder) is capped at 8% of the trader's
   equity, counting what it already holds there, parlays included.
6. Never doubles up on a ticker. Skips prices older than 5 minutes.

### Hard limits (editable on the AI Trader tab)

| Limit | Default |
|---|---|
| Trader bankroll | $500 |
| Max per order | $15 |
| Max open at once (positions + resting orders) | $150 |
| Max new orders a day | 25 |
| Daily loss stop | $25 → stops and alerts you |
| Total loss stop | $50 (equity $450) → stops and alerts you |
| Parlays | 2 a day, $5 each |
| Errors | 3 in a row → stops |

Your rules' per-strike (2–3%) and correlated (8% per game) caps also apply, measured against the trader's
own equity.

The trader keeps a **separate book** from yours. Its trades show on the Trades tab with an **AI** tag, but
they never count against your manual bankroll, your P&L, or your caps, and yours don't count against its.

### Markets
Each of these has an on/off switch:
- NFL singles (moneyline, spread, total, props);
- NFL parlays;
- AAA gas;
- **Sports at all**, a master switch for Hawaii's legal situation (see below).

Proven bettors' picks count as expert agreement or disagreement. Agreement is what lets a Tier G trade go
through; disagreement turns it into a Pass.

**Parlays in Live** are not placed automatically yet. Kalshi's combo (RFQ) order flow can't be tested
without real money, so in Live the trader texts you the ticket with the maximum price to accept, and you
place it in the Kalshi app. In Paper it logs them itself.

### The go-live test
After at least 14 days of paper trading, all of these must be true:
- 10 or more settled trades;
- a profit after fees;
- **beat the S&P 500 over the same days**;
- entry prices beat closing prices on average (CLV ≥ 0);
- no errors in the last 3 days.

You also need:
- the Kalshi key, with its file set to `chmod 600`;
- `APP_PASSWORD`;
- a passed live check within the last 7 days.

Then you switch to Live yourself, by typing LIVE.

**About beating the S&P 500.** Nobody can promise that, and this app doesn't. It measures it instead: the
AI Trader tab charts the trader's equity against the same money in the S&P 500 over the same days, using
FRED's daily S&P 500 closes, with Yahoo's SPY feed as a fallback.

Ten trades over two weeks is a small sample. Closing line value is the more honest early signal, which is
why it's part of the test. Keep watching the chart after you go live. If the trader is behind the S&P after
a full season, turn it off.

"Stay active" means it scans all day, every day. It trades only when an edge clears the rules. Some days
that will be zero trades, and that's correct.

## Taxes (read this before going live)
This is not tax advice. Use a CPA.

**Records.** On the AI Trader tab → Taxes, **Download tax ledger (CSV)**. It has one row per closed
real-money trade: dates acquired and closed, proceeds, cost basis (with fees), and gain or loss. Hand it to
your CPA with Kalshi's own account statements. Paper trades aren't taxable and aren't in it.

**No 1099 for your trading.** Kalshi generally doesn't send a 1099-B for event-contract gains, but the gains
are still taxable, federally and in Hawaii. Promotions or rewards may come on a 1099-MISC.

**Treatment is unsettled.** The IRS hasn't ruled whether event contracts are capital gains, Section 1256
contracts, or gambling income. The answer changes your rate and whether losses offset gains, which is why
the ledger records everything a CPA needs for any of the three.

**The set-aside** (default 30% of net gains) is shown so you don't spend the tax money. If gains get large,
ask your CPA about quarterly estimated payments (IRS 1040-ES and Hawaii N-1).

## Regulatory notes for Hawaii
- **Kalshi only.** Kalshi is a CFTC-regulated exchange. The app never routes you to a sportsbook or DFS
  site. Sportsbook odds are used only as a fair-price reference.
- **Sports contracts are contested in court. Gas and economic markets aren't affected.** Hawaii is in the
  Ninth Circuit, which ruled against Kalshi on sports contracts in the Nevada case in August 2026. I found no
  Hawaii action as of this writing.
  - If Kalshi stops offering sports contracts in Hawaii, the trader simply finds no sports markets.
  - If you'd rather sit sports out while it's unsettled, untick **Sports markets at all**.
  - Never use a VPN, proxy, or Tailscale exit node to reach markets that aren't offered where you are.
- **Your account, your key.** The API key trades only your own account. It can't deposit or withdraw, and
  you can revoke it on kalshi.com at any time. The app never asks for your Kalshi password.
- **Automated trading** through Kalshi's API is what the API is for. The app keeps to Kalshi's rate limits,
  uses post-only orders, and cancels stale orders.

## The rules as coded

### Fees
The Kalshi fee schedule is coded as config in `app/config.py` and editable in Settings.
- **Taker fee:** `0.07 × fee_multiplier × C × P × (1−P)`.
- **Maker fee:** the taker fee times a ratio set by the series' `fee_type`:

  | `fee_type` | Maker fee |
  |---|---|
  | `quadratic` | 0 |
  | `quadratic_with_maker_fees` | 0.25 × taker (0.0175 at multiplier 1) |
  | `quadratic_with_combo_maker_fees` | 0.5 × taker |

- **Where the fee type comes from:** it's read live from Kalshi's `/series`. An event's `fee_type_override`
  wins over the series. Until a series loads, or if its type is unknown, the conservative defaults apply:
  0.0175 maker fee, and 0.035 taker for INX/NASDAQ100.
- **Rounding:** each order's fee is rounded up to $0.0001, the precision Kalshi uses for direct members. If
  you trade through a non-direct member, set it to 0.01 in Settings.
- **Headline EV:** always net of the fee at the recommended contract count.

### Maker price
- The app reads each market's valid price grid from Kalshi (`price_ranges`). Some markets trade in 0.1¢ or
  0.01¢ steps.
- It posts one grid step above the best bid, never at or through the ask. With a one-step spread, it joins
  the bid.
- The taker ask is shown next to it, along with what taking it would do to EV.

### Tiers (`app/engine/tiers.py`)

| Tier | Rule |
|---|---|
| S | Edge **above** 10 pts **and** net EV **above** 20%. |
| G | Edge ≥ 5 and net EV ≥ 10%, **with** a proven expert agreeing. Half size. |
| W (Watch) | Edge ≥ 3, or a proven expert at a fair price. No size. |
| Pass | Below 3 points, any proven expert disagreeing, or stale data. |

Two judgment calls are flagged for your review:
- An edge above 10 points that misses the 20% EV gate drops to the G check.
- G-level numbers without proven expert agreement show as Watch.

### Sizing
1. Start at 0.25 × Kelly, using cost = price + fee.
2. Cap at 2.5% of bankroll per strike.
3. Cap at 8% per correlated group. Each NFL game is one group; all AAA gas strikes share one group.
4. Round down to whole contracts so that price × contracts + the rounded fee fits.
5. Max loss is shown before you act.

### Equivalent outcomes
YES on one team and NO on the other team are the same bet. Top Edges keeps the better price and notes the
other one.

### Experts
- Pick weight requires a published or independently graded record in the **same sport**:
  - 200 or more picks, or a full season;
  - positive units.
- Anyone else is context only.
- Our own rolling grade turns on after 50 graded picks.

### NFL fair price
- Proportional devig of each book: implied ÷ sum of implied.
- If a sharp book is in the pull, its line is the fair price. Otherwise it's the median across books. The
  steps are shown line by line.
- A game that has started is Pass: the pregame price no longer applies.
- A tied game settles at Kalshi's settlement value ($0.50 per side), and the trade log handles that.

### AAA gas fair price
**Model** (from official AAA history only):
- mean = latest AAA + 0.7 × the average of the last 3 daily changes × days ahead;
- sigma = the RMS of that rule's own past one-day errors (floor 0.4¢/day) × √days.

**Fair price:** 50% model probability (clamped to 1–99%) + 50% Kalshi's own monotone-smoothed ladder
probability. Kalshi's implied medians have been well calibrated, so the model is shrunk toward them.
"Above" means strictly above: an AAA print equal to the strike resolves NO. The model applies that
half-tick correction on AAA's $0.0001 grid.

**Model check:** before any gas bet can rate above Watch, the blend has to beat Kalshi alone on Brier score
across at least 10 settled days. Each day is priced using only AAA values that were public at the time (no
look-ahead). The Commodities tab shows the scores for each model weight and the best weight on your
history. Until the model passes, gas edges stay capped at Watch with the reason shown.

**Deceleration trap:** if AAA rose over 3 days, any bet that wins only if the price falls below today's
AAA is forced to Pass with a bold warning. The mirror rule applies to falling trends.

**Calibration:** each settled ladder is rebuilt as it stood 6 hours before close from Kalshi's hourly
candles, about noon HST, after AAA has published the prior day. Our own snapshots add the same record going
forward. Each is graded against the AAA value Kalshi settled on. Markets settled before Kalshi's historical
cutoff are read from the `/historical` endpoints, so the backfill covers the full 30 days.

### Snapshots and history
Snapshotting every open Kalshi market every minute would add tens of millions of rows a day. Instead:
- the active set (NFL, gas, watched tickers, the top 40 by volume, and anything you hold) is snapshotted
  every minute;
- every other market is snapshotted at each full scan, but only when its price, volume, or status changed.

Nothing is ever deleted.

### Paper fills
A paper maker order rests until a later snapshot shows the ask at or through your price, or a trade printed
at or below it. If the market closes first, the order is cancelled. Tick "assume filled" to skip this.

### Stale data
Each source has a staleness limit, editable in Settings. A Kalshi price older than its limit forces Pass.
Failing sources show red with the last good time. Prices, headlines, and expert records are never made up.

## Files

```
start.sh                 development: run | test | smoke
mac/setup.sh             the Mac Mini installer and updater (also installs the ~/KalshiEdge/kalshi helper)
app/main.py              FastAPI app; starts the scheduler
app/api.py               JSON API used by the dashboard
app/config.py            .env loading and every editable default (fees, tiers, caps, intervals)
app/db.py                SQLAlchemy Core schema (SQLite now, Postgres by changing DATABASE_URL)
app/sources/             kalshi.py, kalshi_auth.py (signed orders), polymarket.py, espn.py, odds_api.py, aaa.py,
                         benchmark.py (S&P 500), notify.py (Telegram), http.py (retries, rate limits, pull log)
app/engine/              fees, devig, kelly, tiers, maker, edge, gas, lines, parlay, follow, experts, teams (pure, tested)
app/services/            scanner, nfl_service, lines_service, parlay_service, follow_service, gas_service, portfolio,
                         edges, jobs, autotrader (the AI trader), tax (ledger)
static/                  index.html, app.js, app.css (compiled Tailwind), vendor/chart.umd.js
frontend/                Tailwind source and config (only needed if you change the dashboard's classes)
scripts/smoke_live.py    live-data proof
scripts/trader_cli.py    terminal status / paper / stop for the running app
tests/                   pytest suite
data/                    your database (git-ignored)
```

## Moving to Postgres later
Install a driver (`.venv/bin/pip install "psycopg[binary]"`), set
`DATABASE_URL=postgresql+psycopg://user:pass@localhost/kalshi_edge` in `.env`, and restart. The tables are
created automatically. Every timestamp is stored as an integer epoch, so nothing is SQLite-specific.

## Editing the dashboard
`static/app.css` is compiled ahead of time, so the app needs no CDN while it runs. If you add new Tailwind
classes to `static/app.js` or `static/index.html`, rebuild it:
```
npx tailwindcss@3.4.17 -c frontend/tailwind.config.js -i frontend/app.src.css -o static/app.css --minify
```

## Hawaii legal lane
See [Regulatory notes for Hawaii](#regulatory-notes-for-hawaii). In short: Kalshi only, no sportsbooks or
DFS, no VPN or proxy, and the AI trader stays on paper until you switch it to Live yourself.
