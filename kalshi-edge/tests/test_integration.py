"""End-to-end test through the real jobs and HTTP API, with Kalshi/ESPN/AAA replaced by an
httpx.MockTransport. Fixture prices exist only inside this test."""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.sources import http

MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def tk(d: date) -> str:
    return f"{d.year % 100:02d}{MON[d.month - 1]}{d.day:02d}"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def d4(cents: float) -> str:
    """Kalshi's fixed-point dollar string, e.g. 55 -> "0.5500"."""
    return f"{cents / 100:.4f}"


CENT_GRID = [{"start": "0.0100", "end": "0.9900", "step": "0.0100"}]
DECI_GRID = [{"start": "0.0010", "end": "0.9990", "step": "0.0010"}]


def mkt(ticker, event, bid, ask, last=None, vol=0, vol24=0, oi=0, close=None, grid=CENT_GRID, **extra):
    """A market object in Kalshi's current shape: *_dollars prices, *_fp counts, price_ranges grid."""
    m = {"ticker": ticker, "event_ticker": event, "yes_bid_dollars": d4(bid), "yes_ask_dollars": d4(ask),
         "no_bid_dollars": d4(100 - ask), "no_ask_dollars": d4(100 - bid),
         "last_price_dollars": d4(last) if last is not None else "0.0000",
         "volume_fp": f"{vol:.2f}", "volume_24h_fp": f"{vol24:.2f}", "open_interest_fp": f"{oi:.2f}",
         "status": "active", "close_time": close, "price_level_structure": "linear_cent", "price_ranges": grid}
    m.update(extra)
    return m


