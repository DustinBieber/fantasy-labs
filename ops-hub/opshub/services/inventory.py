"""Stock by location, sell-through, best-by lots and FIFO/FEFO allocation."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Forecast, InventoryLot, Location, Product, SaleLine, StockLevel


def _locations(session: Session) -> list[Location]:
    order = {"warehouse": 0, "partner_3pl": 1, "store": 2, "online": 3}
    return sorted(session.scalars(select(Location)), key=lambda l: (order.get(l.kind, 9), l.code))


def stock_matrix(session: Session) -> dict:
    locs = _locations(session)
    rows: dict[int, dict] = {}
    for sl in session.scalars(select(StockLevel)):
        p = sl.product
        r = rows.setdefault(p.id, {"sku": p.sku, "name": p.name, "hazmat": p.hazmat, "seasonal": p.seasonal,
                                   "by_location": {}, "on_hand": 0, "available": 0})
        r["by_location"][sl.location.code] = {"on_hand": sl.on_hand, "available": sl.available,
                                              "allocated": sl.allocated}
        r["on_hand"] += sl.on_hand
        r["available"] += sl.available
    used = {code for r in rows.values() for code in r["by_location"]}
    return {"locations": [{"code": l.code, "name": l.name, "kind": l.kind} for l in locs if l.code in used],
            "rows": sorted(rows.values(), key=lambda r: r["sku"])}


def sales_by(session: Session, since: datetime) -> dict[tuple[int, int], int]:
    """Units sold since ``since`` keyed by (product_id, location_id)."""
    q = (select(SaleLine.product_id, SaleLine.location_id, func.sum(SaleLine.qty))
         .where(SaleLine.sold_at >= since).group_by(SaleLine.product_id, SaleLine.location_id))
    return {(p, l): int(n or 0) for p, l, n in session.execute(q)}


def forecasts(session: Session) -> dict[tuple[int, int | None], float]:
    """Latest forecast units/day keyed by (product_id, location_id or None for all locations)."""
    out: dict[tuple[int, int | None], float] = {}
    for f in sorted(session.scalars(select(Forecast)), key=lambda f: f.as_of):
        out[(f.product_id, f.location_id)] = float(f.units_per_day)
    return out


def sell_through(session: Session, days: int, now: datetime) -> list[dict]:
    """Per SKU over the last ``days``: sold / (sold + on hand), daily velocity and days of supply."""
    sold = sales_by(session, now - timedelta(days=days))
    fc = forecasts(session)
    on_hand: dict[tuple[int, int], int] = {(s.product_id, s.location_id): s.on_hand
                                           for s in session.scalars(select(StockLevel))}
    locs = {l.id: l for l in session.scalars(select(Location))}
    rows = []
    for p in session.scalars(select(Product).order_by(Product.sku)):
        keys = {k for k in (*sold, *on_hand) if k[0] == p.id}
        if not keys:
            continue
        per_loc = []
        for _, lid in sorted(keys, key=lambda k: locs[k[1]].code):
            s, oh = sold.get((p.id, lid), 0), on_hand.get((p.id, lid), 0)
            r = _st_row(locs[lid].code, s, oh, days)
            r["forecast_per_day"] = fc.get((p.id, lid))
            per_loc.append(r)
        total_sold = sum(r["sold"] for r in per_loc)
        total_oh = sum(r["on_hand"] for r in per_loc)
        row = _st_row(None, total_sold, total_oh, days)
        loc_fc = [r["forecast_per_day"] for r in per_loc if r["forecast_per_day"] is not None]
        row.update({"sku": p.sku, "name": p.name, "seasonal": p.seasonal, "season": p.season,
                    "hazmat": p.hazmat, "by_location": per_loc,
                    "forecast_per_day": fc.get((p.id, None)) or (round(sum(loc_fc), 2) if loc_fc else None)})
        rows.append(row)
    return rows


def _st_row(code: str | None, sold: int, on_hand: int, days: int) -> dict:
    velocity = sold / days if days else 0
    return {
        "location": code, "sold": sold, "on_hand": on_hand,
        "sell_through_pct": round(100 * sold / (sold + on_hand), 1) if sold + on_hand else None,
        "per_day": round(velocity, 2),
        "days_of_supply": round(on_hand / velocity, 1) if velocity else None,
    }


def sku_velocity(session: Session, days: int, now: datetime) -> dict[int, float]:
    totals: dict[int, int] = defaultdict(int)
    for (pid, _lid), n in sales_by(session, now - timedelta(days=days)).items():
        totals[pid] += n
    return {pid: n / days for pid, n in totals.items()}


def _lot_sort_key(policy: str):
    far = date.max
    if policy == "fifo":
        return lambda l: (l.received_on or far, l.best_by or far, l.id)
    return lambda l: (l.best_by or far, l.received_on or far, l.id)


def lots(session: Session, now: datetime, days: int, policy: str = "fefo") -> list[dict]:
    """All lots in pick order, with how many units are projected to pass best-by unsold.

    Projection: the SKU's sales velocity is shared across locations in proportion
    to their stock, and each location sells its lots in pick order.
    """
    today = now.date()
    velocity = sku_velocity(session, days, now)
    total_oh: dict[int, int] = defaultdict(int)
    loc_oh: dict[tuple[int, int], int] = {}
    for s in session.scalars(select(StockLevel)):
        total_oh[s.product_id] += s.on_hand
        loc_oh[(s.product_id, s.location_id)] = s.on_hand
    groups: dict[tuple[int, int], list[InventoryLot]] = defaultdict(list)
    for lot in session.scalars(select(InventoryLot)):
        groups[(lot.product_id, lot.location_id)].append(lot)
    out = []
    for (pid, lid), group in groups.items():
        share = loc_oh.get((pid, lid), 0) / total_oh[pid] if total_oh.get(pid) else 0
        v = velocity.get(pid, 0) * share
        ahead = 0
        for lot in sorted(group, key=_lot_sort_key(policy)):
            days_left = (lot.best_by - today).days if lot.best_by else None
            at_risk = 0
            if days_left is not None:
                sellable = max(0.0, min(lot.qty_on_hand, v * max(days_left, 0) - ahead))
                at_risk = lot.qty_on_hand - int(sellable)
            out.append({
                "id": lot.id, "sku": lot.product.sku, "name": lot.product.name, "location": lot.location.code,
                "lot_number": lot.lot_number, "received_on": lot.received_on.isoformat() if lot.received_on else None,
                "best_by": lot.best_by.isoformat() if lot.best_by else None, "days_left": days_left,
                "qty": lot.qty_on_hand, "at_risk_units": at_risk, "per_day": round(v, 2),
            })
            ahead += lot.qty_on_hand
    return sorted(out, key=lambda r: (r["sku"], r["location"], r["best_by"] or "9999"))


def allocate(session: Session, sku: str, qty: int, *, location_code: str | None = None, min_days_left: int = 0,
             on_date: date | None = None, policy: str = "fefo") -> dict:
    """Which lots to pick for ``qty`` units, oldest first, skipping lots too close to best-by."""
    product = session.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        return {"sku": sku, "requested": qty, "picks": [], "shortfall": qty, "skipped": [], "error": "unknown SKU"}
    on_date = on_date or date.today()
    q = select(InventoryLot).where(InventoryLot.product_id == product.id, InventoryLot.qty_on_hand > 0)
    if location_code:
        q = q.join(Location).where(Location.code == location_code)
    picks, skipped, need = [], [], qty
    for lot in sorted(session.scalars(q), key=_lot_sort_key(policy)):
        days_left = (lot.best_by - on_date).days if lot.best_by else None
        if days_left is not None and days_left < min_days_left:
            skipped.append({"lot_number": lot.lot_number, "location": lot.location.code, "qty": lot.qty_on_hand,
                            "days_left": days_left})
            continue
        if need <= 0:
            break
        take = min(need, lot.qty_on_hand)
        picks.append({"lot_number": lot.lot_number, "location": lot.location.code, "qty": take,
                      "best_by": lot.best_by.isoformat() if lot.best_by else None, "days_left": days_left})
        need -= take
    return {"sku": sku, "requested": qty, "picks": picks, "shortfall": max(need, 0), "skipped": skipped}


def low_stock(session: Session, days: int, now: datetime, threshold_days: int) -> list[dict]:
    """Selling locations whose stock covers fewer than ``threshold_days`` at the current rate."""
    sold = sales_by(session, now - timedelta(days=days))
    fc = forecasts(session)
    backup: dict[int, list[tuple[str, int]]] = defaultdict(list)  # stock that could replenish
    levels = list(session.scalars(select(StockLevel)))
    for s in levels:
        if s.location.kind in {"warehouse", "partner_3pl"} and s.available > 0:
            backup[s.product_id].append((s.location.code, s.available))
    out = []
    for s in levels:
        units = sold.get((s.product_id, s.location_id), 0)
        forecast = fc.get((s.product_id, s.location_id))
        if not units and not forecast:
            continue
        # Plan on the faster of recent sales and the forecast.
        per_day = max(units / days, forecast or 0)
        cover = s.available / per_day
        if cover < threshold_days:
            out.append({"product_id": s.product_id, "sku": s.product.sku, "name": s.product.name,
                        "location_id": s.location_id, "location": s.location.code, "available": s.available,
                        "per_day": round(per_day, 2), "days_of_supply": round(cover, 1),
                        "forecast_per_day": forecast,
                        "replenish_from": [{"location": c, "available": a} for c, a in backup[s.product_id]
                                           if c != s.location.code]})
    return sorted(out, key=lambda r: r["days_of_supply"])
