"""Inbound, outbound and productivity trackers.

Each tracker reads the unified tables every connector fills (receipts, orders with their
warehouse milestones, sale lines with the employee, time-clock shifts) for a window of days
and returns KPI figures, a daily series on business-local dates, and the detail rows used by
the dashboard, CSV exports, Sheets report tabs and the Muse brief.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import (ACTIVE_ORDER_STATUSES, OPEN_SHIPMENT_STATUSES, LaborShift, Location, Order, Receipt, SaleLine,
                      Shipment)
from ..timeutil import iso, local_date, utcnow
from . import shipments
from .orders import effective_status

AGE_BUCKETS = (("under 24h", 24), ("1-2 days", 48), ("2-5 days", 120), ("over 5 days", None))


def _dates(now: datetime, tz: str, days: int) -> list[date]:
    today = local_date(now, tz)
    return [today - timedelta(days=i) for i in range(days - 1, -1, -1)]


def _hours(a: datetime | None, b: datetime | None) -> float | None:
    return None if a is None or b is None else (b - a).total_seconds() / 3600


def _median(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def _loc_label(loc: Location | None) -> str:
    return loc.code if loc else "unknown"


# --- inbound -----------------------------------------------------------------------------

def inbound(session: Session, s: Settings, now: datetime | None = None, days: int = 30) -> dict:
    """Receipts (3PL receivers, store purchase orders, transfers) and inbound freight."""
    now = now or utcnow()
    tz, start = s.timezone, now - timedelta(days=days)
    receipts = list(session.scalars(select(Receipt).options(selectinload(Receipt.location))))
    open_ = sorted((r for r in receipts if r.status in ("expected", "receiving")),
                   key=lambda r: r.expected_at or datetime.max)
    done = [r for r in receipts if r.status == "received" and r.received_at and r.received_at >= start]

    def row(r: Receipt) -> dict:
        late = r.status == "expected" and r.expected_at is not None and local_date(r.expected_at, tz) < local_date(now, tz)
        return {
            "id": r.id, "source": r.source, "kind": r.kind, "reference": r.reference, "vendor": r.vendor,
            "location": _loc_label(r.location), "status": r.status, "expected_at": iso(r.expected_at),
            "arrived_at": iso(r.arrived_at), "received_at": iso(r.received_at), "units_expected": r.units_expected,
            "units_received": r.units_received, "pallets": r.pallets, "late": late,
            "days_late": (local_date(now, tz) - local_date(r.expected_at, tz)).days if late else 0,
            "short_units": max(r.units_expected - r.units_received, 0) if r.status == "received" else 0,
            "dock_to_stock_hours": _round(_hours(r.arrived_at, r.received_at)),
            "lines": r.lines, "notes": r.notes,
        }

    with_dates = [r for r in done if r.expected_at and r.arrived_at]
    on_time = sum(1 for r in with_dates if local_date(r.arrived_at, tz) <= local_date(r.expected_at, tz))
    expected_units = sum(r.units_expected for r in done)
    received_units = sum(r.units_received for r in done)
    freight = session.scalars(select(Shipment).where(Shipment.direction.in_(("inbound", "transfer")),
                                                     Shipment.status.in_(OPEN_SHIPMENT_STATUSES)))
    freight_rows = sorted((shipments.summarize(x, now, s.shipment_stale_days) for x in freight),
                          key=lambda f: f["eta"] or "9999")

    series = {d: {"date": d.isoformat(), "units": 0, "receipts": 0, "expected_units": 0} for d in _dates(now, tz, days)}
    for r in done:
        d = series.get(local_date(r.received_at, tz))
        if d:
            d["units"] += r.units_received
            d["receipts"] += 1
    for r in open_:
        d = series.get(local_date(r.expected_at, tz)) if r.expected_at else None
        if d:
            d["expected_units"] += r.units_expected
    week = now + timedelta(days=7)
    upcoming = [{"date": (local_date(now, tz) + timedelta(days=i)).isoformat(), "units": 0, "receipts": 0}
                for i in range(8)]
    by_date = {u["date"]: u for u in upcoming}
    for r in open_:
        if r.expected_at:
            key = max(local_date(r.expected_at, tz), local_date(now, tz)).isoformat()  # late ones count as today
            if key in by_date:
                by_date[key]["units"] += r.units_expected
                by_date[key]["receipts"] += 1
    by_location = defaultdict(lambda: {"received_units": 0, "receipts": 0, "open": 0})
    for r in done:
        by_location[_loc_label(r.location)]["received_units"] += r.units_received
        by_location[_loc_label(r.location)]["receipts"] += 1
    for r in open_:
        by_location[_loc_label(r.location)]["open"] += 1
    open_rows = [row(r) for r in open_]
    return {
        "days": days, "generated_at": iso(now),
        "kpis": {
            "open_receipts": len(open_),
            "late_receipts": sum(1 for r in open_rows if r["late"]),
            "on_dock": sum(1 for r in open_ if r.status == "receiving"),
            "units_due_7d": sum(r.units_expected for r in open_ if r.expected_at and r.expected_at <= week),
            "received_receipts": len(done),
            "received_units": received_units,
            "received_pallets": sum(r.pallets or 0 for r in done),
            "on_time_pct": _pct(on_time, len(with_dates)),
            "fill_rate_pct": _pct(received_units, expected_units),
            "short_receipts": sum(1 for r in done if r.units_received < r.units_expected),
            "dock_to_stock_hours": _median(_hours(r.arrived_at, r.received_at) for r in done
                                           if r.arrived_at and r.received_at and r.received_at >= r.arrived_at),
            "freight_open": len(freight_rows),
            "freight_late": sum(1 for f in freight_rows if f["late"]),
        },
        "series": list(series.values()),
        "upcoming": upcoming,
        "by_location": [{"location": k, **v} for k, v in sorted(by_location.items())],
        "open": open_rows,
        "received": [row(r) for r in sorted(done, key=lambda r: r.received_at, reverse=True)],
        "freight": freight_rows,
    }


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


# --- outbound ----------------------------------------------------------------------------

def _outbound_record(o: Order) -> dict:
    """One customer order, with the warehouse copy's milestones folded in."""
    recs = [w for w in o.warehouse_records if w.status != "cancelled"]
    stamps = lambda f: [t for t in [getattr(o, f)] + [getattr(w, f) for w in recs] if t]  # noqa: E731
    origin = recs[0].ship_from if recs and recs[0].ship_from else o.ship_from
    return {
        "order": o, "status": effective_status(o), "origin": _loc_label(origin),
        "placed_at": o.placed_at, "picked_at": min(stamps("picked_at"), default=None),
        "packed_at": min(stamps("packed_at"), default=None), "shipped_at": min(stamps("shipped_at"), default=None),
        "delivered_at": max(stamps("delivered_at"), default=None),
        "handled_by": next((w.handled_by for w in recs if w.handled_by), o.handled_by),
        "units": sum(l.qty for l in o.lines),
    }