class FakeKalshi:
    HOST = "external-api.kalshi.com"

    def __init__(self):
        now = datetime.now(timezone.utc)
        self.today = now.date()
        self.game_day = self.today + timedelta(days=2)
        self.ev_nfl = f"KXNFLGAME-{tk(self.game_day)}DALDEN"
        kick = iso(now + timedelta(days=2))
        self.nfl = {
            f"{self.ev_nfl}-DEN": mkt(f"{self.ev_nfl}-DEN", self.ev_nfl, 55, 58, 56, 5000, 900, 3000, kick,
                                      yes_sub_title="Denver", title="Dallas at Denver Winner?"),
            f"{self.ev_nfl}-DAL": mkt(f"{self.ev_nfl}-DAL", self.ev_nfl, 42, 45, 44, 4000, 800, 2500, kick,
                                      yes_sub_title="Dallas", title="Dallas at Denver Winner?"),
        }
        # Settled daily gas events for the last 14 days, rising 0.8c/day, slowing at the end. The older half
        # sits behind Kalshi's historical cutoff and is only served by /historical endpoints.
        self.cutoff = now - timedelta(days=7, hours=12)
        self.settled_live, self.settled_hist = [], []
        val = 3.0000
        for i in range(14, 0, -1):
            d = self.today - timedelta(days=i)
            val += 0.008 if i > 2 else 0.003
            ev = f"KXAAAGASD-{tk(d)}"
            close_dt = datetime(d.year, d.month, d.day, 3, 59, tzinfo=timezone.utc)  # 11:59pm ET the night before
            for k in (2.98, 3.03, 3.08):
                m = {"ticker": f"{ev}-{k:.3f}", "event_ticker": ev, "strike_type": "greater", "floor_strike": k,
                     "status": "finalized", "result": "yes" if val > k else "no",
                     "settlement_value_dollars": "1.0000" if val > k else "0.0000",
                     "expiration_value": f"{val:.4f}", "close_time": iso(close_dt)}
                (self.settled_hist if close_dt < self.cutoff else self.settled_live).append(m)
        self.latest = round(val, 4)
        tomorrow = self.today + timedelta(days=1)
        self.ev_gas = f"KXAAAGASD-{tk(tomorrow)}"
        gclose = iso(now + timedelta(hours=20))
        self.gas = {}
        for k, (b, a) in {3.06: (92, 95), 3.08: (70, 74), 3.10: (40, 44), 3.12: (12, 16)}.items():
            t = f"{self.ev_gas}-{k:.3f}"
            self.gas[t] = mkt(t, self.ev_gas, b, a, None, 300, 100, 0, gclose, DECI_GRID, strike_type="greater",
                              floor_strike=k, yes_sub_title=f"Above ${k:.2f}", title="US gas price tomorrow")
        self.calls = []

    def events_payload(self):
        return {"events": [
            {"event_ticker": self.ev_nfl, "series_ticker": "KXNFLGAME", "title": "Dallas at Denver",
             "category": "Sports", "markets": list(self.nfl.values())},
            {"event_ticker": self.ev_gas, "series_ticker": "KXAAAGASD", "title": "US gas prices tomorrow",
             "category": "Economics", "markets": list(self.gas.values())},
            {"event_ticker": "KXNFLANYTD-X", "series_ticker": "KXNFLANYTD", "title": "Anytime touchdown scorer",
             "category": "Sports",
             "markets": [mkt("KXNFLANYTD-X-1", "KXNFLANYTD-X", 30, 33, close=self.nfl[next(iter(self.nfl))]["close_time"])]},
        ], "cursor": ""}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        q = {k: v[0] for k, v in parse_qs(url.query.decode()).items()}
        self.calls.append((url.host, url.path, q))
        if url.host == self.HOST:
            path = url.path.replace("/trade-api/v2", "")
            if path == "/events":
                return httpx.Response(200, json=self.events_payload())
            if path == "/historical/cutoff":
                return httpx.Response(200, json={"market_settled_ts": iso(self.cutoff)})
            if path == "/historical/markets":
                rows = self.settled_hist if q.get("series_ticker") == "KXAAAGASD" else []
                return httpx.Response(200, json={"markets": rows, "cursor": ""})
            if path == "/markets":
                if q.get("status") == "settled" and q.get("series_ticker") == "KXAAAGASD":
                    return httpx.Response(200, json={"markets": self.settled_live, "cursor": ""})
                if q.get("series_ticker") == "KXNFLGAME" and q.get("status") == "open":
                    return httpx.Response(200, json={"markets": [m for m in self.nfl.values() if m["status"] == "active"]})
                if q.get("series_ticker") == "KXAAAGASD" and q.get("status") == "open":
                    return httpx.Response(200, json={"markets": list(self.gas.values())})
                if "tickers" in q:
                    allm = {**self.nfl, **self.gas}
                    return httpx.Response(200, json={"markets": [allm[t] for t in q["tickers"].split(",") if t in allm]})
                return httpx.Response(200, json={"markets": []})
            if "/candlesticks" in path:
                ticker = path.split("/")[-2]
                strike = ticker.rsplit("-", 1)[-1]
                bid = {"2.980": 85, "3.030": 45, "3.080": 10}[strike]
                end = int(q["end_ts"]) - 100
                if path.startswith("/historical/"):
                    if ticker not in {m["ticker"] for m in self.settled_hist}:
                        return httpx.Response(404, json={"error": "not archived"})
                    c = {"end_period_ts": end, "yes_bid": {"close": d4(bid)}, "yes_ask": {"close": d4(bid + 4)}}
                else:
                    if ticker not in {m["ticker"] for m in self.settled_live}:
                        return httpx.Response(404, json={"error": "settled before cutoff"})
                    c = {"end_period_ts": end, "yes_bid": {"close_dollars": d4(bid)},
                         "yes_ask": {"close_dollars": d4(bid + 4)}}
                return httpx.Response(200, json={"candlesticks": [c]})
            if path.startswith("/series/"):
                st = path.split("/")[2]
                return httpx.Response(200, json={"series": {"ticker": st, "title": st, "fee_type": "quadratic_with_maker_fees",
                                                            "fee_multiplier": 1}})
        if url.host == "site.api.espn.com":
            return httpx.Response(200, json={"events": []})
        if url.host == "gasprices.aaa.com":
            return httpx.Response(403, text="blocked")
        return httpx.Response(404)


@pytest.fixture
def fake(tmpdb):
    f = FakeKalshi()
    http.set_transport(httpx.MockTransport(f.handler))
    return f


@pytest.fixture
def client(fake):
    from app.main import app
    with TestClient(app) as c:
        yield c


