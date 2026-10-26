"""JSON API for the dashboard. Read-only toward Kalshi; writes only to the local database."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, delete, func, insert, or_, select, update

from . import db
from .engine.experts import proof_grade, rolling_grade
from .services import (autotrader, edges, follow_service, gas_service, jobs, lines_service, nfl_service,
                       parlay_service, portfolio, scanner, tax)
from .services.categorize import CATEGORIES
from .settings_store import get_settings, reset_settings, update_settings
from .sources import odds_api
from .timeutil import hst, now_ts

router = APIRouter(prefix="/api")


def _bad(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
def status() -> Dict[str, Any]:
    p = portfolio.summary()
    total_markets = db.fetch_one(select(func.count().label("n")).select_from(db.markets).where(
        db.markets.c.status.in_(("active", "open")), db.markets.c.close_ts > now_ts()))
    snaps = db.fetch_one(select(func.count().label("n")).select_from(db.market_snapshots))
    return {
        "now_hst": hst(now_ts()), "bankroll": p["bankroll"], "bankroll_start": p["bankroll_start"],
        "open_exposure": p["open_exposure"], "today_realized": p["today_realized"], "unrealized": p["unrealized"],
        "warnings": p["warnings"], "freshness": jobs.freshness(), "open_markets": total_markets["n"],
        "snapshots": snaps["n"], "odds_api_enabled": odds_api.enabled(),
        "autotrader": {"mode": get_settings()["autotrader"]["mode"], "killed": bool(autotrader.state().get("killed"))},
    }


@router.get("/edges")
def get_edges(force: bool = False) -> Dict[str, Any]:
    d = edges.compute(force=force)
    return {k: d[k] for k in ("groups", "counts", "errors", "computed_ts")} | {"computed_hst": hst(d["computed_ts"])}


@router.get("/edges/history")
def edge_history(ticker: str, limit: int = 500):
    rows = db.fetch_all(select(db.edge_history).where(db.edge_history.c.ticker == ticker)
                        .order_by(db.edge_history.c.ts.desc()).limit(limit))
    return list(reversed(rows))


@router.get("/nfl")
def nfl_board():
    return nfl_service.board(include_all_sides=True)


@router.post("/nfl/lines")
def nfl_line(payload: Dict[str, Any] = Body(...)):
    try:
        row = nfl_service.add_manual_line(payload)
    except (ValueError, KeyError) as e:
        _bad(e)
    edges.compute(force=True)
    return row


@router.get("/nfl/lines-board")
def nfl_lines_board():
    b = lines_service.board()
    return {k: b[k] for k in ("best", "games", "counts", "odds_api_enabled", "odds_api_quota")}


@router.post("/nfl/deep/{event_ticker}")
def nfl_deep_pull(event_ticker: str):
    """Alternate spreads/totals + player props for one game (costs Odds API credits)."""
    try:
        r = lines_service.deep_pull(event_ticker)
    except ValueError as e:
        _bad(e)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Odds API pull failed: {e}")
    edges.compute(force=True)
    return r


@router.post("/nfl/manual-line")
def nfl_manual_line(payload: Dict[str, Any] = Body(...)):
    try:
        r = lines_service.add_manual(payload)
    except (ValueError, KeyError, TypeError) as e:
        _bad(e)
    edges.compute(force=True)
    return r


@router.get("/auto/status")
def auto_status():
    return autotrader.status()


@router.post("/auto/mode")
def auto_mode(payload: Dict[str, Any] = Body(...)):
    mode = payload.get("mode")
    if mode not in ("off", "paper", "live"):
        _bad(ValueError("mode must be off, paper, or live"))
    if mode == "live":
        blockers = autotrader.live_blockers()
        if blockers:
            raise HTTPException(status_code=400, detail="Live mode is blocked: " + "; ".join(blockers))
    update_settings({"autotrader": {"mode": mode}})
    autotrader.event("info", "mode", f"Mode set to {mode} by you.", mode=mode, alert=True)
    return autotrader.status()


@router.post("/auto/kill")
def auto_kill(payload: Dict[str, Any] = Body(default={})):
    return autotrader.kill(payload.get("reason") or "stopped by you")


@router.post("/auto/resume")
def auto_resume():
    return autotrader.resume()


@router.post("/auto/live-check")
def auto_live_check():
    if not autotrader.kalshi_auth.configured():
        _bad(ValueError("Add KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH to .env and restart first"))
    return autotrader.live_check()


@router.get("/tax/summary")
def tax_summary(year: Optional[int] = None):
    return tax.summary(year)


@router.get("/tax/ledger.csv", response_class=PlainTextResponse)
def tax_csv(year: Optional[int] = None):
    return PlainTextResponse(tax.to_csv(year), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=kalshi_edge_tax_{year or 'all'}.csv"})


@router.get("/parlays")
def parlays_board():
    return parlay_service.board()


@router.get("/follow")
def follow_board():
    return follow_service.feed()


@router.post("/follow/wallet")
def follow_wallet(payload: Dict[str, Any] = Body(...)):
    """Grade one Polymarket wallet you want to follow (it only gets pick weight if it passes the proof rule)."""
    try:
        stats = follow_service.grade_wallet(str(payload.get("wallet", "")), payload.get("name"))
    except ValueError as e:
        _bad(e)
    except Exception as e:  # noqa: BLE001 - surface source failures to the user
        raise HTTPException(status_code=502, detail=f"Could not read that wallet: {e}")
    edges.compute(force=True)
    return stats


@router.get("/gas")
def gas_board():
    return gas_service.board()


@router.post("/gas/aaa")
def gas_manual(payload: Dict[str, Any] = Body(...)):
    try:
        row = gas_service.add_manual_aaa(payload["date"], float(payload["value"]), payload.get("note", ""))
    except (ValueError, KeyError) as e:
        _bad(e)
    edges.compute(force=True)
    return row


@router.get("/markets")
def list_markets(category: Optional[str] = None, q: Optional[str] = None, limit: int = Query(100, le=1000),
                 offset: int = 0, sort: str = "volume_24h"):
    cond = [db.markets.c.status.in_(("active", "open")), db.markets.c.close_ts > now_ts()]
    if category:
        cond.append(db.markets.c.category == category)
    if q:
        like = f"%{q}%"
        cond.append(or_(db.markets.c.ticker.ilike(like), db.markets.c.title.ilike(like),
                        db.markets.c.event_title.ilike(like), db.markets.c.yes_sub_title.ilike(like)))
    col = {"volume_24h": db.markets.c.volume_24h.desc(), "volume": db.markets.c.volume.desc(),
           "close": db.markets.c.close_ts.asc(), "oi": db.markets.c.open_interest.desc()}.get(sort, db.markets.c.volume_24h.desc())
    rows = db.fetch_all(select(db.markets).where(and_(*cond)).order_by(col).limit(limit).offset(offset))
    for r in rows:
        r["close_hst"] = hst(r["close_ts"])
        r["updated_hst"] = hst(r["updated_ts"])
        r.pop("rules_primary", None)
    return rows


@router.get("/markets/categories")
def market_categories():
    rows = db.fetch_all(select(db.markets.c.category, func.count().label("n")).where(and_(
        db.markets.c.status.in_(("active", "open")), db.markets.c.close_ts > now_ts())).group_by(db.markets.c.category))
    counts = {r["category"]: r["n"] for r in rows}
    return [{"category": c, "count": counts.get(c, 0)} for c in CATEGORIES]


@router.get("/markets/{ticker}")
def market_detail(ticker: str, hours: int = 24):
    m = db.fetch_one(select(db.markets).where(db.markets.c.ticker == ticker))
    if not m:
        raise HTTPException(404, "unknown ticker")
    since = now_ts() - hours * 3600 if hours > 0 else None
    hist = scanner.history(ticker, since)
    m["close_hst"] = hst(m["close_ts"])
    m["updated_hst"] = hst(m["updated_ts"])
    return {"market": m, "series": scanner.series_row(m["series_ticker"]), "history": hist}


@router.get("/trades")
def trades(status: Optional[str] = None, mode: Optional[str] = None):
    rows = portfolio.list_trades(status, mode)
    for r in rows:
        r["created_hst"] = hst(r["created_ts"])
        r["closed_hst"] = hst(r["closed_ts"])
    return rows


@router.post("/trades")
def new_trade(payload: Dict[str, Any] = Body(...)):
    try:
        t = portfolio.create_trade(payload)
    except (ValueError, KeyError) as e:
        _bad(e)
    edges.compute(force=True)
    return t


@router.post("/trades/{tid}/close")
def close_trade(tid: int, payload: Dict[str, Any] = Body(...)):
    try:
        fees = payload.get("exit_fees")
        return portfolio.close_trade(tid, float(payload["exit_price"]), float(fees) if fees not in (None, "") else None)
    except (ValueError, KeyError) as e:
        _bad(e)


@router.post("/trades/{tid}/cancel")
def cancel_trade(tid: int):
    try:
        return portfolio.cancel_trade(tid)
    except ValueError as e:
        _bad(e)


@router.get("/trades.csv", response_class=PlainTextResponse)
def trades_csv():
    return PlainTextResponse(portfolio.to_csv(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=kalshi_edge_trades.csv"})


@router.get("/portfolio")
def get_portfolio():
    return portfolio.summary()


@router.get("/settings")
def settings_get():
    return get_settings()


@router.put("/settings")
def settings_put(payload: Dict[str, Any] = Body(...)):
    if (payload.get("autotrader") or {}).get("mode") == "live":
        blockers = autotrader.live_blockers()
        if blockers:
            raise HTTPException(status_code=400, detail="Live mode is blocked: " + "; ".join(blockers))
    try:
        s = update_settings(payload)
    except (ValueError, KeyError, TypeError) as e:
        _bad(e)
    edges.compute(force=True)
    return s


@router.post("/settings/reset")
def settings_reset():
    return reset_settings()


def _expert_view(e: Dict[str, Any]) -> Dict[str, Any]:
    grade, weight, why = proof_grade(e)
    picks = db.fetch_all(select(db.expert_picks).where(db.expert_picks.c.expert_id == e["id"]))
    return dict(e, proof_grade=grade, pick_weight=weight, proof_note=why, rolling=rolling_grade(picks),
                open_picks=sum(1 for p in picks if not p.get("result")))


_EXPERT_FIELDS = ("name", "sport", "proof_url", "proof_type", "proof_picks", "proof_full_season", "proof_units",
                  "notes", "active")


def _expert_values(p: Dict[str, Any]) -> Dict[str, Any]:
    v = {k: p[k] for k in _EXPERT_FIELDS if k in p}
    if "sport" in v:
        v["sport"] = str(v["sport"]).upper().strip()
    if "proof_picks" in v:
        v["proof_picks"] = int(v["proof_picks"] or 0)
    if "proof_units" in v:
        v["proof_units"] = float(v["proof_units"] or 0)
    for b in ("proof_full_season", "active"):
        if b in v:
            v[b] = bool(v[b])
    return v


@router.get("/experts")
def experts_list():
    return [_expert_view(e) for e in db.fetch_all(select(db.experts).order_by(db.experts.c.sport, db.experts.c.name))]


@router.post("/experts")
def experts_create(payload: Dict[str, Any] = Body(...)):
    v = _expert_values(payload)
    if not v.get("name") or not v.get("sport"):
        _bad(ValueError("name and sport are required"))
    v.setdefault("proof_type", "none")
    v["created_ts"] = now_ts()
    with db.get_engine().begin() as c:
        eid = c.execute(insert(db.experts).values(**v)).inserted_primary_key[0]
    return _expert_view(db.fetch_one(select(db.experts).where(db.experts.c.id == eid)))


@router.put("/experts/{eid}")
def experts_update(eid: int, payload: Dict[str, Any] = Body(...)):
    v = _expert_values(payload)
    with db.get_engine().begin() as c:
        c.execute(update(db.experts).where(db.experts.c.id == eid).values(**v))
    e = db.fetch_one(select(db.experts).where(db.experts.c.id == eid))
    if not e:
        raise HTTPException(404, "unknown expert")
    edges.compute(force=True)
    return _expert_view(e)


@router.delete("/experts/{eid}")
def experts_delete(eid: int):
    with db.get_engine().begin() as c:
        c.execute(delete(db.expert_picks).where(db.expert_picks.c.expert_id == eid))
        c.execute(delete(db.experts).where(db.experts.c.id == eid))
    edges.compute(force=True)
    return {"deleted": eid}


@router.get("/experts/{eid}/picks")
def expert_picks(eid: int):
    return db.fetch_all(select(db.expert_picks).where(db.expert_picks.c.expert_id == eid)
                        .order_by(db.expert_picks.c.picked_ts.desc()))


@router.post("/experts/{eid}/picks")
def expert_pick_add(eid: int, payload: Dict[str, Any] = Body(...)):
    side = str(payload.get("side", "")).lower()
    if side not in ("yes", "no") or not payload.get("ticker"):
        _bad(ValueError("ticker and side (yes/no) are required"))
    row = {"expert_id": eid, "ticker": payload["ticker"].strip(), "side": side, "price": payload.get("price"),
           "picked_ts": now_ts(), "source_url": payload.get("source_url"), "result": payload.get("result"),
           "units": payload.get("units")}
    with db.get_engine().begin() as c:
        pid = c.execute(insert(db.expert_picks).values(**row)).inserted_primary_key[0]
    edges.compute(force=True)
    return dict(row, id=int(pid))


@router.get("/pulls")
def pulls(source: Optional[str] = None, limit: int = Query(100, le=1000), failed: bool = False):
    q = select(db.data_pulls)
    if source:
        q = q.where(db.data_pulls.c.source == source)
    if failed:
        q = q.where(db.data_pulls.c.ok.is_(False))
    rows = db.fetch_all(q.order_by(db.data_pulls.c.id.desc()).limit(limit))
    for r in rows:
        r["started_hst"] = hst(r["started_ts"], "%b %d %I:%M:%S %p HST")
    return rows


@router.get("/pulls/{pid}")
def pull_detail(pid: int):
    r = db.fetch_one(select(db.data_pulls).where(db.data_pulls.c.id == pid))
    if not r:
        raise HTTPException(404, "unknown pull")
    r["started_hst"] = hst(r["started_ts"], "%b %d %I:%M:%S %p HST")
    return r


@router.post("/jobs/{name}/run")
def run_job(name: str):
    if name not in jobs.JOBS:
        raise HTTPException(404, "unknown job")
    return jobs.run_job(name)
