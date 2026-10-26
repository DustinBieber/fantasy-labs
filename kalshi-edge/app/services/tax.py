"""Tax ledger for REAL trades (paper trades are excluded).

Kalshi does not send a 1099 covering event-contract profit or loss, but it is taxable, federally and in
Hawaii. How it is classified (capital gains, Section 1256, or gambling) is unsettled, so this ledger records
everything a CPA needs for any of them: what, when acquired, when closed, proceeds, cost basis with fees,
and gain. It is not tax advice.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select

from .. import db
from ..settings_store import get_settings
from ..timeutil import HST, now_ts

FIELDS = ["trade_id", "source", "description", "ticker", "side", "contracts", "date_acquired", "date_closed",
          "proceeds", "cost_basis", "gain_loss", "holding", "how_closed"]


def ledger(year: Optional[int] = None) -> List[Dict[str, Any]]:
    rows = []
    for t in db.fetch_all(select(db.trades).where(and_(db.trades.c.mode == "real",
                                                       db.trades.c.status.in_(("settled", "closed"))))):
        closed = t["closed_ts"] or t["created_ts"]
        if year and datetime.fromtimestamp(closed, tz=HST).year != year:
            continue
        acquired = t["filled_ts"] or t["created_ts"]
        cost = t["contracts"] * t["limit_price"] + (t["fees"] or 0.0)
        proceeds = t["contracts"] * (t["exit_price"] or 0.0) - (t["exit_fees"] or 0.0)
        rows.append({
            "trade_id": t["id"], "source": t.get("source") or "manual", "description": t["title"] or t["ticker"],
            "ticker": t["ticker"], "side": t["side"], "contracts": t["contracts"],
            "date_acquired": datetime.fromtimestamp(acquired, tz=HST).date().isoformat(),
            "date_closed": datetime.fromtimestamp(closed, tz=HST).date().isoformat(),
            "proceeds": round(proceeds, 4), "cost_basis": round(cost, 4), "gain_loss": round(proceeds - cost, 4),
            "holding": "long" if closed - acquired > 365 * 86400 else "short",
            "how_closed": "settled" if t["status"] == "settled" else "sold",
        })
    return sorted(rows, key=lambda r: (r["date_closed"], r["trade_id"]))


def summary(year: Optional[int] = None) -> Dict[str, Any]:
    year = year or datetime.fromtimestamp(now_ts(), tz=HST).year
    rows = ledger(year)
    gain = sum(r["gain_loss"] for r in rows)
    pct = float(get_settings()["autotrader"].get("tax_set_aside_pct", 30.0))
    return {"year": year, "trades": len(rows), "proceeds": round(sum(r["proceeds"] for r in rows), 2),
            "cost_basis": round(sum(r["cost_basis"] for r in rows), 2), "net_gain": round(gain, 2),
            "set_aside_pct": pct, "set_aside": round(max(0.0, gain) * pct / 100, 2),
            "note": ("Kalshi sends no 1099 for event-contract P&L, but it is taxable (federal and Hawaii). Treatment "
                     "(capital gains, Section 1256, or gambling) is unsettled: give this export to a CPA.")}


def to_csv(year: Optional[int] = None) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    for r in ledger(year):
        w.writerow(r)
    return buf.getvalue()
