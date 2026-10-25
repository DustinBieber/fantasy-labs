"""End-to-end: Kalshi spread / total / prop ladders priced from The Odds API (bulk + per-game deep pull,
both mocked), rated on Top Edges, and used as parlay legs alongside moneylines. Fixture data only."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.sources import http
from tests.test_follow_parlay_e2e import FakeWorld
from tests.test_integration import mkt, run, tk


class FakeLines(FakeWorld):
    ODDS = "api.the-odds-api.com"

    def __init__(self):
        super().__init__()
        d = tk(self.game_day)
        kick = self.nfl[next(iter(self.nfl))]["close_time"]
        self.line_events = []
        spread_ev, total_ev, prop_ev = f"KXNFLSPREAD-{d}DALDEN", f"KXNFLTOTAL-{d}LVKC", f"KXNFLRECYDS-{d}NYGPHI"
        self.line_markets = {
            "spread": mkt(f"{spread_ev}-DEN3", spread_ev, 45, 48, close=kick, floor_strike=3.5, strike_type="greater",
                          title="Denver wins by over 3.5 points?", yes_sub_title="Denver by 4+"),
            "spread_far": mkt(f"{spread_ev}-DEN10", spread_ev, 20, 23, close=kick, floor_strike=10.5,
                              strike_type="greater", title="Denver wins by over 10.5 points?"),
            "total": mkt(f"{total_ev}-44", total_ev, 40, 43, close=kick, floor_strike=44.5, strike_type="greater",
                         title="Over 44.5 points scored?"),
            "prop": mkt(f"{prop_ev}-PHIABROWN11-60", prop_ev, 42, 45, close=kick, floor_strike=60,
                        strike_type="greater_or_equal", title="A.J. Brown: 60+ receiving yards"),
            "prop_scaled": mkt(f"{prop_ev}-PHIABROWN11-80", prop_ev, 15, 17, close=kick, floor_strike=80,
                               strike_type="greater_or_equal", title="A.J. Brown: 80+ receiving yards"),
        }
        by_ev = {}
        for m in self.line_markets.values():
            by_ev.setdefault(m["event_ticker"], []).append(m)
        for ev, ms in by_ev.items():
            self.line_events.append({"event_ticker": ev, "series_ticker": ev.split("-")[0], "title": ev,
                                     "category": "Sports", "markets": ms})
        self.deep_calls = []

    def events_payload(self):
        p = super().events_payload()
        p["events"] += self.line_events
        return p

    def odds_events(self):
        def ev(eid, home, away, books):
            return {"id": eid, "home_team": home, "away_team": away, "commence_time": self.nfl[next(iter(self.nfl))]["close_time"],
                    "bookmakers": books}
        dk = lambda markets: {"key": "draftkings", "markets": markets}  # noqa: E731
        return [
            ev("e-dal-den", "Denver Broncos", "Dallas Cowboys", [dk([
                {"key": "h2h", "outcomes": [{"name": "Denver Broncos", "price": -200}, {"name": "Dallas Cowboys", "price": 170}]},
                {"key": "spreads", "outcomes": [{"name": "Denver Broncos", "price": -150, "point": -3.5},
                                                {"name": "Dallas Cowboys", "price": 130, "point": 3.5}]}])]),
            ev("e-lv-kc", "Kansas City Chiefs", "Las Vegas Raiders", [dk([
                {"key": "h2h", "outcomes": [{"name": "Kansas City Chiefs", "price": -500}, {"name": "Las Vegas Raiders", "price": 400}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": 44.5},
                                               {"name": "Under", "price": -110, "point": 44.5}]}])]),
            ev("e-nyg-phi", "Philadelphia Eagles", "New York Giants", [dk([
                {"key": "h2h", "outcomes": [{"name": "Philadelphia Eagles", "price": -300}, {"name": "New York Giants", "price": 250}]}])]),
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == self.ODDS:
            path = request.url.path
            if path.endswith("/americanfootball_nfl/odds"):
                return httpx.Response(200, json=self.odds_events(), headers={"x-requests-remaining": "494",
                                                                               "x-requests-last": "3"})
            if "/events/e-nyg-phi/odds" in path:
                self.deep_calls.append(request.url.params.get("markets"))
                body = dict(self.odds_events()[2])
                body["bookmakers"] = [{"key": "draftkings", "markets": [
                    {"key": "player_reception_yds", "outcomes": [
                        {"name": "Over", "description": "A.J. Brown", "price": -120, "point": 59.5},
                        {"name": "Under", "description": "A.J. Brown", "price": -110, "point": 59.5}]},
                    {"key": "player_reception_yds_alternate", "outcomes": [
                        {"name": "Over", "description": "A.J. Brown", "price": 300, "point": 79.5}]}]}]
                return httpx.Response(200, json=body, headers={"x-requests-remaining": "480", "x-requests-last": "4"})
            return httpx.Response(404)
        return super().handler(request)


@pytest.fixture
def world(tmpdb, monkeypatch):
    monkeypatch.setattr(config, "ODDS_API_KEY", "test-key")
    w = FakeLines()
    http.set_transport(httpx.MockTransport(w.handler))
    return w


@pytest.fixture
def client(world):
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_spreads_totals_props_as_parlay_legs(client, world):
    run(client, "kalshi_scan")
    run(client, "kalshi_series")
    r = run(client, "odds_api")
    assert r["book_lines"] == 3 and r["spread_total_outcomes"] == 4

    board = client.get("/api/nfl/lines-board").json()
    assert board["counts"] == {"spread": 2, "total": 1, "prop": 2}
    by_ticker = {o["ticker"]: o for o in board["best"]}
    sp = by_ticker[world.line_markets["spread"]["ticker"]]
    assert sp["fair_kind"] == "exact" and sp["side"] == "yes" and sp["raw_edge_pts"] > 0
    # No book line at 10.5: priced by the labeled model and never rated above Watch, whatever the numbers say.
    far_m = [o for o in board["best"] if o["ticker"] == world.line_markets["spread_far"]["ticker"]]
    assert far_m and far_m[0]["fair_kind"] == "model" and far_m[0]["tier"] in ("W", "P")
    assert "normal model" in far_m[0]["tier_reason"] or far_m[0]["tier"] == "P"
    assert by_ticker[world.line_markets["total"]["ticker"]]["fair_kind"] == "exact"
    assert world.line_markets["prop"]["ticker"] not in by_ticker  # no props pulled yet

    # Per-game deep pull: only the prop stats Kalshi lists for that game are requested.
    prop_ev = world.line_markets["prop"]["event_ticker"]
    deep = client.post(f"/api/nfl/deep/{prop_ev}").json()
    assert deep["credits_left"] == "480"
    assert world.deep_calls == ["alternate_spreads,alternate_totals,player_reception_yds,player_reception_yds_alternate"]
    board = client.get("/api/nfl/lines-board").json()
    by_ticker = {o["ticker"]: o for o in board["best"]}
    p60 = by_ticker[world.line_markets["prop"]["ticker"]]
    p80 = by_ticker[world.line_markets["prop_scaled"]["ticker"]]
    assert p60["fair_kind"] == "exact" and p80["fair_kind"] == "scaled"
    assert any("one-sided" in w for w in p80["warnings"])

    edges = client.get("/api/edges?force=true").json()
    groups = {g["key"]: g for g in edges["groups"]}
    assert groups["props"]["items"] and groups["lines"]["items"]

    # Parlays mix leg types, one leg per game, and never use model-priced legs by default.
    p = client.get("/api/parlays").json()
    assert set(p["legs_by_type"]) >= {"moneyline", "spread", "total", "prop"}
    assert p["tickets"]
    types_seen = set()
    for t in p["tickets"]:
        games = [g["game"] for g in t["legs"]]
        assert len(set(games)) == len(games)
        assert all(g["fair_kind"] != "model" for g in t["legs"])
        types_seen |= {g["leg_type"] for g in t["legs"]}
    assert types_seen - {"moneyline"}, types_seen

    # One game, one correlation bucket: a Broncos moneyline position shrinks room for the Broncos spread.
    from app.services import portfolio
    sp_t = world.line_markets["spread"]
    ml_key = portfolio.corr_key("KXNFLGAME", world.ev_nfl)
    assert ml_key == portfolio.corr_key("KXNFLSPREAD", sp_t["event_ticker"]) and ml_key.startswith("NFL:")
    before = next(o for o in client.get("/api/nfl/lines-board").json()["best"] if o["ticker"] == sp_t["ticker"])
    client.post("/api/trades", json={"mode": "paper", "ticker": f"{world.ev_nfl}-DEN", "side": "yes", "limit_price": 0.56,
                                     "contracts": 125, "assume_filled": True})  # ~$70 of the $80 game cap
    after = next(o for o in client.get("/api/nfl/lines-board").json()["best"] if o["ticker"] == sp_t["ticker"])
    assert before["size"]["contracts"] > 0
    assert after["size"]["contracts"] < before["size"]["contracts"]
    assert after["size"]["binding"] == "correlated cap room"

    # Without an Odds API key you can still enter a line by hand.
    r = client.post("/api/nfl/manual-line", json={"kind": "spread", "event_ticker": world.line_markets["spread"]["event_ticker"],
                                                  "team": "DEN", "point": 10.5, "price_a": 260, "price_b": -330, "book": "screen"})
    assert r.status_code == 200
    board = client.get("/api/nfl/lines-board").json()
    far = next(o for o in board["best"] if o["ticker"] == world.line_markets["spread_far"]["ticker"])
    assert far["fair_kind"] == "exact"
    bad = client.post("/api/nfl/manual-line", json={"kind": "spread", "event_ticker": "nope", "team": "DEN", "point": 3,
                                                    "price_a": -110, "price_b": -110})
    assert bad.status_code == 400
