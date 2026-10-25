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
There are 77 tests:
- fee math (fee types, event overrides, $0.0001 rounding), devig math, Kelly sizing, tier logic;
- the maker price on Kalshi's price grid, and sharp-book fair prices;
- gas ladder calibration, implied median, the trap rule, the model, the no-look-ahead history, and the
  model-vs-Kalshi score;
- parsers for each source in Kalshi's current format (`*_dollars`, `*_fp`, `price_ranges`,
  `settlement_value_dollars`, historical candles);
- an end-to-end test of scan, snapshots, NFL edges, gas with the historical archive, paper fill,
  settlement, CSV export, and stale-data handling, run against a mocked Kalshi API in its current format.

## What's on screen

| Tab | What it does |
|---|---|
| Top bar | Bankroll, open exposure, today's P&L, and unrealized P&L. One freshness chip per source; a chip turns red with the last good time when its data is stale or failing. |
| Top Edges | Props first (rated in Phase 2), then NFL game winners, then the AAA gas ladder. Tier badges. Tap a row to see the reason in plain English, the full math, sources with pull IDs, a 24-hour chart, and the trade form. |
| Sports | One card per NFL game: Kalshi bid/ask, fair price, 1-hour and 24-hour moves, sparklines, ESPN context, and manual line entry. |
| Commodities & Econ | The deceleration-trap warning, AAA trends over 1/3/7/14 days, every open gas ladder (Kalshi vs model vs fair, implied median), and median calibration against actual AAA prints. |
| Markets & History | Every open market by board. Search, sort, a price and volume history chart, and a ★ to watch a ticker (1-minute snapshots). |
| Trades | Paper and real trade log, open positions, resting maker orders, P&L by tier, closing line value, correlated-exposure warnings, and CSV export. |
| Settings | Bankroll, Kelly fraction, caps, tier gates, fee rates, gas model, staleness limits, watched tickers, news feeds, and the expert roster with proof grades. |
| Audit | Every data job and every HTTP pull, with time (HST), status, rows, and errors. Any price on screen links to the pull it came from. |

All times are Pacific/Honolulu.

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
app/sources/             kalshi.py, espn.py, odds_api.py, aaa.py, http.py (retries, rate limits, pull log)
app/engine/              fees, devig, kelly, tiers, maker, edge, gas, experts, teams (pure math, fully tested)
app/services/            scanner, nfl_service, gas_service, portfolio, edges, jobs (scheduler + freshness)
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
