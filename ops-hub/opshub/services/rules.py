"""Watch rules: turn the current state of orders, freight, stock and invoices into alerts.

Every rule run computes the full set of conditions. Alerts are keyed by
condition (``dedupe_key``): a condition that persists updates its alert, one
that clears auto-resolves it, and one that gets worse reopens and re-notifies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import (ACTIVE_ORDER_STATUSES, OPEN_SHIPMENT_STATUSES, Alert, InventoryLot, Invoice, Order,
                      ProcessedDocument, Product, Shipment, StandingOrder, StockLevel)
from ..timeutil import to_local, utcnow
from . import billing, inventory
from .orders import effective_status
from .shipments import current_leg
from .signals import HIGH_RISK_KEYWORDS

SEVERITY_RANK = {"info": 0, "warn": 1, "urgent": 2}


@dataclass
class Finding:
    key: str
    kind: str
    severity: str
    subject_type: str
    subject_id: int | None
    title: str
    detail: str = ""


def _fmt_when(dt: datetime, now: datetime) -> str:
    hours = (dt - now).total_seconds() / 3600
    if hours < 0:
        return f"{abs(hours) / 24:.1f} days ago" if hours < -48 else f"{abs(hours):.0f}h ago"
    return f"in {hours / 24:.1f} days" if hours > 48 else f"in {hours:.0f}h"


def order_rules(session: Session, s: Settings, now: datetime) -> list[Finding]:
    out = []
    orders = session.scalars(select(Order).where(Order.linked_order_id.is_(None))
                             .options(selectinload(Order.warehouse_records), selectinload(Order.lines)))
    for o in orders:
        status = effective_status(o)
        if status not in ACTIVE_ORDER_STATUSES:
            continue
        label = f"Order {o.number}" + (f" ({o.customer_name})" if o.customer_name else "")
        if o.required_by:
            hours = (o.required_by - now).total_seconds() / 3600
            if hours < 0:
                sev, what = "urgent", "is past its deadline"
            elif hours <= s.deadline_urgent_hours:
                sev, what = "urgent", "is due within a day"
            elif hours <= s.deadline_warn_hours:
                sev, what = "warn", "is due soon"
            else:
                sev = None
            if sev:
                out.append(Finding(f"deadline:order:{o.id}", "order_deadline", sev, "order", o.id,
                                   f"{label} {what}",
                                   f"Needed by {to_local(o.required_by, s.timezone):%a %b %d} "
                                   f"({_fmt_when(o.required_by, now)}), "
                                   f"from {o.required_by_source}: \"{o.required_by_text}\". Status: {status}."))
        if o.note_flags:
            risky = [f for f in o.note_flags if f.lower() in HIGH_RISK_KEYWORDS]
            out.append(Finding(f"notes:order:{o.id}", "order_notes", "warn" if risky else "info", "order", o.id,
                               f"{label} has notes to act on", f"Flags: {', '.join(o.note_flags)}. "
                               f"Notes: {(o.notes or '').strip()[:300]}"))
        if o.is_hazmat:
            items = ", ".join(l.sku for l in o.lines if l.product and l.product.hazmat) or "see notes"
            out.append(Finding(f"hazmat:order:{o.id}", "order_hazmat", "info", "order", o.id,
                               f"{label} contains hazmat", f"Items: {items}. Check DG labels and ground-only service."))
        if o.order_type == "priority" and status == "open" and o.placed_at and \
                now - o.placed_at > timedelta(hours=s.priority_unallocated_hours):
            out.append(Finding(f"priority:order:{o.id}", "order_priority_waiting", "warn", "order", o.id,
                               f"Priority {label} not started",
                               f"Placed {_fmt_when(o.placed_at, now)} and still open."))
        last_change = max([d for d in (o.status_changed_at, o.source_updated_at,
                                       *(w.source_updated_at for w in o.warehouse_records)) if d], default=None)
        if last_change and now - last_change > timedelta(days=s.order_stale_days):
            out.append(Finding(f"stale:order:{o.id}", "order_stale", "warn", "order", o.id,
                               f"{label} has not moved in {(now - last_change).days} days",
                               f"Status {status} since {to_local(last_change, s.timezone):%b %d}."))
    return out


def shipment_rules(session: Session, s: Settings, now: datetime) -> list[Finding]:
    out = []
    for shp in session.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATUSES))):
        leg = current_leg(shp)
        carrier = leg.carrier if leg else None
        who = (leg.carrier_name if leg else "carrier") + (f" / {leg.agent_name}" if leg and leg.agent_name else "")
        contact = f" Contact: {carrier.contact}." if carrier and carrier.contact else ""
        label = f"Shipment {shp.reference}"
        last = shp.last_event_at or shp.created_at
        if shp.status == "exception":
            out.append(Finding(f"exception:shipment:{shp.id}", "shipment_exception", "urgent", "shipment", shp.id,
                               f"{label} has an exception", f"With {who}.{contact}"))
        if shp.eta and shp.eta < now:
            out.append(Finding(f"late:shipment:{shp.id}", "shipment_late",
                               "urgent" if shp.priority or shp.hazmat else "warn", "shipment", shp.id,
                               f"{label} is past its ETA",
                               f"ETA was {to_local(shp.eta, s.timezone):%b %d}; with {who}.{contact}"))
        if last and now - last > timedelta(days=s.shipment_stale_days):
            out.append(Finding(f"stale:shipment:{shp.id}", "shipment_stale", "warn", "shipment", shp.id,
                               f"No update on {label} in {(now - last).days} days",
                               f"Last heard {to_local(last, s.timezone):%b %d} from {who}.{contact}"))
        if shp.hazmat:
            if carrier is None or not carrier.hazmat_approved:
                out.append(Finding(f"hazmat_carrier:shipment:{shp.id}", "shipment_hazmat_carrier", "urgent",
                                   "shipment", shp.id, f"Hazmat {label} is with a carrier not approved for hazmat",
                                   f"Current leg: {who}. Confirm DG certification or rebook."))
            if not shp.un_numbers:
                out.append(Finding(f"hazmat_un:shipment:{shp.id}", "shipment_hazmat_paperwork", "warn", "shipment",
                                   shp.id, f"Hazmat {label} has no UN numbers on file",
                                   "Add UN numbers / class to the booking so DG paperwork can be checked."))
    for doc in session.scalars(select(ProcessedDocument).where(ProcessedDocument.kind == "unmatched")):
        out.append(Finding(f"unmatched_doc:{doc.id}", "tracking_unmatched", "info", "document", doc.id,
                           "Carrier message not matched to a shipment",
                           f"{doc.source}: {doc.summary}. Add the reference to the shipment so updates attach."))
    return out


def inventory_rules(session: Session, s: Settings, now: datetime) -> list[Finding]:
    out = []
    for lot in inventory.lots(session, now, s.sell_through_days, s.lot_policy):
        if lot["days_left"] is None or lot["days_left"] > s.expiry_warn_days:
            continue
        if lot["days_left"] < 0:
            sev, title = "urgent", f"Lot {lot['lot_number']} of {lot['sku']} is past best-by"
        elif lot["at_risk_units"] > 0:
            sev = "urgent" if lot["days_left"] <= 14 else "warn"
            title = f"{lot['at_risk_units']} units of {lot['sku']} may pass best-by unsold"
        else:
            continue
        out.append(Finding(f"expiry:lot:{lot['id']}", "lot_expiry", sev, "lot", lot["id"], title,
                           f"Lot {lot['lot_number']} at {lot['location']}: {lot['qty']} units, best-by {lot['best_by']} "
                           f"({lot['days_left']} days). Selling ~{lot['per_day']}/day from this location."))
    for row in inventory.low_stock(session, s.sell_through_days, now, s.low_stock_days):
        sev = "urgent" if row["days_of_supply"] < s.low_stock_days / 2 else "warn"
        backup = "; ".join(f"{b['location']} has {b['available']}" for b in row["replenish_from"]) or "no backup stock"
        out.append(Finding(f"low_stock:{row['product_id']}:{row['location_id']}", "low_stock", sev, "product",
                           row["product_id"], f"{row['sku']} at {row['location']}: {row['days_of_supply']} days left",
                           f"{row['available']} available, selling {row['per_day']}/day. Replenish: {backup}."))
    out += standing_order_rules(session, s, now)
    for p in session.scalars(select(Product).where(Product.auto_created.is_(True))):
        out.append(Finding(f"unknown_sku:{p.id}", "unknown_sku", "info", "product", p.id,
                           f"SKU {p.sku} is not in the product sheet",
                           "Add it to the Products tab (hazmat, shelf life, seasonal) or map it as an alias."))
    return out


def standing_order_rules(session: Session, s: Settings, now: datetime) -> list[Finding]:
    """Is there enough in-date stock for each standing order due soon, given its shelf-life minimum?"""
    out = []
    horizon = now.date() + timedelta(days=s.standing_order_lookahead_days)
    for so in session.scalars(select(StandingOrder).where(StandingOrder.active.is_(True),
                                                          StandingOrder.next_due <= horizon)):
        short = []
        for line in so.lines or []:
            product = session.scalar(select(Product).where(Product.sku == line["sku"]))
            if product is None:
                short.append(f"{line['sku']}: unknown SKU")
                continue
            loc_code = so.ship_from.code if so.ship_from else None
            lots_q = select(InventoryLot.id).where(InventoryLot.product_id == product.id)
            if so.ship_from_location_id:
                lots_q = lots_q.where(InventoryLot.location_id == so.ship_from_location_id)
            has_lots = session.scalar(lots_q.limit(1))
            if has_lots:
                plan = inventory.allocate(session, line["sku"], line["qty"], location_code=loc_code,
                                          min_days_left=so.min_days_to_best_by, on_date=so.next_due, policy=s.lot_policy)
                if plan["shortfall"]:
                    skipped = sum(x["qty"] for x in plan["skipped"])
                    short.append(f"{line['sku']}: short {plan['shortfall']} of {line['qty']}"
                                 + (f" ({skipped} on hand too close to best-by)" if skipped else ""))
            else:
                q = select(StockLevel).where(StockLevel.product_id == product.id)
                if so.ship_from_location_id:
                    q = q.where(StockLevel.location_id == so.ship_from_location_id)
                available = sum(sl.available for sl in session.scalars(q))
                if available < line["qty"]:
                    short.append(f"{line['sku']}: short {line['qty'] - available} of {line['qty']}")
        if short:
            days = (so.next_due - now.date()).days
            out.append(Finding(f"standing:{so.id}:{so.next_due.isoformat()}", "standing_order_short",
                               "urgent" if days <= 3 else "warn", "standing_order", so.id,
                               f"Standing order {so.code} ({so.customer_name}) due {so.next_due:%b %d} is short",
                               "; ".join(short) + f". Needs {so.min_days_to_best_by}+ days to best-by at ship date."))
    return out


def billing_rules(session: Session, s: Settings, now: datetime) -> list[Finding]:
    out = []
    for inv in session.scalars(select(Invoice).where(Invoice.status.in_(("received", "audited")))):
        result = billing.audit(session, inv, s.billing_tolerance)
        if inv.status == "received":
            inv.status = "audited"
        over = Decimal(result["overbilled"])
        if over > s.billing_tolerance:
            out.append(Finding(f"invoice:{inv.id}", "invoice_variance", "warn", "invoice", inv.id,
                               f"{inv.vendor} invoice {inv.number} may be over by ${over}",
                               f"Billed ${result['billed_total']}, expected ${result['expected_total']}; "
                               f"{result['issues']} issue(s) to review before paying."))
    return out


def run(session: Session, settings: Settings, now: datetime | None = None) -> dict:
    now = now or utcnow()
    findings = (order_rules(session, settings, now) + shipment_rules(session, settings, now)
                + inventory_rules(session, settings, now) + billing_rules(session, settings, now))
    alerts = {a.dedupe_key: a for a in session.scalars(select(Alert))}
    stats = {"open": 0, "new": 0, "escalated": 0, "auto_resolved": 0}
    seen = set()
    for f in findings:
        if f.key in seen:
            continue
        seen.add(f.key)
        a = alerts.get(f.key)
        if a is None:
            a = Alert(dedupe_key=f.key, created_at=now, status="open")
            session.add(a)
            stats["new"] += 1
        elif a.status == "resolved":
            a.status, a.resolved_at, a.notified_severity = "open", None, None
            stats["new"] += 1
        elif SEVERITY_RANK[f.severity] > SEVERITY_RANK.get(a.severity, 0) and a.status in ("acked", "dismissed"):
            a.status = "open"
            stats["escalated"] += 1
        a.kind, a.severity, a.subject_type, a.subject_id = f.kind, f.severity, f.subject_type, f.subject_id
        a.title, a.detail, a.last_seen_at = f.title, f.detail, now
        if a.status == "open":
            stats["open"] += 1
    for key, a in alerts.items():
        if key not in seen and a.status != "resolved":
            a.status, a.resolved_at = "resolved", now
            stats["auto_resolved"] += 1
    session.flush()
    return stats
