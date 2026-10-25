# Kalshi Edge (Phase 1)

A local, read-only Kalshi research dashboard with paper trading. It runs on your Mac Mini, and you open it
from your MacBook Air and phone on the same Wi-Fi.

Phase 1 covers:
- the market scanner, with history snapshots;
- the edge engine for NFL moneylines and the AAA gas ladder;
- a trade log with simulated maker fills;
- the Top Edges board.

There is no Kalshi login, no order placement, and no money movement anywhere in this code.

## Run it on the Mac Mini

1. Install Python 3.10 or newer if you don't have it. Check with `python3 --version`.
   If it's older, run `brew install python@3.12`. Homebrew is at https://brew.sh.
2. Put this `kalshi-edge` folder on the Mac Mini, for example in `~/kalshi-edge`.
3. Open Terminal and run:
   ```
   cd ~/kalshi-edge
   ./start.sh
   ```
   The first run creates `.env` from `.env.example`, builds a private Python environment in `.venv`, and
   installs packages. That takes about a minute.
4. If macOS asks whether Python may accept incoming network connections, click **Allow**. Your phone needs this.
5. Open **http://localhost:8000** on the Mac Mini.
6. On your MacBook Air or phone, open the second address `start.sh` printed, for example
   `http://192.168.1.23:8000`. On iPhone, tap Share → Add to Home Screen to get an app icon.
7. Data starts flowing within about a minute:
   - the full Kalshi scan runs every 10 minutes;
   - 1-minute snapshots cover NFL, gas, your watched tickers, and the 40 highest-volume markets;
   - ESPN updates every 15 minutes and AAA every hour;
   - gas settlement history and calibration backfill run at startup and then daily.
8. To stop the app, press Ctrl+C in Terminal. Running `./start.sh` again resumes with all history kept in
   `data/kalshi_edge.db`.

### Optional: The Odds API key
1. Get a free key at https://the-odds-api.com. The free tier is 500 credits a month.
2. Open `.env` and set `ODDS_API_KEY=your_key`.
3. Restart with Ctrl+C, then `./start.sh`. The default pull asks for US and EU books (`us,eu`), which adds
   Pinnacle. When a sharp book (Pinnacle, Circa, LowVig, BetOnline) is in the pull, its devigged line is the
   fair price; otherwise it's the median of all books. Each pull costs 2 credits, and the default 240-minute
   poll uses about 360 credits a month. Use "Run now" on the Audit tab to refresh right before you trade.

Without a key, the fair price falls back in this order:
1. lines you enter by hand on the Sports tab;
2. ESPN's single sportsbook line, labeled low confidence.

### Prove it on live data
```
./start.sh smoke
```
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
There are 98 tests:
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
  follows the proven one, builds parlays, and logs and settles one.

## What's on screen

| Tab | What it does |
|---|---|
| Top bar | Bankroll, open exposure, today's P&L, and unrealized P&L. One freshness chip per source; a chip turns red with the last good time when its data is stale or failing. |
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
start.sh                 one command: run | test | smoke
app/main.py              FastAPI app; starts the scheduler
app/api.py               JSON API used by the dashboard
app/config.py            .env loading and every editable default (fees, tiers, caps, intervals)
app/db.py                SQLAlchemy Core schema (SQLite now, Postgres by changing DATABASE_URL)
app/sources/             kalshi.py, polymarket.py, espn.py, odds_api.py, aaa.py, http.py (retries, rate limits, pull log)
app/engine/              fees, devig, kelly, tiers, maker, edge, gas, lines, parlay, follow, experts, teams (pure, tested)
app/services/            scanner, nfl_service, lines_service, parlay_service, follow_service, gas_service, portfolio,
                         edges, jobs
static/                  index.html, app.js, app.css (compiled Tailwind), vendor/chart.umd.js
frontend/                Tailwind source and config (only needed if you change the dashboard's classes)
scripts/smoke_live.py    live-data proof
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
- This app reads public Kalshi data and keeps a paper log.
- Sportsbook odds are used only as a fair-price reference. The app never routes you to a sportsbook or DFS
  site.
- Price shopping across legal prediction venues comes in Phase 2.
- Authenticated Kalshi access (Phase 4) is out of scope until you approve it in writing.
