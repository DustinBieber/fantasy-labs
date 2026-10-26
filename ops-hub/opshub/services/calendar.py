"""Calendar entries for order deadlines, freight ETAs and standing-order ship dates."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ACTIVE_ORDER_STATUSES, OPEN_SHIPMENT_STATUSES, Order, Shipment, StandingOrder
from ..timeutil import iso
from .orders import effective_status


def build_events(session: Session, now: datetime, days: int = 30) -> list[dict]:
    """Google Calendar event bodies. ``id`` is stable so republishing updates instead of duplicating."""
    until = now + timedelta(days=days)
    events = []
    for o in session.scalars(select(Order).where(Order.linked_order_id.is_(None), Order.required_by.is_not(None),
                                                 Order.required_by <= until)):
        if effective_status(o) in ACTIVE_ORDER_STATUSES:
            events.append({"id": f"opshuborder{o.id}", "summary": f"Due: order {o.number} ({o.customer_name or ''})",
                           "description": f"{o.required_by_text or ''}\n{o.notes or ''}".strip(),
                           "date": o.required_by.date().isoformat(), "kind": "order_deadline", "at": iso(o.required_by)})
    for s in session.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATUSES),
                                                    Shipment.eta.is_not(None), Shipment.eta <= until)):
        events.append({"id": f"opshubship{s.id}", "summary": f"ETA: {s.reference} ({s.direction}, {s.mode})",
                       "description": f"{s.origin or ''} -> {s.destination or ''}" + (" | HAZMAT" if s.hazmat else ""),
                       "date": s.eta.date().isoformat(), "kind": "shipment_eta", "at": iso(s.eta)})
    for so in session.scalars(select(StandingOrder).where(StandingOrder.active.is_(True),
                                                          StandingOrder.next_due <= until.date())):
        lines = ", ".join(f"{l['sku']} x{l['qty']}" for l in so.lines or [])
        events.append({"id": f"opshubstanding{so.id}{so.next_due:%Y%m%d}",
                       "summary": f"Ship standing order {so.code} ({so.customer_name})", "description": lines,
                       "date": so.next_due.isoformat(), "kind": "standing_order", "at": so.next_due.isoformat()})
    return sorted(events, key=lambda e: e["date"])