def run(client, name):
    r = client.post(f"/api/jobs/{name}/run")
    assert r.status_code == 200, r.text
    return r.json()


def test_full_phase1_flow(client, fake):
    scan = run(client, "kalshi_scan")
    assert scan == {"events": 3, "markets": 7, "snapshots": 7}
    assert run(client, "kalshi_scan")["snapshots"] == 0  # change-only snapshots on full scans
    assert run(client, "kalshi_active")["snapshots"] == 6  # NFL + gas every minute
    assert run(client, "kalshi_series")["series"] >= 2
    cats = {c["category"]: c["count"] for c in client.get("/api/markets/categories").json()}
    assert cats["Sports"] == 2 and cats["Props"] == 1 and cats["Commodities and Energy"] == 4

    # AAA is blocked in this fixture: the job fails visibly and freshness shows it.
    assert "error" in run(client, "aaa")
    fr = {f["name"]: f for f in client.get("/api/status").json()["freshness"]}
    assert fr["aaa"]["state"] == "error" and fr["kalshi_scan"]["state"] == "ok"

    # No odds yet: NFL markets show no fair price and are not rated.
    nfl = client.get("/api/nfl").json()
    assert nfl["games"][0]["fair"] is None and nfl["opportunities"] == []

    # Manual line: DEN -200 (home) / DAL +170 -> fair DEN 0.6429
    r = client.post("/api/nfl/lines", json={"home_team": "DEN", "away_team": "DAL", "home_price": -200,
                                            "away_price": 170, "book": "My screen"})
    assert r.status_code == 200
    nfl = client.get("/api/nfl").json()
    assert nfl["games"][0]["fair"]["source"] == "manual"
    den = next(o for o in nfl["opportunities"] if o["ticker"].endswith("-DEN") and o["side"] == "yes")
    assert den["maker_price"] == 0.56 and den["raw_edge_pts"] == pytest.approx(8.29, abs=0.01)
    assert den["tier"] == "W"  # G-level numbers, no proven expert

    # A proven NFL expert agreeing flips it to G at half size; an unproven one would not.
    e = client.post("/api/experts", json={"name": "Graded Capper", "sport": "nfl", "proof_type": "independent",
                                          "proof_picks": 640, "proof_units": 31.5}).json()
    assert e["pick_weight"] and e["proof_grade"] == "A"
    client.post(f"/api/experts/{e['id']}/picks", json={"ticker": den["ticker"], "side": "yes"})
    nfl = client.get("/api/nfl").json()
    den = next(o for o in nfl["opportunities"] if o["ticker"].endswith("-DEN") and o["side"] == "yes")
    assert den["tier"] == "G" and den["size"]["contracts"] > 0
    assert den["size"]["max_loss"] <= 1000 * 0.025
    # Equivalent outcome on the other market (DAL NO) also counts as agreement.
    dal_no = next(o for o in nfl["opportunities"] if o["ticker"].endswith("-DAL") and o["side"] == "no")
    assert dal_no["expert"]["status"] == "agree"

    # Gas: settlement backfill gives AAA history + calibration marks.
    bf = run(client, "gas_backfill")
    assert bf["actuals"] == 14 and bf["calibration_marks"] == 14 and bf["errors"] == []
    paths = {c[1] for c in fake.calls}
    assert "/trade-api/v2/historical/markets" in paths  # older settlements come from the archive
    assert any(p.startswith("/trade-api/v2/historical/markets/") and p.endswith("/candlesticks") for p in paths)
    gas = client.get("/api/gas").json()
    assert gas["aaa"]["latest"] == pytest.approx(fake.latest)
    lad = gas["ladders"][0]
    assert lad["implied_median"] is not None and lad["model"]["ok"]
    assert gas["calibration"]["market"]["n"] == 14
    # The model median is graded on every day with enough prior history (7 daily changes), not just some.
    assert gas["calibration"]["model"]["n"] == gas["model_score"]["days"] > 0
    # Maker price snaps to the market's 0.1-cent grid: 0.40 bid -> rest at 0.401.
    yes310 = next(o for o in gas["opportunities"] if o["ticker"].endswith("3.100") and o["side"] == "yes")
    assert yes310["maker_price"] == 0.401
    # Too few graded days for the model to have proven itself against Kalshi: nothing above Watch.
    sc = gas["model_score"]
    assert sc["days"] < sc["min_days"] and sc["adds_value"] is False
    assert all(o["tier"] in ("W", "P") for o in gas["opportunities"])
    assert any("Model check" in w for o in gas["opportunities"] for w in o["warnings"])
    # Rising but slowing trend: any bet that only wins on a drop below the latest AAA must be Pass.
    for o in gas["opportunities"]:
        trap = [w for w in o["warnings"] if "TRAP" in w]
        if trap:
            assert o["tier"] == "P"

    edges = client.get("/api/edges?force=true").json()
    assert [g["key"] for g in edges["groups"]] == ["props", "games", "commodities"]
    games = edges["groups"][1]["items"]
    outcomes = [o["outcome"] for o in games]
    assert len(outcomes) == len(set(outcomes))  # DEN YES and DAL NO never both listed
    assert any("Take one, not both" in w for o in games for w in o["warnings"])
    assert "1 open prop markets" in edges["groups"][0]["note"]

    # Paper trade: resting maker bid on DEN YES at 0.56.
    t = client.post("/api/trades", json={"mode": "paper", "ticker": den["ticker"], "side": "yes",
                                         "limit_price": 0.56, "contracts": 10, "tier": "G"}).json()
    assert t["status"] == "resting" and t["fees"] == 0.0432  # 0.0175 x 10 x .56 x .44 = 0.04312 -> 0.0432
    run(client, "settle")
    assert client.get("/api/trades").json()[0]["status"] == "resting"
    fake.nfl[den["ticker"]]["yes_ask_dollars"] = "0.5600"  # the market comes to our bid
    run(client, "kalshi_active")
    run(client, "settle")
    tr = client.get("/api/trades").json()[0]
    assert tr["status"] == "open" and "ask 0.56 reached" in tr["fill_note"]
    port = client.get("/api/portfolio").json()
    assert port["open_exposure"] == pytest.approx(5.6432, abs=0.005)

    # Game ends: DEN wins, market settles.
    past = iso(datetime.now(timezone.utc) - timedelta(minutes=5))
    fake.nfl[den["ticker"]].update(status="finalized", result="yes", settlement_value_dollars="1.0000", close_time=past)
    run(client, "settle")
    tr = client.get("/api/trades").json()[0]
    assert tr["status"] == "settled" and tr["realized_pnl"] == pytest.approx(10 * 0.44 - 0.0432)
    assert client.get("/api/status").json()["bankroll"] == pytest.approx(1000 + 4.36, abs=0.006)

    csv_text = client.get("/api/trades.csv").text
    assert csv_text.splitlines()[0].startswith("id,mode,status,ticker")
    assert len(csv_text.strip().splitlines()) == 2

    # Every price is traceable to a logged pull.
    pulls = client.get("/api/pulls?limit=500").json()
    assert any(p["source"] == "kalshi" and p["ok"] for p in pulls)
    assert any(p["source"] == "aaa" and not p["ok"] for p in pulls)
    assert all("apiKey=" not in (p["endpoint"] or "") or "***" in p["endpoint"] for p in pulls)


def test_settings_validation_and_stale_prices(client, fake):
    run(client, "kalshi_scan")
    assert client.put("/api/settings", json={"kelly_fraction": 2}).status_code == 400
    s = client.put("/api/settings", json={"bankroll": 2500, "per_strike_cap_pct": 3}).json()
    assert s["bankroll"] == 2500 and s["tiers"]["s_min_edge_pts"] == 10.0
    client.post("/api/nfl/lines", json={"home_team": "DEN", "away_team": "DAL", "home_price": -200, "away_price": 170})
    client.put("/api/settings", json={"staleness_minutes": {"kalshi_scan": -1}})
    nfl = client.get("/api/nfl").json()
    assert nfl["opportunities"] and all(o["tier"] == "P" and "stale" in o["tier_reason"] for o in nfl["opportunities"])
