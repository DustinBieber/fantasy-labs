"""Unit tests for bettor grading, Polymarket parsing, and parlay math."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.config import DEFAULT_SETTINGS
from app.engine import follow as fl
from app.engine import parlay as pm
from app.engine.experts import proof_grade
from app.services.portfolio import combo_payout
from app.sources import http, polymarket

CFG = DEFAULT_SETTINGS["follow"]


def pos(pnl, slug="nfl-dal-den-2026-10-26", outcome="Broncos", title="Cowboys vs. Broncos", cid=None, days_ago=1,
        avg=0.5, bought=100.0, end_day=None):
    end_day = end_day or datetime(2026, 10, 20, tzinfo=timezone.utc) - timedelta(days=days_ago)
    end = int(end_day.timestamp())
    return {"event_slug": slug, "slug": slug, "outcome": outcome, "title": title, "condition_id": cid or slug + title,
            "realized_pnl": pnl, "avg_price": avg, "total_bought": bought, "end_ts": end}


def test_slug_and_moneyline_detection():
    assert fl.parse_nfl_slug("nfl-dal-den-2026-10-26") == ("dal", "den", date(2026, 10, 26))
    assert fl.parse_nfl_slug("nba-lal-bos-2026-10-26") is None
    assert fl.is_moneyline({"title": "Cowboys vs. Broncos", "outcome": "Broncos"})
    assert not fl.is_moneyline({"title": "Spread: Broncos (-3.5)", "outcome": "Broncos"})
    assert not fl.is_moneyline({"title": "Cowboys vs. Broncos: O/U 45.5", "outcome": "Over"})
    assert fl.is_nfl({"event_slug": "nfl-kc-lv-2026-11-01"}) and not fl.is_nfl({"event_slug": "nba-x"})


def test_grade_proven_bettor():
    positions = [pos(30.0 if i % 5 else -50.0, slug=f"nfl-g{i % 90}-h-2026-10-{1 + i % 28:02d}", cid=f"c{i}",
                     days_ago=i % 120) for i in range(220)]
    positions += [pos(999.0, slug="nba-lal-bos-2026-10-01", cid="nba")]  # other sports never count
    s = fl.grade(positions, CFG)
    assert s["nfl_picks"] == 220 and s["wins"] == 176 and s["losses"] == 44
    assert s["pnl"] == pytest.approx(176 * 30 - 44 * 50)
    assert s["units"] == pytest.approx(s["pnl"] / 50.0, abs=0.1)  # average stake 0.5 x 100
    assert s["eligible"] and not s["market_maker"]
    e = fl.expert_fields(s)
    assert proof_grade(dict(e, sport="NFL"))[1] is True


def test_grade_rejections():
    few = fl.grade([pos(100.0, cid=f"c{i}") for i in range(20)], CFG)
    assert not few["eligible"] and "only 20 resolved NFL picks" in few["why_not"]
    losing = fl.grade([pos(-1.0, cid=f"c{i}") for i in range(250)], CFG)
    assert not losing["eligible"] and "not positive" in losing["why_not"]
    hedged = []
    for i in range(250):
        hedged += [pos(5.0, cid=f"m{i}", outcome="Broncos"), pos(-3.0, cid=f"m{i}", outcome="Cowboys")]
    mm = fl.grade(hedged, CFG)
    assert mm["market_maker"] and not mm["eligible"]
    assert proof_grade(dict(fl.expert_fields(mm), sport="NFL"))[1] is False  # context only


def test_full_season_counts_as_proof():
    # 16 distinct weeks inside one season (Sep 10 2026 through late Dec 2026)
    weekly = [pos(10.0, cid=f"c{i}", end_day=datetime(2026, 9, 10, tzinfo=timezone.utc) + timedelta(days=7 * i))
              for i in range(16)]
    s = fl.grade(weekly, CFG)
    assert s["full_season"] and s["eligible"]


def test_polymarket_normalizes_v1_and_v2():
    v1 = polymarket.normalize_position({"proxyWallet": "0xabc", "eventSlug": "nfl-a-b-2026-10-26", "outcome": "Broncos",
                                        "avgPrice": 0.55, "totalBought": 200, "realizedPnl": 90.5, "curPrice": 1})
    v2 = polymarket.normalize_position({"proxy_wallet": "0xabc", "event_slug": "nfl-a-b-2026-10-26", "outcome": "Broncos",
                                        "avg_price": "0.55", "total_bought": "200", "realized_pnl": "90.5",
                                        "current_price": "1"})
    for k in ("wallet", "event_slug", "avg_price", "total_bought", "realized_pnl", "cur_price"):
        assert v1[k] == v2[k], k


def test_polymarket_falls_back_to_v2(tmpdb):
    def handler(req: httpx.Request):
        if req.url.path == "/v1/leaderboard":
            return httpx.Response(404)
        if req.url.path == "/v2/leaderboard":
            cur = req.url.params.get("cursor")
            if not cur:
                return httpx.Response(200, json={"data": [{"wallet": "0x" + "1" * 40, "pnl": 10}], "next_cursor": "p2"})
            return httpx.Response(200, json={"data": [{"wallet": "0x" + "2" * 40, "pnl": 5}], "next_cursor": None})
        return httpx.Response(404)
    http.set_transport(httpx.MockTransport(handler))
    rows = polymarket.leaderboard("SPORTS", "ALL", 50)
    assert [r["wallet"][:3] for r in rows] == ["0x1", "0x2"]


def leg(t, ev, fair, ask, expert="none"):
    return {"ticker": t, "event_ticker": ev, "fair": fair, "ask": ask, "expert": expert, "label": t}


def test_max_price_for_ev():
    p = pm.max_price_for_ev(0.30, 10.0, 0.07)
    assert pm.net_ev_pct(0.30, p, 0.07) >= 10.0
    assert pm.net_ev_pct(0.30, p + 0.0001, 0.07) < 10.0
    assert pm.max_price_for_ev(0.0001, 10.0, 0.07) is None  # nothing on the grid is cheap enough


def test_parlay_build_rules():
    legs = [leg("A", "G1", 0.60, 0.55), leg("A2", "G1", 0.45, 0.40),  # same game: keep the better one
            leg("B", "G2", 0.70, 0.66), leg("C", "G3", 0.50, 0.52),  # C is -EV: never used
            leg("D", "G4", 0.58, 0.55, expert="disagree")]  # a proven bettor against it: never used
    pool = pm.candidate_legs(legs, 12)
    assert [g["ticker"] for g in pool] == ["A2", "B"] or [g["ticker"] for g in pool] == ["A", "B"]
    assert {g["event_ticker"] for g in pool} == {"G1", "G2"}
    cands = pm.build(legs, {"candidate_legs": 12, "min_legs": 2, "max_legs": 4, "min_net_ev_pct": 10}, 0.07)
    assert len(cands) == 1
    c = cands[0]
    assert c["fair"] == pytest.approx(pool[0]["fair"] * pool[1]["fair"])
    assert c["est_quote"] >= pool[0]["ask"] * pool[1]["ask"]
    assert c["net_ev_pct"] > 0 and (c["max_price"] is None or c["max_price"] < c["fair"])


def test_combo_payout():
    assert combo_payout([1.0, None, None]) is None  # still waiting
    assert combo_payout([1.0, 0.0, None]) == 0.0  # one leg lost: done
    assert combo_payout([1.0, 1.0, 1.0]) == 1.0
    assert combo_payout([1.0, 0.5]) == 0.5  # tie leg
