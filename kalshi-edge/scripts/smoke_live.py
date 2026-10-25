"""Live proof: run every Phase 1 data job once against the real public sources, then print what the
dashboard will show. Writes into the app's database (data/kalshi_edge.db) unless --temp is passed.

    ./start.sh smoke            # uses the app database, so the dashboard is populated afterwards
    ./start.sh smoke --temp     # throwaway database
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

os.environ.setdefault("SCHEDULER_ENABLED", "false")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--temp", action="store_true", help="use a throwaway database")
    ap.add_argument("--skip-scan", action="store_true", help="skip the full scan (faster)")
    args = ap.parse_args()
    if args.temp:
        os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/smoke.db"

    from sqlalchemy import func, select

    from app import config, db
    from app.services import edges, jobs
    from app.timeutil import hst, now_ts

    print(f"Kalshi Edge live smoke test · {hst(now_ts())}")
    print(f"Kalshi API: {config.KALSHI_API_BASE}")
    print(f"Database: {config.DATABASE_URL}\n")
    order = ["kalshi_series", "kalshi_active", "espn_nfl", "odds_api", "aaa", "gas_backfill", "follow_discover",
             "follow_picks", "settle"]
    if not args.skip_scan:
        order.insert(0, "kalshi_scan")
    results = {}
    for name in order:
        t0 = time.time()
        res = jobs.run_job(name)
        results[name] = res
        print(f"  {jobs.JOBS[name]['label']:<32} {time.time() - t0:5.1f}s  {res}")

    print("\nOpen markets by board:")
    rows = db.fetch_all(select(db.markets.c.category, func.count().label("n")).where(
        db.markets.c.status.in_(("active", "open")), db.markets.c.close_ts > now_ts()).group_by(db.markets.c.category))
    for r in sorted(rows, key=lambda r: -r["n"]):
        print(f"  {r['category']:<24} {r['n']:>6}")

    data = edges.compute(force=True)
    nfl, gas = data["nfl"], data["gas"]
    print(f"\nNFL game markets: {len(nfl['games'])} games")
    for g in nfl["games"][:8]:
        f = g["fair"]
        teams = "  ".join(
            f"{t['team']} bid/ask {fmt(t['yes_bid'])}/{fmt(t['yes_ask'])} fair {pct(t['fair'])}" for t in g["teams"])
        print(f"  {g['title'][:40]:<40} {teams}  [{f['source'] if f else 'no fair yet'}]")

    a = gas["aaa"]
    print(f"\nAAA national average: {a['latest']} on {a['latest_date']}  trends: "
          f"{ {k: a['trends'].get(k) for k in ('chg_1d', 'chg_3d', 'chg_7d', 'chg_14d', 'decelerating')} }")
    for lad in gas["ladders"][:3]:
        print(f"  Ladder {lad['event_ticker']} resolves {lad['resolution_date']}: implied median {lad['implied_median']} "
              f"({lad['median_note']}), model {lad['model'].get('mu') or lad['model'].get('why')}")
        for r in lad["rows"]:
            print(f"     {str(r['label'])[:28]:<28} bid/ask {fmt(r['yes_bid'])}/{fmt(r['yes_ask'])}  fair YES {pct(r['fair_yes'])}")
    cal = gas["calibration"]
    print(f"  Calibration (Kalshi implied median vs AAA): {cal['market']}")
    print(f"  Calibration (our model median vs AAA):      {cal['model']}")

    print(f"\nTop edges: counts {data['counts']}")
    items = [o for grp in data["groups"] for o in grp["items"]]
    items.sort(key=lambda o: ({"S": 0, "G": 1, "W": 2, "P": 3}[o["tier"]], -(o.get("net_ev_pct") or -999)))
    for o in items[:10]:
        print(f"  [{o['tier']}] {o['subject'][:34]:<34} {o['side'].upper():<3} fair {pct(o['fair'])} maker {fmt(o['maker_price'])} "
              f"edge {o['raw_edge_pts']} pts  net EV {o['net_ev_pct']}%  size {o['size']['contracts']} ct")
    if items:
        best = items[0]
        print(f"\nFull math for the top row ({best['ticker']} {best['side'].upper()}):")
        for line in best["math"]:
            print(f"  - {line}")
        print(f"  Reason: {best['reason']}")

    sc = gas.get("model_score") or {}
    print(f"  Model check: {sc.get('verdict')}")
    if sc.get("brier"):
        print(f"  Brier by model weight (lower is better): {sc['brier']}")

    from app.services import follow_service, parlay_service
    fol = follow_service.feed(nfl)
    print(f"\nFollowed bettors: {fol['proven_count']} proven of {len(fol['wallets'])} graded")
    for w in fol["wallets"][:8]:
        st = w["stats"]
        print(f"  {'PROVEN ' if w['proven'] else 'context'} {w['name'][:18]:<18} {w['short']}  NFL {st.get('nfl_picks', 0)} picks, "
              f"{st.get('wins', 0)}-{st.get('losses', 0)}, ${st.get('pnl', 0):,.0f}, {st.get('units', 0):+.1f}u"
              + (f"  ({st['why_not']})" if st.get("why_not") else ""))
    for p in fol["picks"][:8]:
        print(f"  pick: {p['name'][:14]:<14} {p['label']:<40} ${p['size_usd'] or 0:,.0f}  -> {p['verdict']}")
    from app.services import lines_service
    lb = lines_service.board()
    print(f"\nNFL spreads/totals/props on Kalshi: {lb['counts']}; rated (book line at that number): "
          f"{sum(g['rated'] for g in lb['games'])}")
    for o in [o for o in lb["best"] if o["side"] == "yes"][:6]:
        print(f"  [{o['tier']}] {o['subject'][:44]:<44} fair {100 * o['fair']:.1f}% ({o['fair_kind']}) "
              f"maker {fmt(o['maker_price'])} net EV {o['net_ev_pct']}%")
    par = parlay_service.board()
    print(f"\nParlays: {len(par['tickets'])} tickets from {len(par['pool'])} +EV legs")
    for t in par["tickets"][:5]:
        print(f"  [{t['tier']}] {' + '.join(g['label'] for g in t['legs'])}: fair {100 * t['fair']:.1f}%, est quote "
              f"{100 * t['est_quote']:.1f}c, accept up to {('%.1fc' % (100 * t['max_price'])) if t['max_price'] else 'none'}, "
              f"net EV {t['net_ev_pct']:+.1f}%")

    print("\nSeries fee types from Kalshi:")
    for r in db.fetch_all(select(db.series)):
        print(f"  {r['series_ticker']:<14} fee_type={r['fee_type']} multiplier={r['fee_multiplier']}")

    print("\nVERIFY (each line is an assumption the app depends on):")
    priced = db.fetch_one(select(func.count().label("n")).select_from(db.markets).where(db.markets.c.yes_bid.isnot(None)))
    grids = db.fetch_one(select(func.count().label("n")).select_from(db.markets).where(db.markets.c.price_ranges.isnot(None)))
    bf = results.get("gas_backfill", {})
    espn_res = results.get("espn_nfl", {})
    checks = [
        ("Kalshi API reachable and scan parsed", "error" not in results.get("kalshi_scan", {"error": 1}) or args.skip_scan,
         "Check KALSHI_API_BASE in .env; https://api.elections.kalshi.com/trade-api/v2 is the fallback host."),
        ("Kalshi prices parsed (*_dollars fields)", (priced or {}).get("n", 0) > 0,
         "Markets loaded but no bids parsed: Kalshi may have renamed price fields. Send me one /markets response."),
        ("Kalshi price grid parsed (price_ranges)", (grids or {}).get("n", 0) > 0,
         "No price_ranges found: maker prices fall back to whole cents (still valid)."),
        ("Series fee types loaded", bool(db.fetch_all(select(db.series).where(db.series.c.fee_type.isnot(None)))),
         "Fees fall back to the conservative defaults until /series loads."),
        ("NFL game markets found (KXNFLGAME)", len(nfl["games"]) > 0,
         "No open NFL game markets: check the series ticker under Settings -> NFL."),
        ("Gas ladder found (KXAAAGASD and friends)", len(gas["ladders"]) > 0,
         "No open gas markets: check Settings -> Gas series tickers."),
        ("Settled gas history loaded (live + /historical)", isinstance(bf, dict) and bf.get("actuals", 0) > 0,
         f"Backfill result: {bf}"),
        ("Gas ladders graded for calibration", isinstance(bf, dict) and bf.get("calibration_marks", 0) > 0,
         "Candlestick fetch failed or returned nothing; the model check stays at Watch until graded."),
        ("AAA national average scraped", "error" not in results.get("aaa", {"error": 1}),
         "AAA blocked the scraper: enter the value by hand on Commodities & Econ (or rely on Kalshi settlements)."),
        ("ESPN odds parsed", isinstance(espn_res, dict) and espn_res.get("with_moneyline", 0) > 0,
         "ESPN returned no moneylines (normal if no upcoming games have odds); use manual lines or ODDS_API_KEY."),
        ("Kalshi NFL spread/total ladders found (KXNFLSPREAD / KXNFLTOTAL)",
         lb["counts"].get("spread", 0) + lb["counts"].get("total", 0) > 0,
         "None found: check Settings -> NFL spread/total series tickers (normal before lines are posted)."),
        ("Kalshi NFL player props parsed ('Player: N+ stat' titles)", lb["counts"].get("prop", 0) > 0,
         "No prop titles parsed: Kalshi may word them differently; send me one prop market's title."),
        ("Spreads/totals priced from The Odds API", "error" not in results.get("odds_api", {}) and
         results.get("odds_api", {}).get("spread_total_outcomes", 0) > 0 if "skipped" not in results.get("odds_api", {}) else False,
         "Needs ODDS_API_KEY (or enter lines by hand on the Sports tab)."),
        ("Polymarket leaderboard read (bettor discovery)", "error" not in results.get("follow_discover", {"error": 1})
         and results.get("follow_discover", {}).get("candidates", 0) > 0,
         f"Result: {results.get('follow_discover')}. Polymarket may have changed its Data API; send me this line."),
        ("Bettors graded on NFL records", results.get("follow_discover", {}).get("graded", 0) > 0,
         "No leaderboard wallet had resolved NFL positions, or closed-positions parsing failed."),
        ("At least one proven bettor (200+ NFL picks, positive units)", fol["proven_count"] > 0,
         "None passed your proof rule yet. That's fine; they just don't get pick weight."),
        ("Followed picks mapped to Kalshi tickers", "error" not in results.get("follow_picks", {"error": 1}),
         f"Result: {results.get('follow_picks')}"),
        ("The Odds API on", "skipped" not in results.get("odds_api", {}),
         "Optional: add ODDS_API_KEY to .env for sharp-book (Pinnacle) fair prices."),
    ]
    for label, ok, hint in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        -> {hint}"))

    print("\nData freshness:")
    for f in jobs.freshness():
        print(f"  {f['label']:<32} {f['state']:<6} last good {f['last_ok'] or 'never'}"
              + (f"  ERROR: {f['last_error']}" if f["state"] == "error" else ""))
    return 0


def fmt(x):
    return "--" if x is None else f"{100 * x:.0f}c"


def pct(x):
    return "--" if x is None else f"{100 * x:.1f}%"


if __name__ == "__main__":
    sys.exit(main())
