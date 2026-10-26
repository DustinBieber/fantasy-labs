"""AI trader: signing, order mapping, paper cycle, limits, stops, go-live test, live check and live orders
against a mocked Kalshi that verifies every signature, tax ledger, and the dashboard password."""
import base64
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app import config, db
from app.services import autotrader
from app.sources import benchmark, http, kalshi_auth
from tests.test_lines_e2e import FakeLines
from tests.test_integration import iso, run


# ------------------------------------------------------------------ signing and order shape

def test_sign_rsa_and_ed25519_verify():
    msg = "1700000000000GET/trade-api/v2/portfolio/balance"
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sig = base64.b64decode(kalshi_auth.sign(k, msg))
    k.public_key().verify(sig, msg.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                                         salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    e = ed25519.Ed25519PrivateKey.generate()
    e.public_key().verify(base64.b64decode(kalshi_auth.sign(e, msg)), msg.encode())


def test_headers_sign_path_without_query():
    k = ed25519.Ed25519PrivateKey.generate()
    h = kalshi_auth.auth_headers("GET", "https://external-api.kalshi.com/trade-api/v2/portfolio/orders?status=resting",
                                 "key-1", k, now_ms=1700000000000)
    assert h["KALSHI-ACCESS-KEY"] == "key-1" and h["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    k.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
                          b"1700000000000GET/trade-api/v2/portfolio/orders")


def test_order_body_maps_no_to_ask():
    y = kalshi_auth.order_body("T", "yes", 0.46, 32, "c1")
    n = kalshi_auth.order_body("T", "no", 0.30, 5, "c2")
    assert (y["side"], y["price"], y["count"], y["post_only"]) == ("bid", "0.4600", "32.00", True)
    assert (n["side"], n["price"]) == ("ask", "0.7000")
    with pytest.raises(ValueError):
        kalshi_auth.order_body("T", "maybe", 0.5, 1, "c3")


def test_normalize_order_shapes():
    assert kalshi_auth.normalize_order({"order_id": "a", "side": "bid", "price": "0.4600"})["side"] == "yes"
    n = kalshi_auth.normalize_order({"order_id": "b", "side": "ask", "price": "0.7000"})
    assert (n["side"], n["action"], n["price"]) == ("no", "buy", 0.3)
    legacy = kalshi_auth.normalize_order({"order_id": "c", "side": "no", "action": "buy", "no_price_dollars": "0.3000"})
    assert (legacy["side"], legacy["price"]) == ("no", 0.3)


# ------------------------------------------------------------------ fake Kalshi with authenticated endpoints

class FakeTrading(FakeLines):
    def __init__(self, public_key):
        super().__init__()
        self.pub = public_key
        self.resting = {}
        self.fills = []
        self.auth_calls = []
        self.n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == self.HOST and "/portfolio/" in path:
            ts, sig = request.headers["KALSHI-ACCESS-TIMESTAMP"], request.headers["KALSHI-ACCESS-SIGNATURE"]
            self.pub.verify(base64.b64decode(sig), (ts + request.method + path).encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())  # raises if the signature is wrong
            self.auth_calls.append((request.method, path))
            if path.endswith("/portfolio/balance"):
                return httpx.Response(200, json={"balance": 50000, "portfolio_value": 0})
            if path.endswith("/portfolio/events/orders") and request.method == "POST":
                import json as _j
                b = _j.loads(request.content)
                self.n += 1
                oid = f"o{self.n}"
                self.resting[oid] = dict(b, order_id=oid)
                return httpx.Response(201, json={"order": {"order_id": oid, "client_order_id": b["client_order_id"]}})
            if "/portfolio/events/orders/" in path and request.method == "DELETE":
                oid = path.rsplit("/", 1)[-1]
                self.resting.pop(oid, None)
                return httpx.Response(200, json={"order_id": oid, "reduced_by": "1.00"})
            if path.endswith("/portfolio/orders"):
                rows = []
                for o in self.resting.values():
                    if request.url.params.get("ticker") and o["ticker"] != request.url.params["ticker"]:
                        continue
                    px = float(o["price"])
                    legacy = ({"side": "yes", "action": "buy", "yes_price_dollars": f"{px:.4f}"} if o["side"] == "bid"
                              else {"side": "no", "action": "buy", "no_price_dollars": f"{1 - px:.4f}"})
                    rows.append(dict(legacy, order_id=o["order_id"], client_order_id=o["client_order_id"],
                                     ticker=o["ticker"], status="resting"))
                return httpx.Response(200, json={"orders": rows})
            if path.endswith("/portfolio/fills"):
                return httpx.Response(200, json={"fills": self.fills})
            return httpx.Response(404)
        return super().handler(request)

    def fill(self, oid, count):
        o = self.resting.pop(oid)
        px = float(o["price"])
        side, price = ("yes", px) if o["side"] == "bid" else ("no", 1 - px)
        self.fills.append({"order_id": oid, "ticker": o["ticker"], "side": side, "action": "buy", "count": count,
                           f"{side}_price_dollars": f"{price:.4f}", "fee_cost": "0.0500"})


@pytest.fixture
def key(tmp_path):
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    p = tmp_path / "kalshi.pem"
    p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    p.chmod(0o600)
    return k, p


@pytest.fixture
def world(tmpdb, monkeypatch, key):
    monkeypatch.setattr(config, "ODDS_API_KEY", "test-key")
    w = FakeTrading(key[0].public_key())
    http.set_transport(httpx.MockTransport(w.handler))
    kalshi_auth._key_cache.clear()
    return w


@pytest.fixture
def client(world):
    from app.main import app
    with TestClient(app) as c:
        yield c


def _setup(client):
    for j in ("kalshi_scan", "kalshi_series", "kalshi_active", "odds_api"):
        run(client, j)


def _auto(status=None, mode="paper"):
    return autotrader.auto_trades(mode, status)


def test_paper_cycle_limits_and_order_management(client, world):
    _setup(client)
    assert run(client, "autotrader") == {"mode": "off"}
    assert client.post("/api/auto/mode", json={"mode": "paper"}).status_code == 200
    r = run(client, "autotrader")
    assert r["placed"] >= 1
    trades = _auto(("resting",))
    sp = next(t for t in trades if t["ticker"] == world.line_markets["spread"]["ticker"])
    assert sp["source"] == "auto" and sp["tier"] in ("S", "G")
    assert sp["contracts"] * sp["limit_price"] + sp["fees"] <= 15.0 + 1e-9  # max order $15
    assert run(client, "autotrader")["placed"] == 0  # never doubles up on a ticker
    st = client.get("/api/auto/status").json()
    assert st["gate"]["passed"] is False and st["gate"]["started"]

    # The edge disappears: Kalshi reprices the spread above our fair price -> the resting order is cancelled.
    world.line_markets["spread"].update(yes_bid_dollars="0.7000", yes_ask_dollars="0.7200",
                                        no_bid_dollars="0.2800", no_ask_dollars="0.3000")
    run(client, "kalshi_scan")
    run(client, "autotrader")
    sp = next(t for t in _auto() if t["ticker"] == world.line_markets["spread"]["ticker"])
    assert sp["status"] == "cancelled"
    events = [e["action"] for e in client.get("/api/auto/status").json()["events"]]
    assert "placed" in events and "cancelled" in events


def test_stops_kill_and_resume(client, world):
    _setup(client)
    client.post("/api/auto/mode", json={"mode": "paper"})
    run(client, "autotrader")
    resting_before = len(_auto(("resting",)))
    assert resting_before >= 1
    # A $60 realized loss on the AI trader's book breaches the $50 total stop.
    with db.get_engine().begin() as c:
        c.execute(insert(db.trades).values(mode="paper", ticker="X", side="yes", limit_price=0.5, contracts=120, fees=0.0,
                                           status="settled", created_ts=int(time.time()), closed_ts=int(time.time()),
                                           realized_pnl=-60.0, source="auto"))
    r = run(client, "autotrader")
    assert r["killed"] is True and "down $" in r["reason"]
    assert len(_auto(("resting",))) == 0  # kill cancels every resting order
    assert run(client, "autotrader")["killed"]  # stays stopped
    client.post("/api/auto/resume")
    assert client.get("/api/auto/status").json()["killed"] is False


def test_daily_loss_limit(client, world):
    _setup(client)
    client.post("/api/auto/mode", json={"mode": "paper"})
    start_of_day = int(datetime.now(autotrader.HST).replace(hour=0, minute=1).timestamp())
    with db.get_engine().begin() as c:
        c.execute(insert(db.equity_snapshots).values(ts=start_of_day, mode="paper", equity=530.0, realized=0, unrealized=0))
    r = run(client, "autotrader")  # equity ~500 now, 530 at the start of the day: down $30 > $25 limit
    assert r["killed"] is True and "today" in r["reason"]


def _gate_fixture(pnl_each, spy_start, spy_end, n=12, clv=1.0):
    start = int(time.time()) - 15 * 86400
    st = autotrader.state()
    st["paper_started_ts"] = start
    autotrader.save_state(st)
    with db.get_engine().begin() as c:
        for i in range(n):
            c.execute(insert(db.trades).values(mode="paper", ticker=f"T{i}", side="yes", limit_price=0.5, contracts=10,
                                               fees=0.0, status="settled", created_ts=start + 3600, closed_ts=start + 7200,
                                               realized_pnl=pnl_each, clv_pts=clv, source="auto"))
        d0 = datetime.fromtimestamp(start, tz=autotrader.HST).date()
        c.execute(insert(db.spy_prices).values(date=d0.isoformat(), close=spy_start))
        c.execute(insert(db.spy_prices).values(date=(d0 + timedelta(days=14)).isoformat(), close=spy_end))


def test_gate_passes_only_when_every_check_passes(tmpdb):
    _gate_fixture(pnl_each=3.0, spy_start=500.0, spy_end=505.0)  # +7.2% vs S&P +1%
    g = autotrader.gate()
    assert g["passed"], g["checks"]
    assert g["return_pct"] == pytest.approx(7.2) and g["spy_pct"] == pytest.approx(1.0)


def test_gate_fails_when_sp500_did_better(tmpdb):
    _gate_fixture(pnl_each=0.2, spy_start=500.0, spy_end=520.0)  # +0.48% vs S&P +4%
    g = autotrader.gate()
    assert not g["passed"]
    assert any(label.startswith("Beat the S&P") and not ok for label, ok, _ in g["checks"])


def test_gate_fails_on_thin_record(tmpdb):
    _gate_fixture(pnl_each=10.0, spy_start=500.0, spy_end=501.0, n=4)
    assert not autotrader.gate()["passed"]


def test_live_is_locked_until_everything_is_in_place(client, world, key, monkeypatch):
    _setup(client)
    r = client.post("/api/auto/mode", json={"mode": "live"})
    assert r.status_code == 400 and "API key" in r.json()["detail"] and "APP_PASSWORD" in r.json()["detail"]
    assert client.put("/api/settings", json={"autotrader": {"mode": "live"}}).status_code == 400
    monkeypatch.setattr(config, "KALSHI_API_KEY_ID", "key-1")
    monkeypatch.setattr(config, "KALSHI_PRIVATE_KEY_PATH", str(key[1]))
    monkeypatch.setattr(config, "APP_PASSWORD", "hunter2-correct-horse")
    auth = ("me", "hunter2-correct-horse")
    assert client.get("/api/auto/status").status_code == 401  # password now required
    lc = client.post("/api/auto/live-check", auth=auth).json()
    assert lc["ok"], lc["steps"]
    assert world.resting == {}  # both 1c test orders were cancelled
    assert any("buy NO @ 1c as intended" in s for s in lc["steps"])
    r = client.post("/api/auto/mode", json={"mode": "live"}, auth=auth)
    assert r.status_code == 400 and "go-live test" in r.json()["detail"]  # 2 weeks of paper first
    key[1].chmod(0o644)  # a key file other users can read blocks live mode
    assert any("chmod 600" in b for b in autotrader.live_blockers())


def test_live_orders_fill_and_kill(client, world, key, monkeypatch):
    _setup(client)
    monkeypatch.setattr(config, "KALSHI_API_KEY_ID", "key-1")
    monkeypatch.setattr(config, "KALSHI_PRIVATE_KEY_PATH", str(key[1]))
    auth = ("me", "pw-for-tests-only")
    monkeypatch.setattr(config, "APP_PASSWORD", auth[1])
    assert client.post("/api/auto/live-check", auth=auth).json()["ok"]
    monkeypatch.setattr(autotrader, "gate", lambda: {"passed": True, "checks": [], "started": 1})
    assert client.post("/api/auto/mode", json={"mode": "live"}, auth=auth).status_code == 200
    r = client.post("/api/jobs/autotrader/run", auth=auth).json()
    assert r["placed"] >= 1
    live = autotrader.auto_trades("live", ("resting",))
    t = next(x for x in live if x["ticker"] == world.line_markets["spread"]["ticker"])
    assert t["mode"] == "real" and t["external_order_id"] in world.resting
    sent = world.resting[t["external_order_id"]]
    assert sent["post_only"] is True and float(sent["price"]) == pytest.approx(t["limit_price"])
    # Kalshi fills 10 of them: the next cycle syncs the fill (contracts, price, fee from Kalshi).
    world.fill(t["external_order_id"], 10)
    client.post("/api/jobs/autotrader/run", auth=auth)
    t2 = next(x for x in autotrader.auto_trades("live") if x["id"] == t["id"])
    assert t2["status"] == "open" and t2["contracts"] == 10 and t2["fees"] == pytest.approx(0.05)
    # Kill switch cancels everything still resting on Kalshi.
    others = list(world.resting)
    client.post("/api/auto/kill", json={"reason": "test"}, auth=auth)
    assert not any(o in world.resting for o in others)
    assert ("DELETE", f"/trade-api/v2/portfolio/events/orders/{others[0]}") in world.auth_calls if others else True


def test_tax_ledger(tmpdb):
    from app.services import tax
    now = int(time.time())
    with db.get_engine().begin() as c:
        c.execute(insert(db.trades).values(mode="real", ticker="W", title="Win", side="yes", limit_price=0.4, contracts=10,
                                           fees=0.1, status="settled", created_ts=now - 86400, filled_ts=now - 86400,
                                           closed_ts=now, exit_price=1.0, exit_fees=0.0, realized_pnl=5.9, source="auto"))
        c.execute(insert(db.trades).values(mode="real", ticker="L", title="Loss", side="no", limit_price=0.3, contracts=10,
                                           fees=0.1, status="settled", created_ts=now - 86400, closed_ts=now,
                                           exit_price=0.0, exit_fees=0.0, realized_pnl=-3.1))
        c.execute(insert(db.trades).values(mode="paper", ticker="P", side="yes", limit_price=0.5, contracts=10, fees=0,
                                           status="settled", created_ts=now, closed_ts=now, exit_price=1, realized_pnl=5))
    rows = tax.ledger()
    assert [r["ticker"] for r in rows] == ["W", "L"] or [r["ticker"] for r in rows] == ["L", "W"]
    w = next(r for r in rows if r["ticker"] == "W")
    assert (w["proceeds"], w["cost_basis"], w["gain_loss"]) == (10.0, 4.1, 5.9)
    s = tax.summary()
    assert s["net_gain"] == pytest.approx(2.8) and s["set_aside"] == pytest.approx(0.84)
    assert tax.to_csv().splitlines()[0].startswith("trade_id,source,description")


def test_telegram_token_never_logged(tmpdb, monkeypatch):
    from app.sources import notify
    seen = []
    http.set_transport(httpx.MockTransport(lambda r: (seen.append(str(r.url)), httpx.Response(200, json={"ok": True}))[1]))
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:SECRET-token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    assert notify.send("hi")
    assert "SECRET" in seen[0]  # the request itself needs the token
    pulls = db.fetch_all(select(db.data_pulls))
    assert pulls and all("SECRET" not in (p["endpoint"] or "") for p in pulls)


def test_sp500_from_fred_then_yahoo_fallback(tmpdb):
    fred = "observation_date,SP500\n2026-10-20,6650.10\n2026-10-21,.\n2026-10-22,6702.55\n"
    yahoo = {"chart": {"result": [{"timestamp": [1792540800, 1792627200],
                                   "indicators": {"quote": [{"close": [661.2, None]}]}}]}}
    state = {"fred_down": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fred.stlouisfed.org":
            assert request.url.params["id"] == "SP500"
            return httpx.Response(503) if state["fred_down"] else httpx.Response(200, text=fred)
        if request.url.host == "query1.finance.yahoo.com":
            return httpx.Response(200, json=yahoo)
        return httpx.Response(404)

    http.set_transport(httpx.MockTransport(handler))
    r = autotrader.refresh_spy()
    assert r["days"] == 2 and r["latest"] == ("2026-10-22", 6702.55)  # the "." holiday row is skipped
    state["fred_down"] = True
    rows, _ = benchmark.fetch_spy()
    assert rows == [("2026-10-21", 661.2)]  # Yahoo fallback; null closes dropped


def test_trader_sizes_from_its_own_book_and_caps_each_game(client, world):
    _setup(client)
    from app.services import edges, portfolio
    from app.settings_store import update_settings
    # Loose dollar limits, so only quarter Kelly on the trader's own $500 and the 8% per-game cap can bind.
    update_settings({"autotrader": {"mode": "paper", "max_order_usd": 400.0, "max_open_usd": 400.0}})
    board = {(o["ticker"], o["side"]): o for g in edges.compute(force=True)["groups"] for o in g["items"]}
    manual_bank = portfolio.summary()["bankroll"]
    run(client, "autotrader")
    mine = _auto(("resting",))
    assert mine
    for t in mine:
        o = board[(t["ticker"], t["side"])]
        assert t["contracts"] <= o["size"]["contracts"] * 500.0 / manual_bank + 1e-9  # scaled to $500, not $1,000
    per_game = portfolio.exposure_by_key(auto=True, mode="paper")
    assert per_game and all(v <= 0.08 * 500.0 + 1e-6 for v in per_game.values())

    # The trader already holds $32 on the same game (another strike): only ~$8 of its $40 game cap is left.
    for t in mine:
        autotrader._cancel(t, "paper", "test reset")
    portfolio.create_trade({"ticker": world.line_markets["spread_far"]["ticker"], "side": "yes", "contracts": 150,
                            "limit_price": 0.21, "mode": "paper", "source": "auto", "assume_filled": True})
    run(client, "autotrader")
    again = [t for t in _auto(("resting",)) if t["ticker"] == world.line_markets["spread"]["ticker"]]
    assert again and again[0]["contracts"] < 26  # cut by the game cap, not by Kelly
    per_game = portfolio.exposure_by_key(auto=True, mode="paper")
    assert all(v <= 0.08 * 500.0 + 1e-6 for v in per_game.values())
    # Your manual book doesn't see the trader's orders: same bankroll, no exposure, no cap warnings.
    s = portfolio.summary()
    assert s["bankroll"] == manual_bank and s["open_exposure"] == 0 and s["warnings"] == []
    assert portfolio.exposure_by_key() == {}
    assert any(p["source"] == "auto" for p in s["positions"])  # still listed on the Trades tab


def test_terminal_status_and_stop(client, world, monkeypatch):
    from scripts import trader_cli
    _setup(client)

    def call(method, path, body=None):  # the CLI's HTTP call, routed into the test app
        r = client.request(method, "/api" + path, json=body)
        assert r.status_code == 200, r.text
        return r.json()

    monkeypatch.setattr(trader_cli, "call", call)
    assert trader_cli.main(["x", "paper"]) == 0
    assert autotrader.status()["mode"] == "paper"
    run(client, "autotrader")
    text = trader_cli.status_text(call("GET", "/auto/status"))
    assert "AI trader: PAPER" in text and "Go-live test: not yet" in text and "14 days of paper trading" in text
    assert trader_cli.main(["x", "stop"]) == 0
    assert autotrader.status()["killed"] and _auto(("resting",)) == []
    assert trader_cli.main(["x", "paper"]) == 0 and autotrader.status()["mode"] == "paper"  # never flips to live
