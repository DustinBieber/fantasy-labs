"""Multi-leg freight tracking.

A shipment is one movement of goods. Every time a carrier hands it to another
carrier or agent (port to drayage, LTL interline, forwarder to local courier)
it gets a new leg with its own tracking reference, so updates quoting the
new reference still land on the same shipment.
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.base import NShipment, NTrackingUpdate
from ..models import OPEN_SHIPMENT_STATUSES, Carrier, Order, Shipment, ShipmentEvent, ShipmentLeg
from ..timeutil import end_of_day_utc, utcnow
from .signals import DATE_EXPR, _parse_match

STATUS_RANK = {"booked": 0, "picked_up": 1, "in_transit": 2, "at_terminal": 2, "handed_off": 2, "exception": 2,
               "out_for_delivery": 3, "delivered": 4, "cancelled": 5}
_SUFFIXES = {"co", "inc", "llc", "ltd", "corp", "company", "the", "corporation"}


def _norm(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def match_carrier(session: Session, name: str | None) -> Carrier | None:
    if not name:
        return None
    target = _norm(name)
    for c in session.scalars(select(Carrier)):
        if target in {_norm(c.name), *(_norm(a) for a in c.aliases or [])}:
            return c
    return None


def current_leg(shipment: Shipment) -> ShipmentLeg | None:
    return shipment.legs[-1] if shipment.legs else None


def find_by_ref(session: Session, ref: str | None, open_only: bool = False) -> tuple[Shipment, ShipmentLeg | None] | None:
    if not ref:
        return None
    leg = session.scalar(select(ShipmentLeg).where(ShipmentLeg.tracking_ref == ref).order_by(ShipmentLeg.id.desc()))
    if leg and (not open_only or leg.shipment.status in OPEN_SHIPMENT_STATUSES):
        return leg.shipment, leg
    shp = session.scalar(select(Shipment).where(Shipment.reference == ref))
    if shp and (not open_only or shp.status in OPEN_SHIPMENT_STATUSES):
        return shp, current_leg(shp)
    return None


def upsert_shipment(session: Session, n: NShipment, source: str, authoritative: bool,
                    resolve_location) -> tuple[Shipment, bool]:
    """Create or update from a booking record. Matches by reference, then by tracking ref."""
    shp = session.scalar(select(Shipment).where(Shipment.reference == n.reference))
    if shp is None and n.tracking_ref:
        hit = find_by_ref(session, n.tracking_ref)
        shp = hit[0] if hit else None
    created = shp is None
    if created:
        shp = Shipment(reference=n.reference, direction=n.direction, mode=n.mode, status=n.status, source=source,
                       created_at=n.booked_at or utcnow())
        session.add(shp)
        carrier = match_carrier(session, n.carrier_name)
        leg = ShipmentLeg(seq=1, carrier=carrier, carrier_name=carrier.name if carrier else n.carrier_name,
                          agent_name=n.agent_name, mode=n.mode, tracking_ref=n.tracking_ref, status=n.status,
                          departed_at=n.booked_at, eta=n.eta)
        shp.legs.append(leg)
        shp.events.append(ShipmentEvent(occurred_at=n.booked_at or utcnow(), status="booked", source=source,
                                        description=f"Booked with {leg.carrier_name}"
                                                    + (f" via {n.agent_name}" if n.agent_name else "")
                                                    + (f", ref {n.tracking_ref}" if n.tracking_ref else "")))
        shp.last_event_at = n.booked_at or utcnow()
    if created or authoritative:
        shp.origin = n.origin or shp.origin
        shp.destination = n.destination or shp.destination
        shp.hazmat = n.hazmat or shp.hazmat
        shp.un_numbers = n.un_numbers or shp.un_numbers or []
        shp.priority = n.priority or shp.priority
        shp.notes = n.notes or shp.notes
        if n.eta and (created or not shp.eta):
            shp.eta = n.eta
        if n.destination_location:
            loc = resolve_location(n.destination_location)
            shp.destination_location_id = loc.id if loc else None
    # Booking sources rarely know the latest carrier status; only ever move forward.
    if STATUS_RANK.get(n.status, 0) > STATUS_RANK.get(shp.status, 0):
        shp.status = n.status
        leg = current_leg(shp)
        if leg:
            leg.status = n.status
        if n.status == "delivered":
            shp.delivered_at = shp.delivered_at or utcnow()
    if n.order_number and not shp.order_id:
        order = session.scalar(select(Order).where(Order.number == n.order_number, Order.linked_order_id.is_(None)))
        shp.order_id = order.id if order else None
    return shp, created


def apply_update(session: Session, u: NTrackingUpdate) -> Shipment | None:
    """Record a status update; returns None when no shipment matches the references."""
    hit = find_by_ref(session, u.tracking_ref) or find_by_ref(session, u.shipment_ref)
    if hit is None:
        return None
    shp, leg = hit
    leg = current_leg(shp) if leg is None or leg.status == "handed_off" else leg
    is_handoff = bool(u.new_carrier or u.new_tracking_ref)
    status = "handed_off" if is_handoff else u.status
    for e in shp.events:
        if e.occurred_at == u.occurred_at and e.status == status and (e.description or "") == (u.description or ""):
            return shp  # already recorded
    shp.events.append(ShipmentEvent(leg_id=leg.id if leg else None, occurred_at=u.occurred_at, status=status,
                                    location_text=u.location_text, description=u.description, source=u.source))
    if is_handoff and not any(l.tracking_ref and l.tracking_ref == u.new_tracking_ref for l in shp.legs):
        if leg:
            leg.status = "handed_off"
            leg.arrived_at = u.occurred_at
        carrier = match_carrier(session, u.new_carrier)
        shp.legs.append(ShipmentLeg(
            seq=(leg.seq + 1) if leg else 1, carrier=carrier,
            carrier_name=carrier.name if carrier else (u.new_carrier or (leg.carrier_name if leg else "Unknown")),
            agent_name=u.new_agent, mode=u.new_mode or (leg.mode if leg else shp.mode),
            tracking_ref=u.new_tracking_ref, status="in_transit", departed_at=u.occurred_at, eta=u.eta,
        ))
        shp.status = "in_transit"
    elif not is_handoff:
        if leg:
            leg.status = u.status
            if u.status == "delivered":
                leg.arrived_at = u.occurred_at
            if u.eta:
                leg.eta = u.eta
        if u.status != "booked":
            shp.status = u.status
        if u.status == "delivered":
            shp.delivered_at = u.occurred_at
    if u.eta:
        shp.eta = u.eta
    if not shp.last_event_at or u.occurred_at > shp.last_event_at:
        shp.last_event_at = u.occurred_at
    return shp


# --- reading carrier emails without AI ------------------------------------------------------

HANDOFF_RE = re.compile(
    r"\b(?i:handed\s+off|transferred|interlined|tendered|reassigned|released|turned\s+over)\s+(?i:to)\s+"
    r"(?P<carrier>[A-Z][\w&'.-]*(?:\s+(?:[A-Z][\w&'.-]*|&|of))*)", re.UNICODE)
NEW_REF_RE = re.compile(
    r"\b(?:new\s+(?:pro|ref(?:erence)?|tracking(?:\s+(?:no|number|#))?|bol)|under\s+ref(?:erence)?|"
    r"ref(?:erence)?)\s*(?:#|no\.?|number)?\s*[:#]?\s*(?P<ref>[A-Z0-9][A-Z0-9-]{4,})", re.IGNORECASE)
ETA_RE = re.compile(rf"\b(?:revised\s+eta|new\s+eta|eta|estimated\s+delivery|expected\s+delivery|"
                    rf"delivery\s+date)\s*(?:is|:|of)?\s*(?:{DATE_EXPR})", re.IGNORECASE)
STATUS_WORDS = [
    ("delivered", r"\bdelivered\b(?!\s+(?:by|to\s+the\s+terminal))"),
    ("out_for_delivery", r"\bout\s+for\s+delivery\b"),
    ("exception", r"\b(?:exception|delay(?:ed)?|damaged|refused|held\s+at|on\s+hold|missed|weather)\b"),
    ("at_terminal", r"\b(?:discharged|arrived\s+at|at\s+(?:the\s+)?(?:terminal|port|warehouse|cfs))\b"),
    ("picked_up", r"\bpicked\s+up\b"),
]


def read_email(text: str, known_refs: list[str], received: datetime, tz: str) -> NTrackingUpdate | None:
    """Rule-based reading of a carrier/forwarder update. Needs one of our known refs in the text."""
    ref = next((r for r in sorted(known_refs, key=len, reverse=True) if r and r in text), None)
    if ref is None:
        return None
    u = NTrackingUpdate(occurred_at=received, status="in_transit", tracking_ref=ref, source="email",
                        description=" ".join(text.split())[:400])
    for status, pattern in STATUS_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            u.status = status
            break
    m = HANDOFF_RE.search(text)
    if m:
        u.new_carrier = re.split(r"\s+(?:for|under|at|on|with|in)\s+", m.group("carrier"))[0].rstrip(".,")
        refs = [r.group("ref") for r in NEW_REF_RE.finditer(text) if r.group("ref") != ref]
        u.new_tracking_ref = refs[0] if refs else None
        u.status = "handed_off"
    m = ETA_RE.search(text)
    if m:
        d = _parse_match(m, received.date())
        if d:
            u.eta = end_of_day_utc(d, tz)
    return u


def open_refs(session: Session) -> list[str]:
    refs = []
    for shp in session.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATUSES))):
        refs.append(shp.reference)
        refs += [leg.tracking_ref for leg in shp.legs if leg.tracking_ref]
    return refs


def summarize(shp: Shipment, now: datetime, stale_days: int) -> dict:
    from ..timeutil import iso

    leg = current_leg(shp)
    last = shp.last_event_at or shp.created_at
    is_open = shp.status in OPEN_SHIPMENT_STATUSES
    carrier = leg.carrier if leg else None
    return {
        "id": shp.id, "reference": shp.reference, "direction": shp.direction, "mode": shp.mode,
        "status": shp.status, "priority": shp.priority, "origin": shp.origin, "destination": shp.destination,
        "eta": iso(shp.eta), "hazmat": shp.hazmat, "un_numbers": shp.un_numbers or [],
        "order_number": shp.order.number if shp.order else None,
        "carrier": leg.carrier_name if leg else None, "agent": leg.agent_name if leg else None,
        "tracking_ref": leg.tracking_ref if leg else None,
        "tracking_url": (carrier.tracking_url_template.format(ref=leg.tracking_ref)
                         if carrier and carrier.tracking_url_template and leg.tracking_ref else None),
        "carrier_contact": carrier.contact if carrier else None,
        "carrier_hazmat_approved": carrier.hazmat_approved if carrier else None,
        "legs": len(shp.legs), "last_event_at": iso(last),
        "days_since_update": round((now - last).total_seconds() / 86400, 1) if last else None,
        "stale": bool(is_open and last and (now - last).days >= stale_days),
        "late": bool(is_open and shp.eta and shp.eta < now),
        "notes": shp.notes,
    }


def detail(shp: Shipment, now: datetime, stale_days: int) -> dict:
    from ..timeutil import iso

    d = summarize(shp, now, stale_days)
    d["legs_detail"] = [{"id": l.id, "seq": l.seq, "carrier": l.carrier_name, "agent": l.agent_name, "mode": l.mode,
                         "tracking_ref": l.tracking_ref, "status": l.status, "departed_at": iso(l.departed_at),
                         "arrived_at": iso(l.arrived_at), "eta": iso(l.eta)} for l in shp.legs]
    d["events"] = [{"occurred_at": iso(e.occurred_at), "status": e.status, "location": e.location_text,
                    "description": e.description, "source": e.source, "leg_id": e.leg_id}
                   for e in sorted(shp.events, key=lambda e: e.occurred_at, reverse=True)]
    return d