def outbound(session: Session, s: Settings, now: datetime | None = None, days: int = 30) -> dict:
    """Orders out the door: volume, speed to ship, backlog age, warehouse stage times, delivery."""
    now = now or utcnow()
    tz, start = s.timezone, now - timedelta(days=days)
    primaries = session.scalars(select(Order).where(Order.linked_order_id.is_(None)).options(
        selectinload(Order.warehouse_records).selectinload(Order.ship_from), selectinload(Order.ship_from),
        selectinload(Order.lines)))
    recs = [_outbound_record(o) for o in primaries if o.status != "cancelled"]
    shipped = [r for r in recs if r["shipped_at"] and r["shipped_at"] >= start]
    placed = [r for r in recs if r["placed_at"] and r["placed_at"] >= start]
    backlog = [r for r in recs if r["status"] in ACTIVE_ORDER_STATUSES]
    to_ship = [_hours(r["placed_at"], r["shipped_at"]) for r in shipped if r["placed_at"]]
    to_ship = [h for h in to_ship if h is not None and h >= 0]
    deadline = [r for r in shipped if r["order"].required_by]
    met = sum(1 for r in deadline if (r["delivered_at"] or r["shipped_at"]) <= r["order"].required_by)
    today = local_date(now, tz)

    series = {d: {"date": d.isoformat(), "placed": 0, "shipped": 0, "delivered": 0} for d in _dates(now, tz, days)}
    origins: dict[str, dict] = {}
    for r in placed:
        if (d := series.get(local_date(r["placed_at"], tz))):
            d["placed"] += 1
    for r in shipped:
        if (d := series.get(local_date(r["shipped_at"], tz))):
            d["shipped"] += 1
            d.setdefault("by_origin", {}).setdefault(r["origin"], 0)
            d["by_origin"][r["origin"]] += 1
        o = origins.setdefault(r["origin"], {"origin": r["origin"], "shipped": 0, "units": 0, "_hours": []})
        o["shipped"] += 1
        o["units"] += r["units"]
        if r["placed_at"]:
            o["_hours"].append(_hours(r["placed_at"], r["shipped_at"]))
    for r in recs:
        if r["delivered_at"] and r["delivered_at"] >= start and (d := series.get(local_date(r["delivered_at"], tz))):
            d["delivered"] += 1
    for o in origins.values():
        hrs = [h for h in o.pop("_hours") if h is not None and h >= 0]
        o["order_to_ship_hours"] = _median(hrs)
        o["within_48h_pct"] = _pct(sum(1 for h in hrs if h <= 48), len(hrs))

    aging = {label: 0 for label, _ in AGE_BUCKETS}
    backlog_rows = []
    for r in sorted(backlog, key=lambda r: r["placed_at"] or now):
        age = _hours(r["placed_at"], now) or 0
        aging[next(label for label, limit in AGE_BUCKETS if limit is None or age < limit)] += 1
        o = r["order"]
        backlog_rows.append({
            "id": o.id, "number": o.number, "customer": o.customer_name, "origin": r["origin"], "status": r["status"],
            "order_type": o.order_type, "placed_at": iso(r["placed_at"]), "age_hours": round(age, 1),
            "picked_at": iso(r["picked_at"]), "packed_at": iso(r["packed_at"]), "required_by": iso(o.required_by),
            "hours_left": _round(_hours(now, o.required_by)), "units": r["units"]})
    stage = [r for r in shipped if r["picked_at"]]
    return {
        "days": days, "generated_at": iso(now),
        "kpis": {
            "shipped_today": sum(1 for r in shipped if local_date(r["shipped_at"], tz) == today),
            "shipped": len(shipped),
            "shipped_units": sum(r["units"] for r in shipped),
            "placed": len(placed),
            "backlog": len(backlog),
            "backlog_on_hold": sum(1 for r in backlog if r["status"] == "on_hold"),
            "backlog_over_48h": sum(1 for r in backlog if r["placed_at"] and _hours(r["placed_at"], now) > 48),
            "order_to_ship_hours": _median(to_ship),
            "order_to_ship_p90_hours": _round(statistics.quantiles(to_ship, n=10)[-1]) if len(to_ship) >= 10 else None,
            "within_24h_pct": _pct(sum(1 for h in to_ship if h <= 24), len(to_ship)),
            "within_48h_pct": _pct(sum(1 for h in to_ship if h <= 48), len(to_ship)),
            "ship_to_deliver_days": _median(_hours(r["shipped_at"], r["delivered_at"]) / 24 for r in shipped
                                            if r["delivered_at"]),
            "deadline_met_pct": _pct(met, len(deadline)),
            "deadline_orders": len(deadline),
        },
        "stages": {
            "order_to_pick_hours": _median(_hours(r["placed_at"], r["picked_at"]) for r in stage if r["placed_at"]),
            "pick_to_pack_hours": _median(_hours(r["picked_at"], r["packed_at"]) for r in stage if r["packed_at"]),
            "pack_to_ship_hours": _median(_hours(r["packed_at"], r["shipped_at"]) for r in stage if r["packed_at"]),
            "orders": len(stage),
        },
        "series": list(series.values()),
        "by_origin": sorted(origins.values(), key=lambda o: -o["shipped"]),
        "aging": [{"bucket": k, "orders": v} for k, v in aging.items()],
        "backlog": backlog_rows,
        "shipped_orders": [{
            "id": r["order"].id, "number": r["order"].number, "customer": r["order"].customer_name,
            "origin": r["origin"], "status": r["status"], "placed_at": iso(r["placed_at"]),
            "picked_at": iso(r["picked_at"]), "packed_at": iso(r["packed_at"]), "shipped_at": iso(r["shipped_at"]),
            "delivered_at": iso(r["delivered_at"]), "handled_by": r["handled_by"], "units": r["units"],
            "order_to_ship_hours": _round(_hours(r["placed_at"], r["shipped_at"]))}
            for r in sorted(shipped, key=lambda r: r["shipped_at"], reverse=True)],
    }


