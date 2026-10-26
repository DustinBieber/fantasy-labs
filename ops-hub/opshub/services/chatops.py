"""Chat commands over Telegram and WhatsApp.

Staff (TELEGRAM_STAFF_CHAT_IDS, WHATSAPP_STAFF_NUMBERS) can ask about orders, stock,
shipments and alerts. Freight contacts (TELEGRAM_FREIGHT_CHAT_IDS, WHATSAPP_FREIGHT_NUMBERS)
can only send shipment updates: any message that quotes a known tracking, PRO, BOL or
shipment reference is logged on that shipment, handoffs to a new carrier included.
Messages from anyone else are ignored.
"""
from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..connectors import get_connector
from ..models import Alert, Order, Product, StockLevel
from ..timeutil import to_local, utcnow
from . import kv, notify, orders, shipments

HELP = ("Ops Hub commands:\n"
        "  alerts - open urgent/warn alerts\n"
        "  order #2041 - status, deadline, notes\n"
        "  stock CBC-32 - on hand by location\n"
        "  ship IN-PO-7781 (or a tracking/PRO number) - where it is\n"
        "  lots - stock close to best-by\n"
        "  digest - today's summary\n"
        "Carriers: send the PRO/tracking number with the update, e.g. \"PRO 4471983 delivered\".")


def _ids(value: str | None) -> set[str]:
    return {v.strip().lstrip("+") for v in (value or "").split(",") if v.strip()}


def role_for(channel: str, sender: str, env) -> str | None:
    sender = str(sender).lstrip("+")
    if channel == "telegram":
        staff = _ids(env.get("TELEGRAM_STAFF_CHAT_IDS")) or _ids(env.get("TELEGRAM_CHAT_ID"))
        freight = _ids(env.get("TELEGRAM_FREIGHT_CHAT_IDS"))
    else:
        staff = _ids(env.get("WHATSAPP_STAFF_NUMBERS")) or _ids(env.get("WHATSAPP_TO"))
        freight = _ids(env.get("WHATSAPP_FREIGHT_NUMBERS"))
    if sender in staff:
        return "staff"
    if sender in freight:
        return "freight"
    return None


def handle(session: Session, s: Settings, text: str, role: str, channel: str) -> str | None:
    t = re.sub(r"^/(\w+)@\w+", r"/\1", (text or "").strip())  # "/order@JocorOpsBot 2041" in groups
    low = t.lower().lstrip("/")
    now = utcnow()
    if role == "staff":
        if low in ("help", "start", "?", "menu"):
            return HELP
        if low.startswith("alerts"):
            rows = [a for a in session.scalars(select(Alert).where(Alert.status == "open", Alert.severity != "info"))]
            rows.sort(key=lambda a: (a.severity != "urgent", a.kind))
            if not rows:
                return "No urgent or warning alerts open."
            return "\n".join([f"{len(rows)} open:"] + [f"{'!!' if a.severity == 'urgent' else '!'} {a.title}"
                                                        for a in rows[:12]])
        if low in ("digest", "summary"):
            return notify.digest_text(session)
        m = re.match(r"^(?:order|o)\s+(\S+)$", low) or re.match(r"^(#\d+)$", low)
        if m:
            return _order_reply(session, s, m.group(1), now)
        m = re.match(r"^(?:stock|s|inv)\s+(\S+)$", low)
        if m:
            return _stock_reply(session, m.group(1))
        m = re.match(r"^(?:ship|track|t|where)\s+(\S+)$", low)
        if m:
            hit = shipments.find_by_ref(session, t.split()[-1])
            return _ship_reply(hit[0], s, now) if hit else f"No shipment with reference {t.split()[-1]}."
        if low.startswith("lots"):
            from . import inventory

            near = [l for l in inventory.lots(session, now, s.sell_through_days, s.lot_policy)
                    if l["days_left"] is not None and l["days_left"] <= s.expiry_warn_days]
            if not near:
                return "No lots within the best-by warning window."
            return "\n".join(f"{l['sku']} {l['lot_number']} @ {l['location']}: {l['qty']} units, {l['days_left']} days"
                             + (f", {l['at_risk_units']} at risk" if l["at_risk_units"] else "") for l in near)
    # Staff or freight: a message quoting a known reference is a shipment update.
    u = shipments.read_email(t, shipments.open_refs(session), now, s.timezone)
    if u is not None:
        u.source = channel
        shp = shipments.apply_update(session, u)
        if shp is not None:
            session.flush()
            msg = f"Logged on {shp.reference}: {u.status.replace('_', ' ')}"
            if u.new_carrier:
                msg += f", now with {u.new_carrier}" + (f" ref {u.new_tracking_ref}" if u.new_tracking_ref else "")
            if u.eta:
                msg += f", ETA {to_local(u.eta, s.timezone):%b %d}"
            return msg + ". Thanks!"
    if role == "staff":
        return "Sorry, I didn't catch that.\n" + HELP
    return "Thanks. Please include the PRO, tracking or BOL number so we can match your update."


