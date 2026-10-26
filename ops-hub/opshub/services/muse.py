"""Muse AI handoff: build the daily export, read what Muse sends back."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..connectors import get_connector
from ..connectors._util import rows_to_dicts
from ..connectors.base import NDocument
from ..models import (ACTIVE_ORDER_STATUSES, OPEN_SHIPMENT_STATUSES, Alert, Forecast, HandoffFile, InboxItem, Invoice,
                      Location, Order, Product, Shipment, StandingOrder)
from ..timeutil import end_of_day_utc, iso, local_date, utcnow
from . import ai, billing, files, inventory, orders, shipments, signals, trackers

SEVERITIES = {"urgent", "warn", "info"}


# --- Ops Hub -> Muse -------------------------------------------------------------------

def build_export(session: Session, s: Settings, now: datetime | None = None) -> dict:
    now = now or utcnow()
    primaries = session.scalars(select(Order).where(Order.linked_order_id.is_(None)).options(
        selectinload(Order.lines), selectinload(Order.warehouse_records)))
    watch = []
    for o in primaries:
        d = orders.to_dict(o, now)
        if d["status"] in ACTIVE_ORDER_STATUSES and (o.required_by or o.note_flags or o.is_hazmat
                                                     or o.order_type != "standard"):
            watch.append({k: d[k] for k in ("number", "source", "customer", "status", "order_type", "required_by",
                                            "hours_left", "note_flags", "notes", "hazmat")}
                         | {"lines": [{"sku": l["sku"], "qty": l["qty"]} for l in d["lines"]]})
    watch.sort(key=lambda d: d["required_by"] or "9999")
    alerts = [{"severity": a.severity, "kind": a.kind, "title": a.title, "detail": a.detail}
              for a in session.scalars(select(Alert).where(Alert.status.in_(("open", "acked"))))]
    alerts.sort(key=lambda a: {"urgent": 0, "warn": 1, "info": 2}[a["severity"]])
    matrix = inventory.stock_matrix(session)
    st = inventory.sell_through(session, s.sell_through_days, now)
    lots = [l for l in inventory.lots(session, now, s.sell_through_days, s.lot_policy)
            if l["days_left"] is not None and l["days_left"] <= s.expiry_warn_days]
    freight = [shipments.summarize(x, now, s.shipment_stale_days)
               for x in session.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATUSES)))]
    invoices = []
    for inv in session.scalars(select(Invoice).where(Invoice.status != "approved")):
        a = billing.audit(session, inv, s.billing_tolerance)
        invoices.append({"invoice": inv.number, "vendor": inv.vendor, "status": inv.status,
                         "billed": a["billed_total"], "expected": a["expected_total"], "overbilled": a["overbilled"],
                         "issues": a["issues"]})
    return {
        "kind": "opshub_to_muse", "version": 1, "generated_at": iso(now), "timezone": s.timezone,
        "local_date": local_date(now, s.timezone).isoformat(),
        "alerts": alerts,
        "orders_watch": watch,
        "inventory": {
            "locations": matrix["locations"],
            "stock": [{"sku": r["sku"], "name": r["name"], "on_hand": r["on_hand"], "available": r["available"],
                       "by_location": {c: v["on_hand"] for c, v in r["by_location"].items()}} for r in matrix["rows"]],
            "sell_through_days": s.sell_through_days,
            "sell_through": [{k: r[k] for k in ("sku", "name", "sold", "on_hand", "sell_through_pct", "per_day",
                                                "days_of_supply", "seasonal")} for r in st],
            "lots_near_best_by": lots,
            "low_stock": inventory.low_stock(session, s.sell_through_days, now, s.low_stock_days),
        },
        "freight": [{k: x[k] for k in ("reference", "direction", "mode", "status", "carrier", "agent", "tracking_ref",
                                       "eta", "late", "stale", "hazmat", "un_numbers", "origin", "destination")}
                    for x in freight],
        "standing_orders": [{"code": so.code, "customer": so.customer_name, "next_due": so.next_due.isoformat(),
                             "lines": so.lines, "min_days_to_best_by": so.min_days_to_best_by}
                            for so in session.scalars(select(StandingOrder).where(StandingOrder.active.is_(True)))],
        "billing": invoices,
        "trackers": _trackers(session, s, now),
        "reply": "Drop a muse-to-opshub-YYYY-MM-DD.json file in the Ops Hub inbox folder (format: MUSE_HANDOFF.md).",
    }


def _trackers(session: Session, s: Settings, now: datetime, days: int = 7) -> dict:
    inb = trackers.inbound(session, s, now, days)
    out = trackers.outbound(session, s, now, days)
    prod = trackers.productivity(session, s, now, days)
    keep = ("source", "kind", "reference", "vendor", "location", "status", "expected_at", "units_expected", "late",
            "days_late")
    return {
        "days": days,
        "inbound": {"kpis": inb["kpis"], "open_receipts": [{k: r[k] for k in keep} for r in inb["open"]]},
        "outbound": {"kpis": out["kpis"], "stages": out["stages"], "by_origin": out["by_origin"], "aging": out["aging"]},
        "productivity": {"kpis": prod["kpis"], "warehouse_handlers": prod["handlers"], "stores": prod["stores"],
                         "people": prod["people"]},
    }


def brief_markdown(data: dict) -> str:
    sev = {"urgent": 0, "warn": 0, "info": 0}
    for a in data["alerts"]:
        sev[a["severity"]] += 1
    lines = [f"# Ops Hub brief: {data['local_date']}", "",
             f"{len(data['alerts'])} open alerts ({sev['urgent']} urgent, {sev['warn']} warn, {sev['info']} info), "
             f"{len(data['orders_watch'])} orders on the watchlist, {len(data['freight'])} shipments in transit.", ""]
    if data["alerts"]:
        lines += ["## Alerts", *[f"- **{a['severity'].upper()}** {a['title']}: {a['detail']}" for a in data["alerts"][:40]], ""]
    if data["orders_watch"]:
        lines += ["## Orders to watch", "| Order | Customer | Status | Needed by | Flags |", "|---|---|---|---|---|"]
        lines += [f"| {o['number']} | {o['customer'] or ''} | {o['status']} | {(o['required_by'] or '-')[:10]} | "
                  f"{', '.join(o['note_flags'])} |" for o in data["orders_watch"][:40]]
        lines.append("")
    low = data["inventory"]["low_stock"]
    if low:
        lines += ["## Low stock", *[f"- {r['sku']} at {r['location']}: {r['days_of_supply']} days left "
                                    f"({r['available']} available, {r['per_day']}/day)" for r in low], ""]
    lots = data["inventory"]["lots_near_best_by"]
    if lots:
        lines += ["## Lots near best-by", *[f"- {l['sku']} lot {l['lot_number']} at {l['location']}: {l['qty']} units, "
                                            f"best-by {l['best_by']} ({l['days_left']} days), {l['at_risk_units']} at risk"
                                            for l in lots], ""]
    if data["freight"]:
        lines += ["## Freight in transit", *[f"- {f['reference']} ({f['mode']}): {f['status']} with {f['carrier']}"
                                             + (f" via {f['agent']}" if f["agent"] else "")
                                             + f", ETA {(f['eta'] or '-')[:10]}"
                                             + (" LATE" if f["late"] else "") + (" no recent update" if f["stale"] else "")
                                             for f in data["freight"]], ""]
    if data["billing"]:
        lines += ["## 3PL invoices to review", *[f"- {b['vendor']} {b['invoice']}: billed ${b['billed']}, expected "
                                                 f"${b['expected']}, possible overbilling ${b['overbilled']}"
                                                 for b in data["billing"]], ""]
    t = data.get("trackers")
    if t:
        i, o, p = t["inbound"]["kpis"], t["outbound"]["kpis"], t["productivity"]["kpis"]
        lines += [f"## Inbound, outbound and productivity (last {t['days']} days)",
                  f"- Inbound: {i['open_receipts']} receipts open ({i['late_receipts']} late, {i['on_dock']} on the dock), "
                  f"{i['units_due_7d']} units due in 7 days; received {i['received_units']} units on "
                  f"{i['received_receipts']} receipts, on time {_pct(i['on_time_pct'])}, "
                  f"dock-to-stock {_num(i['dock_to_stock_hours'], 'h')}.",
                  f"- Outbound: shipped {o['shipped']} orders ({o['shipped_today']} today), backlog {o['backlog']} "
                  f"({o['backlog_over_48h']} over 48h), order-to-ship {_num(o['order_to_ship_hours'], 'h')} median, "
                  f"{_pct(o['within_48h_pct'])} within 48h, ship-to-deliver {_num(o['ship_to_deliver_days'], ' days')}.",
                  f"- Warehouse: {p['warehouse_orders_shipped']} orders, {_num(p['warehouse_orders_per_day'])} a day, "
                  f"pick-to-ship {_num(p['warehouse_pick_to_ship_hours'], 'h')}. Stores: ${p['store_revenue']} on "
                  f"{p['store_labor_hours']} labor hours (${_num(p['store_revenue_per_labor_hour'])}/hour).", ""]
        late = [r for r in t["inbound"]["open_receipts"] if r["late"]]
        if late:
            lines += ["Late receipts:", *[f"- {r['reference']} ({r['vendor'] or r['source']}) to {r['location']}: "
                                          f"{r['units_expected']} units, {r['days_late']} days late" for r in late], ""]
    lines += ["---", data["reply"]]
    return "\n".join(lines)


def _num(x, unit: str = "") -> str:
    return "-" if x is None else f"{x}{unit}"


def _pct(x) -> str:
    return "-" if x is None else f"{x}%"


def export_daily(session: Session, s: Settings, env: dict[str, str] | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    data = build_export(session, s, now)
    day = data["local_date"]
    out = [(f"opshub-to-muse-{day}.json", json.dumps(data, indent=2, default=str).encode(), "application/json"),
           (f"opshub-brief-{day}.md", brief_markdown(data).encode(), "text/markdown")]
    where = get_connector("muse", env).write_outbox(out)
    for (name, _, _), loc in zip(out, where):
        session.add(HandoffFile(partner="muse", direction="out", name=name, location=loc,
                                summary=f"{len(data['alerts'])} alerts, {len(data['orders_watch'])} watched orders"))
    session.flush()
    return {"files": [n for n, _, _ in out], "locations": where}


# --- Muse -> Ops Hub ----------------------------------------------------------------------

def _payload_from_file(name: str, content, s: Settings, env) -> dict | None:
    low = name.lower()
    if low.endswith(".json"):
        data = json.loads(files.to_text(name, content))
        return data if isinstance(data, dict) else {"tasks": data}
    if low.endswith((".csv", ".tsv", ".xlsx", ".xlsm")):
        rows = rows_to_dicts(files.read_table(name, content))
        payload: dict = {"alerts": [], "tasks": [], "order_updates": [], "forecasts": []}
        for r in rows:
            kind = (r.get("type") or r.get("kind") or "task").lower().rstrip("s")
            key = {"alert": "alerts", "task": "tasks", "order_update": "order_updates", "order": "order_updates",
                   "forecast": "forecasts"}.get(kind, "tasks")
            payload[key].append(r)
        return payload
    text = files.to_text(name, content)
    if ai.configured_providers(s, env):  # let the AI turn free text into the handoff shape
        prompt = ("Convert this note from our planning assistant into JSON with keys alerts [{severity, title, "
                  "detail, order, sku}], tasks [{title, detail, due, order, sku}], order_updates [{order, deadline, "
                  "note}], forecasts [{sku, location, units_per_day, note}], notes (string). Leave lists empty when "
                  f"nothing applies.\n\nNote:\n\"\"\"\n{text[:12000]}\n\"\"\"")
        result, _ = ai.complete_json(s, prompt, env)
        if result:
            return result
    return {"notes": text}


def _subject(session: Session, item: dict) -> tuple[str | None, int | None]:
    if item.get("order"):
        o = session.scalar(select(Order).where(Order.number == str(item["order"]), Order.linked_order_id.is_(None)))
        if o:
            return "order", o.id
    if item.get("shipment"):
        hit = shipments.find_by_ref(session, str(item["shipment"]))
        if hit:
            return "shipment", hit[0].id
    if item.get("sku"):
        p = session.scalar(select(Product).where(Product.sku == str(item["sku"])))
        if p:
            return "product", p.id
    return None, None


def import_document(session: Session, doc: NDocument, s: Settings, env: dict[str, str] | None = None) -> str:
    now = utcnow()
    today = local_date(now, s.timezone)
    counts = {"alerts": 0, "tasks": 0, "order_updates": 0, "forecasts": 0, "notes": 0}
    for name, content in doc.attachments or [(doc.title, doc.text)]:
        payload = _payload_from_file(name, content, s, env) or {}
        file_key = hashlib.sha1(f"{doc.external_id}:{name}".encode()).hexdigest()[:12]
        if payload.get("alerts"):  # Muse's latest view replaces its earlier alerts
            for old in session.scalars(select(InboxItem).where(InboxItem.source == "muse", InboxItem.kind == "alert",
                                                               InboxItem.status == "open")):
                old.status = "superseded"
        for i, a in enumerate(payload.get("alerts") or []):
            st, sid = _subject(session, a)
            sev = str(a.get("severity") or "info").lower()
            _upsert_item(session, f"{file_key}:a{i}", "alert", sev if sev in SEVERITIES else "info",
                         a.get("title") or "Muse alert", a.get("detail"), None, st, sid, name)
            counts["alerts"] += 1
        for i, t in enumerate(payload.get("tasks") or []):
            st, sid = _subject(session, t)
            due = signals.parse_date_value(str(t.get("due") or ""), today)
            _upsert_item(session, f"{file_key}:t{i}", "task", "info", t.get("title") or "Muse task", t.get("detail"),
                         due, st, sid, name)
            counts["tasks"] += 1
        for u in payload.get("order_updates") or []:
            o = session.scalar(select(Order).where(Order.number == str(u.get("order") or ""),
                                                   Order.linked_order_id.is_(None)))
            if o is None:
                _upsert_item(session, f"{file_key}:o{u.get('order')}", "note", "info",
                             f"Muse update for unknown order {u.get('order')}", u.get("note"), None, None, None, name)
                continue
            d = signals.parse_date_value(str(u.get("deadline") or ""), today)
            if d:
                o.manual_deadline, o.manual_deadline_source = end_of_day_utc(d, s.timezone), "muse"
            if u.get("note"):
                notes = list(o.external_notes or [])
                if not any(n.get("note") == u["note"] for n in notes):
                    notes.append({"source": "muse", "note": u["note"], "at": iso(now)})
                o.external_notes = notes
            counts["order_updates"] += 1
        for f in payload.get("forecasts") or []:
            p = session.scalar(select(Product).where(Product.sku == str(f.get("sku") or "")))
            if p is None or f.get("units_per_day") in (None, ""):
                continue
            loc = session.scalar(select(Location).where(Location.code == str(f["location"]))) if f.get("location") else None
            row = session.scalar(select(Forecast).where(Forecast.source == "muse", Forecast.product_id == p.id,
                                                        Forecast.location_id == (loc.id if loc else None)))
            row = row or Forecast(source="muse", product_id=p.id, location_id=loc.id if loc else None)
            row.units_per_day, row.as_of, row.note = Decimal(str(f["units_per_day"])), today, f.get("note")
            session.add(row)
            counts["forecasts"] += 1
        if payload.get("notes"):
            text = str(payload["notes"])
            _upsert_item(session, f"{file_key}:n", "note", "info", f"Muse note: {text.splitlines()[0][:120]}",
                         text[:4000], None, None, None, name)
            counts["notes"] += 1
        session.add(HandoffFile(partner="muse", direction="in", name=name, location=doc.external_id,
                                summary=", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items() if v)
                                or "nothing to import"))
    session.flush()
    return "muse"


def _upsert_item(session, ext, kind, severity, title, detail, due, subject_type, subject_id, file_name):
    item = session.scalar(select(InboxItem).where(InboxItem.source == "muse", InboxItem.external_id == ext))
    if item is None:
        item = InboxItem(source="muse", external_id=ext, kind=kind)
        session.add(item)
    item.severity, item.title, item.detail, item.due = severity, str(title)[:300], detail, due
    item.subject_type, item.subject_id, item.file_name = subject_type, subject_id, file_name
    if item.status == "superseded":
        item.status = "open"


def inbox_findings(session: Session, s: Settings, now: datetime) -> list:
    """Open Muse items become alerts. Notes and alerts expire after a week."""
    from .rules import Finding

    today = local_date(now, s.timezone)
    out = []
    for item in session.scalars(select(InboxItem).where(InboxItem.status == "open")):
        if item.kind in ("alert", "note") and item.received_at < now - timedelta(days=7):
            item.status = "superseded"
            continue
        sev = item.severity
        detail = item.detail or ""
        if item.kind == "task":
            sev = "warn" if item.due and item.due <= today + timedelta(days=1) else "info"
            if item.due:
                detail = f"Due {item.due:%a %b %d}. " + detail
        out.append(Finding(f"inbox:{item.id}", f"{item.source}_{item.kind}", sev, item.subject_type or "inbox",
                           item.subject_id if item.subject_type else item.id,
                           ("Task: " if item.kind == "task" else "") + item.title, f"From {item.source.title()}. {detail}".strip()))
    return out