# --- productivity ------------------------------------------------------------------------

def productivity(session: Session, s: Settings, now: datetime | None = None, days: int = 30) -> dict:
    """Warehouse throughput by handler and day; store sales per employee and per labor hour."""
    now = now or utcnow()
    tz, start = s.timezone, now - timedelta(days=days)
    dates = _dates(now, tz, days)
    locs = {l.id: l for l in session.scalars(select(Location))}

    # Warehouse: every warehouse record (linked or not) is the 3PL's work.
    wh = list(session.scalars(select(Order).where(Order.channel == "warehouse").options(selectinload(Order.lines))))
    wh_series = {d: {"date": d.isoformat(), "picked": 0, "packed": 0, "shipped": 0, "units_shipped": 0} for d in dates}
    handlers: dict[str, dict] = {}
    for o in wh:
        for f, key in (("picked_at", "picked"), ("packed_at", "packed"), ("shipped_at", "shipped")):
            t = getattr(o, f)
            if t and t >= start and (d := wh_series.get(local_date(t, tz))):
                d[key] += 1
                if key == "shipped":
                    d["units_shipped"] += sum(l.qty for l in o.lines)
        if o.status == "shipped" and o.shipped_at and o.shipped_at >= start:
            h = handlers.setdefault(o.handled_by or "unassigned", {"name": o.handled_by or "unassigned", "orders": 0,
                                                                     "units": 0, "_days": set(), "_hours": []})
            h["orders"] += 1
            h["units"] += sum(l.qty for l in o.lines)
            h["_days"].add(local_date(o.shipped_at, tz))
            h["_hours"].append(_hours(o.picked_at, o.shipped_at))
    for h in handlers.values():
        worked = h.pop("_days")
        h["days_active"] = len(worked)
        h["orders_per_day"] = round(h["orders"] / len(worked), 1) if worked else None
        h["pick_to_ship_hours"] = _median(h.pop("_hours"))
    wh_days = [d for d in wh_series.values() if d["shipped"]]

    # Stores: sales by employee, hours from the time clock.
    sales = list(session.scalars(select(SaleLine).where(SaleLine.sold_at >= start)))
    shifts = list(session.scalars(select(LaborShift).where(LaborShift.clock_in >= start)))
    people: dict[tuple[str, str], dict] = {}
    stores: dict[str, dict] = {}
    store_series = {d: {"date": d.isoformat(), "revenue": 0.0, "units": 0, "labor_hours": 0.0} for d in dates}

    def store(code: str) -> dict:
        return stores.setdefault(code, {"location": code, "revenue": Decimal(0), "units": 0, "lines": 0,
                                        "labor_hours": 0.0, "_staff": set()})

    def person(name: str, code: str, source: str) -> dict:
        return people.setdefault((name, code), {"name": name, "location": code, "source": source,
                                                "revenue": Decimal(0), "units": 0, "lines": 0, "hours": 0.0,
                                                "shifts": 0})

    for sl in sales:
        loc = locs.get(sl.location_id)
        if loc is None or sl.source == "shopify" or loc.kind not in ("store", "unknown"):
            continue  # online sales have no store staff
        st = store(loc.code)
        st["revenue"] += sl.net_amount or 0
        st["units"] += sl.qty
        st["lines"] += 1
        if (d := store_series.get(local_date(sl.sold_at, tz))):
            d["revenue"] += float(sl.net_amount or 0)
            d["units"] += sl.qty
        if sl.employee:
            p = person(sl.employee, loc.code, sl.source)
            p["revenue"] += sl.net_amount or 0
            p["units"] += sl.qty
            p["lines"] += 1
            st["_staff"].add(sl.employee)
    for sh in shifts:
        loc = locs.get(sh.location_id)
        code = loc.code if loc else "unknown"
        end = min(sh.clock_out or now, now)
        hrs = max(_hours(sh.clock_in, end) or 0, 0)
        if sh.clock_out is None and hrs > 16:  # forgot to clock out: don't count a runaway shift
            hrs = 0
        p = person(sh.employee, code, sh.source)
        p["hours"] += hrs
        p["shifts"] += 1
        st = store(code)
        st["labor_hours"] += hrs
        st["_staff"].add(sh.employee)
        if (d := store_series.get(local_date(sh.clock_in, tz))):
            d["labor_hours"] += hrs
    on_clock = [{"name": sh.employee, "location": _loc_label(locs.get(sh.location_id)), "since": iso(sh.clock_in)}
                for sh in shifts if sh.clock_out is None and 0 <= (_hours(sh.clock_in, now) or 0) <= 16]

    def per_hour(amount, hours):
        return round(float(amount) / hours, 2) if hours else None

    people_rows = []
    for p in people.values():
        p["hours"] = round(p["hours"], 1)
        p["revenue_per_hour"] = per_hour(p["revenue"], p["hours"])
        p["units_per_hour"] = per_hour(p["units"], p["hours"])
        p["revenue"] = str(p["revenue"].quantize(Decimal("0.01")))
        people_rows.append(p)
    store_rows = []
    for st in stores.values():
        st["staff"] = len(st.pop("_staff"))
        st["labor_hours"] = round(st["labor_hours"], 1)
        st["revenue_per_labor_hour"] = per_hour(st["revenue"], st["labor_hours"])
        st["units_per_labor_hour"] = per_hour(st["units"], st["labor_hours"])
        st["revenue"] = str(st["revenue"].quantize(Decimal("0.01")))
        store_rows.append(st)
    for d in store_series.values():
        d["revenue"] = round(d["revenue"], 2)
        d["labor_hours"] = round(d["labor_hours"], 1)
    total_rev = sum(Decimal(st["revenue"]) for st in store_rows)
    total_hours = sum(st["labor_hours"] for st in store_rows)
    clocked_rev = sum(Decimal(st["revenue"]) for st in store_rows if st["labor_hours"])  # stores with a time clock
    shipped_wh = sum(h["orders"] for h in handlers.values())
    return {
        "days": days, "generated_at": iso(now),
        "kpis": {
            "warehouse_orders_shipped": shipped_wh,
            "warehouse_units_shipped": sum(h["units"] for h in handlers.values()),
            "warehouse_orders_per_day": round(shipped_wh / len(wh_days), 1) if wh_days else None,
            "warehouse_pick_to_ship_hours": _median(_hours(o.picked_at, o.shipped_at) for o in wh
                                                    if o.shipped_at and o.shipped_at >= start),
            "store_revenue": str(total_rev.quantize(Decimal("0.01"))),
            "store_labor_hours": round(total_hours, 1),
            "store_revenue_per_labor_hour": per_hour(clocked_rev, total_hours),
            "staff_on_clock": len(on_clock),
        },
        "warehouse_series": list(wh_series.values()),
        "handlers": sorted(handlers.values(), key=lambda h: -h["orders"]),
        "store_series": list(store_series.values()),
        "stores": sorted(store_rows, key=lambda st: -float(st["revenue"])),
        "people": sorted(people_rows, key=lambda p: -float(p["revenue"])),
        "on_clock": on_clock,
        "notes": ("Warehouse handler is the last user to touch the order in Extensiv (usually who closed it). "
                  "Online sales have no store staff, so they are not in the per-employee figures."),
    }


TRACKERS = {"inbound": inbound, "outbound": outbound, "productivity": productivity}