def _order_reply(session: Session, s: Settings, number: str, now) -> str:
    candidates = {number, f"#{number.lstrip('#')}", number.upper()}
    o = session.scalar(select(Order).where(func.upper(Order.number).in_([c.upper() for c in candidates]),
                                           Order.linked_order_id.is_(None)))
    if o is None:
        return f"No order {number}."
    d = orders.to_dict(o, now)
    lines = [f"Order {o.number} ({o.customer_name or 'no name'}): {d['status'].replace('_', ' ')}"]
    if d["warehouse"]:
        lines.append("Warehouse: " + ", ".join(f"{w['source']} {w['status']}" for w in d["warehouse"]))
    if o.required_by:
        lines.append(f"Needed by {to_local(o.required_by, s.timezone):%a %b %d} ({d['hours_left']:.0f}h)"
                     f" from {o.required_by_source}")
    if o.note_flags:
        lines.append("Flags: " + ", ".join(o.note_flags))
    if o.notes:
        lines.append("Notes: " + o.notes.strip()[:300])
    lines.append("Items: " + ", ".join(f"{l['sku']} x{l['qty']}" for l in d["lines"]))
    return "\n".join(lines)


def _stock_reply(session: Session, sku: str) -> str:
    p = session.scalar(select(Product).where(func.upper(Product.sku) == sku.upper()))
    if p is None:
        return f"No SKU {sku}."
    rows = session.scalars(select(StockLevel).where(StockLevel.product_id == p.id))
    parts = [f"{r.location.code}: {r.on_hand}" + (f" ({r.available} avail)" if r.available != r.on_hand else "")
             for r in sorted(rows, key=lambda r: r.location.code)]
    return f"{p.sku} {p.name}\n" + ("\n".join(parts) or "No stock recorded.")


def _ship_reply(shp, s: Settings, now) -> str:
    d = shipments.summarize(shp, now, s.shipment_stale_days)
    out = [f"{d['reference']} ({d['direction']} {d['mode']}): {d['status'].replace('_', ' ')}",
           f"With {d['carrier']}" + (f" via {d['agent']}" if d["agent"] else "") + (f", ref {d['tracking_ref']}"
                                                                                  if d["tracking_ref"] else "")]
    if d["eta"]:
        out.append(f"ETA {to_local(shp.eta, s.timezone):%a %b %d}" + (" (late)" if d["late"] else ""))
    out.append(f"Last update {d['days_since_update']} days ago" + (" - stale" if d["stale"] else ""))
    if d["hazmat"]:
        out.append("Hazmat " + ", ".join(d["un_numbers"]))
    return "\n".join(out)


# --- Telegram long polling -------------------------------------------------------------------

def poll_telegram(session: Session, s: Settings, env: dict[str, str] | None = None) -> dict:
    """Read new messages to the bot and answer them. Run from the scheduler every few seconds."""
    tg = get_connector("telegram", env)
    if not tg.is_configured():
        return {"skipped": "telegram not configured"}
    offset = int(kv.get("telegram_offset", "0", session=session))
    updates = tg.get_updates(offset)
    replied = 0
    import json

    seen = json.loads(kv.get("telegram_seen_chats", "{}", session=session))  # shown on the Setup page
    for up in updates:
        offset = max(offset, int(up["update_id"]) + 1)
        msg = up.get("message") or up.get("edited_message") or {}
        chat = msg.get("chat") or {}
        chat_id, text = chat.get("id"), msg.get("text") or msg.get("caption")
        if chat_id is not None:
            seen[str(chat_id)] = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        if chat_id is None or not text:
            continue
        role = role_for("telegram", str(chat_id), tg.env)
        if role is None:
            continue
        reply = handle(session, s, text, role, "telegram")
        if reply:
            tg.send(reply, chat_id=str(chat_id))
            replied += 1
    kv.set("telegram_offset", str(offset), session=session)
    kv.set("telegram_seen_chats", json.dumps(seen), session=session)
    return {"updates": len(updates), "replied": replied}


# --- WhatsApp webhook ----------------------------------------------------------------------------

def handle_whatsapp(session: Session, s: Settings, payload: dict, env: dict[str, str] | None = None) -> int:
    wa = get_connector("whatsapp", env)
    replied = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for m in (change.get("value") or {}).get("messages") or []:
                if m.get("type") != "text":
                    continue
                sender = m.get("from", "")
                role = role_for("whatsapp", sender, wa.env)
                if role is None:
                    continue
                reply = handle(session, s, (m.get("text") or {}).get("body", ""), role, "whatsapp")
                if reply and wa.is_configured():
                    wa.send(reply, to=sender)
                    replied += 1
    return replied
