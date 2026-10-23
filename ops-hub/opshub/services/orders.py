"""Order linking, effective status and deadline/notes signals."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import ACTIVE_ORDER_STATUSES, Order, OrderLine
from ..timeutil import end_of_day_utc, iso, local_date, utcnow
from . import ai, signals

STATUS_RANK = {"open": 0, "allocated": 1, "processing": 2, "partial": 2, "shipped": 4, "delivered": 5}


def link_orders(session: Session) -> int:
    """Attach warehouse records (e.g. Extensiv) to the channel order they fulfil, by reference number."""
    linked = 0
    for wh in session.scalars(select(Order).where(Order.reference.is_not(None), Order.linked_order_id.is_(None))):
        primary = session.scalar(select(Order).where(Order.number == wh.reference, Order.source != wh.source,
                                                     Order.linked_order_id.is_(None)))
        if primary is not None and primary.id != wh.id:
            wh.linked_order = primary  # via the relationship, so loaded collections see it
            linked += 1
    return linked


def effective_status(order: Order) -> str:
    """The furthest-along status across the order and its warehouse records.
    Cancelled or on-hold on the channel order wins: the warehouse may not know yet."""
    if order.status in ("cancelled", "on_hold"):
        return order.status
    best = order.status
    for wh in order.warehouse_records:
        if wh.status == "cancelled":
            continue
        if STATUS_RANK.get(wh.status, 0) > STATUS_RANK.get(best, 0):
            best = wh.status
    return best


def _notes_hash(order: Order) -> str:
    payload = json.dumps([order.notes or "", order.attributes or {}], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def refresh_signals(session: Session, settings: Settings, now: datetime | None = None, use_ai: bool = True,
                    env: dict[str, str] | None = None) -> dict:
    """Recompute order type, hazmat, note flags and deadline for every order.

    Warehouse records are read first so their deadlines and flags can roll up to the channel order.
    AI reads notes only on active orders whose notes changed since the last read.
    """
    now = now or utcnow()
    stats = {"orders": 0, "ai_reads": 0}
    orders = list(session.scalars(select(Order).options(selectinload(Order.lines).selectinload(OrderLine.product),
                                                        selectinload(Order.warehouse_records))))
    ai_ready = use_ai and bool(ai.configured_providers(settings, env))
    for order in sorted(orders, key=lambda o: o.linked_order_id is None):  # warehouse records first
        stats["orders"] += 1
        ref = local_date(order.placed_at or now, settings.timezone)
        has_seasonal = any(l.product and l.product.seasonal for l in order.lines)
        source_type = order.order_type if order.order_type == "wholesale" else None
        sig = signals.read_order(notes=order.notes, tags=order.tags or [], attributes=order.attributes or {}, ref=ref,
                                 keywords=settings.note_keywords, deadline_keys=settings.deadline_keys,
                                 source_type=source_type, has_seasonal_items=has_seasonal)
        candidates: list[tuple[datetime, str, str]] = []
        if order.system_deadline:
            candidates.append((order.system_deadline, "system", f"date set in {order.source}"))
        if sig.deadline:
            candidates.append((end_of_day_utc(sig.deadline, settings.timezone), sig.deadline_source, sig.deadline_text))
        flags = list(sig.flags)
        hazmat = any(l.product and l.product.hazmat for l in order.lines)
        order_type = sig.order_type

        h = _notes_hash(order)
        if ai_ready and order.notes and order.status in ACTIVE_ORDER_STATUSES and order.notes_hash != h:
            result = ai.read_order_notes(settings, order.notes, ref, env)
            if result is not None:  # on failure, try again next sync
                order.ai_signals, order.notes_hash = result, h
                stats["ai_reads"] += 1
        elif not order.notes:
            order.ai_signals = None
        a = order.ai_signals or {}
        if a.get("deadline"):
            d = signals.parse_date_value(str(a["deadline"]), ref)
            if d:
                candidates.append((end_of_day_utc(d, settings.timezone), "ai", a.get("deadline_quote") or a["deadline"]))
        flags += [f for f in a.get("important") or [] if f not in flags]
        hazmat = hazmat or bool(a.get("hazmat"))
        if a.get("priority") and order_type == "standard":
            order_type = "priority"

        for wh in order.warehouse_records:
            if wh.required_by:
                candidates.append((wh.required_by, "linked", f"{wh.source}: {wh.required_by_text or 'deadline'}"))
            flags += [f for f in wh.note_flags or [] if f not in flags]
            hazmat = hazmat or wh.is_hazmat

        order.order_type = order_type
        order.is_hazmat = hazmat
        order.note_flags = flags
        if candidates:
            order.required_by, order.required_by_source, order.required_by_text = min(candidates, key=lambda c: c[0])
        else:
            order.required_by = order.required_by_source = order.required_by_text = None
    return stats


def to_dict(order: Order, now: datetime) -> dict:
    status = effective_status(order)
    hours_left = None
    if order.required_by:
        hours_left = round((order.required_by - now).total_seconds() / 3600, 1)
    return {
        "id": order.id, "source": order.source, "number": order.number, "channel": order.channel,
        "customer": order.customer_name, "status": status, "channel_status": order.status,
        "raw_status": order.raw_status, "order_type": order.order_type, "placed_at": iso(order.placed_at),
        "required_by": iso(order.required_by), "required_by_source": order.required_by_source,
        "required_by_text": order.required_by_text, "hours_left": hours_left, "notes": order.notes,
        "note_flags": order.note_flags or [], "tags": order.tags or [], "attributes": order.attributes or {},
        "hazmat": order.is_hazmat, "ship_from": order.ship_from.code if order.ship_from else None,
        "ai": order.ai_signals,
        "warehouse": [{"source": w.source, "number": w.number, "status": w.status, "raw_status": w.raw_status,
                       "notes": w.notes, "ship_from": w.ship_from.code if w.ship_from else None}
                      for w in order.warehouse_records],
        "lines": [{"sku": l.sku, "title": l.title, "qty": l.qty, "fulfilled": l.qty_fulfilled,
                   "hazmat": bool(l.product and l.product.hazmat)} for l in order.lines],
    }
