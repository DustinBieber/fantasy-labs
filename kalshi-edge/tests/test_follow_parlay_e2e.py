"""End-to-end: grade Polymarket bettors, follow the proven one, build NFL parlays, log and settle one.
Polymarket and Kalshi are mocked; all fixture data exists only in this test."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.sources import http
from tests.test_integration import FakeKalshi, iso, mkt, run, tk

PRO, ROOKIE, MM = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40


def closed(n, pnl_win, pnl_loss, every=5, hedge=False):
    rows = []
    for i in range(n):
        base = {"proxyWallet": PRO, "eventSlug": f"nfl-aa-bb-2026-10-{1 + i % 28:02d}", "title": "Jets vs. Bills",
                "conditionId": f"c{i}", "avgPrice": 0.5, "totalBought": 100,
                "endDate": iso(datetime(2026, 10, 1, tzinfo=timezone.utc) - timedelta(days=i % 100))}
        rows.append(dict(base, outcome="Bills", realizedPnl=pnl_loss if i % every == 0 else pnl_win))
        if hedge:
            rows.append(dict(base, outcome="Jets", realizedPnl=-2.0))
    return rows


class FakeWorld(FakeKalshi):
    PM = "data-api.polymarket.com"

    def __init__(self):
        super().__init__()
        kick = self.nfl[next(iter(self.nfl))]["close_time"]
        self.extra = []
        for away, home, a_name, h_name, (ab, aa), (hb, ha) in [
                ("LV", "KC", "Las Vegas", "Kansas City", (18, 21), (76, 79)),
                ("NYG", "PHI", "New York G", "Philadelphia", (25, 28), (67, 70))]:
            ev = f"KXNFLGAME-{tk(self.game_day)}{away}{home}"
            ms = [mkt(f"{ev}-{away}", ev, ab, aa, close=kick, yes_sub_title=a_name, title=f"{away} at {home} Winner?"),
                  mkt(f"{ev}-{home}", ev, hb, ha, close=kick, yes_sub_title=h_name, title=f"{away} at {home} Winner?")]
            for m in ms:
                self.nfl[m["ticker"]] = m
            self.extra.append({"event_ticker": ev, "series_ticker": "KXNFLGAME", "title": f"{away} at {home}",
                               "category": "Sports", "markets": ms})
        slug = f"nfl-dal-den-{self.game_day.isoformat()}"
        self.closed = {PRO: closed(220, 30.0, -50.0), ROOKIE: closed(20, 40.0, -10.0), MM: closed(250, 5.0, -3.0, hedge=True)}
        self.open = {
            PRO: [{"proxyWallet": PRO, "eventSlug": slug, "slug": slug, "title": "Cowboys vs. Broncos", "outcome": "Broncos",
                   "size": 9000, "avgPrice": 0.55, "curPrice": 0.57, "currentValue": 5130},
                  {"proxyWallet": PRO, "eventSlug": slug, "slug": slug + "-spread", "title": "Spread: Broncos (-3.5)",
                   "outcome": "Broncos", "size": 9000, "avgPrice": 0.5, "currentValue": 4500}],  # not a moneyline
            ROOKIE: [{"proxyWallet": ROOKIE, "eventSlug": slug, "title": "Cowboys vs. Broncos", "outcome": "Cowboys",
                      "size": 20000, "avgPrice": 0.43, "currentValue": 8800}],
            MM: [],
        }

    def events_payload(self):
        p = super().events_payload()
        p["events"][0]["markets"] = [m for m in p["events"][0]["markets"] if m["event_ticker"] == self.ev_nfl]
        p["events"] += self.extra
        return p

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == self.PM:
            q = dict(request.url.params)
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 50))
            if request.url.path == "/v1/leaderboard":
                rows = [{"rank": "1", "proxyWallet": PRO, "userName": "SharpPro", "pnl": 5000},
                        {"rank": "2", "proxyWallet": ROOKIE, "userName": "HotStreak", "pnl": 700},
                        {"rank": "3", "proxyWallet": MM, "userName": "BookMaker", "pnl": 900}]
                return httpx.Response(200, json=rows if off == 0 else [])
            if request.url.path == "/closed-positions":
                return httpx.Response(200, json=self.closed.get(q["user"], [])[off:off + lim])
            if request.url.path == "/positions":
                return httpx.Response(200, json=self.open.get(q["user"], []) if off == 0 else [])
            return httpx.Response(404)
        return super().handler(request)


@pytest.fixture
def world(tmpdb):
    w = FakeWorld()
    http.set_transport(httpx.MockTransport(w.handler))
    return w


@pytest.fixture
def client(world):
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_follow_proven_bettors_and_build_parlays(client, world):
    run(client, "kalshi_scan")
    run(client, "kalshi_series")

    # Grade bettors: only the 220-pick, profitable, non-hedging wallet earns pick weight.
    d = run(client, "follow_discover")
    assert d["candidates"] == 3 and d["graded"] == 3 and d["eligible"] == 1
    f = client.get("/api/follow").json()
    by_name = {w["name"]: w for w in f["wallets"]}
    assert by_name["SharpPro"]["proven"] is True
    assert by_name["HotStreak"]["proven"] is False and "only 20" in by_name["HotStreak"]["stats"]["why_not"]
    assert by_name["BookMaker"]["proven"] is False and by_name["BookMaker"]["stats"]["market_maker"]

    # Follow: only proven wallets' moneyline positions become picks, mapped to the Kalshi ticker.
    r = run(client, "follow_picks")
    assert r == {"wallets": 1, "picks": 1, "mapped_to_kalshi": 1, "errors": []}
    f = client.get("/api/follow").json()
    assert len(f["picks"]) == 1
    pick = f["picks"][0]
    assert pick["ticker"].endswith("-DEN") and pick["size_usd"] == 5130
    assert "no fair price" in pick["verdict"]

    # Fair prices for all three games.
    for home, away, hp, ap in (("DEN", "DAL", -200, 170), ("KC", "LV", -500, 400), ("PHI", "NYG", -300, 250)):
        assert client.post("/api/nfl/lines", json={"home_team": home, "away_team": away, "home_price": hp,
                                                   "away_price": ap, "book": "screen"}).status_code == 200
    nfl = client.get("/api/nfl").json()
    den = next(o for o in nfl["opportunities"] if o["ticker"].endswith("-DEN") and o["side"] == "yes")
    assert den["expert"]["status"] == "agree" and "SharpPro" in den["expert"]["note"]
    assert den["tier"] == "G"  # G-level numbers plus a proven bettor on the same side
    f = client.get("/api/follow").json()
    assert "still below our fair price" in f["picks"][0]["verdict"]

    # Parlays: multi-game only, every leg +EV on its own.
    p = client.get("/api/parlays").json()
    assert p["tickets"], p
    for t in p["tickets"]:
        assert len({g["event_ticker"] for g in t["legs"]}) == t["n_legs"] >= 2
        assert all(g["fair"] > g["ask"] for g in t["legs"])
        assert t["fair"] == pytest.approx(__import__("math").prod(g["fair"] for g in t["legs"]), abs=1e-4)
        assert t["max_price"] is None or t["max_price"] < t["fair"]
        assert any("Accept only at or below" in s or "Do not accept" in s for s in t["setup"])
    best = p["tickets"][0]

    # Log the parlay at the quote we got, then settle: every leg wins.
    legs = [{"ticker": g["ticker"], "side": "yes", "label": g["label"]} for g in best["legs"]]
    tr = client.post("/api/trades", json={"mode": "paper", "combo_legs": legs, "limit_price": best["est_quote"],
                                          "contracts": 10, "tier": best["tier"]}).json()
    assert tr["ticker"].startswith("COMBO-") and tr["status"] == "open" and tr["category"] == "Parlays"
    bad = client.post("/api/trades", json={"mode": "paper", "combo_legs": legs[:1], "limit_price": 0.2, "contracts": 1})
    assert bad.status_code == 400
    past = iso(datetime.now(timezone.utc) - timedelta(minutes=5))
    for g in best["legs"]:
        world.nfl[g["ticker"]].update(status="finalized", result="yes", settlement_value_dollars="1.0000", close_time=past)
    world.nfl[f"{world.ev_nfl}-DEN"].update(status="finalized", result="yes", settlement_value_dollars="1.0000",
                                            close_time=past)
    run(client, "settle")
    tr = next(t for t in client.get("/api/trades").json() if t["ticker"].startswith("COMBO-"))
    assert tr["status"] == "settled" and tr["settlement_result"] == "won"
    assert tr["realized_pnl"] == pytest.approx(10 * (1 - best["est_quote"]) - tr["fees"], abs=1e-4)

    # The followed bettor's pick is graded from the Kalshi result, building our own record of them.
    f = client.get("/api/follow").json()
    rolling = next(w for w in f["wallets"] if w["name"] == "SharpPro")["rolling"]
    assert rolling["graded"] == 1 and rolling["wins"] == 1 and rolling["units"] == pytest.approx((1 - 0.55) / 0.55, abs=0.01)
