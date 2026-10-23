"""HTTP API and dashboard."""
from __future__ import annotations

import base64
import csv
import io
import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import db
from .config import Settings, get_settings
from .connectors import all_connectors, get_connector
from .connectors.base import NotWiredYet, NTrackingUpdate
from .models import (ACTIVE_ORDER_STATUSES, OPEN_SHIPMENT_STATUSES, Alert, Invoice, Notification, Order,
                     RateCardEntry, Shipment, SyncRun)
from .services import billing, calendar, inventory, notify, orders, shipments, sync
from .timeutil import end_of_day_utc, iso, parse_date, parse_iso, utcnow

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def settings_dep() -> Settings:
    return get_settings()


class ShipmentUpdateIn(BaseModel):
    status: str = "in_transit"
    location: str | None = None
    description: str | None = None
    eta: str | None = None  # YYYY-MM-DD
    new_carrier: str | None = None
    new_agent: str | None = None
    new_tracking_ref: str | None = None
    occurred_at: str | None = None
    source: str = "manual"


class InvoiceStatusIn(BaseModel):
    status: str


# --- app -------------------------------------------------------------------------

def _scheduler(stop: threading.Event, minutes: int) -> None:
    while not stop.wait(minutes * 60):
        try:
            with db.session_scope() as session:
                sync.sync_all(session, get_settings())
        except Exception:
            log.exception("Scheduled sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.create_all()
    stop = threading.Event()
    minutes = int(os.environ.get("SYNC_INTERVAL_MINUTES") or 0)
    if minutes > 0:
        threading.Thread(target=_scheduler, args=(stop, minutes), daemon=True).start()
    yield
    stop.set()


def create_app() -> FastAPI:
    app = FastAPI(title="Ops Hub", lifespan=lifespan)

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        """When OPSHUB_PASSWORD is set, every page and API call needs HTTP Basic auth."""
        password = os.environ.get("OPSHUB_PASSWORD")
        if password and request.url.path != "/healthz":
            user = os.environ.get("OPSHUB_USER", "ops")
            header = request.headers.get("authorization", "")
            ok = False
            if header.lower().startswith("basic "):
                try:
                    u, _, p = base64.b64decode(header[6:]).decode().partition(":")
                    ok = secrets.compare_digest(u, user) and secrets.compare_digest(p, password)
                except Exception:
                    ok = False
            if not ok:
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Ops Hub"'})
        return await call_next(request)

    app.include_router(router_api())
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    return app


# --- routes ----------------------------------------------------------------------------

def router_api():
    from fastapi import APIRouter

    r = APIRouter(prefix="/api")

    @r.get("/overview")
    def overview(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        now = utcnow()
        primaries = list(session.scalars(select(Order).where(Order.linked_order_id.is_(None))
                                         .options(selectinload(Order.warehouse_records))))
        active = [o for o in primaries if orders.effective_status(o) in ACTIVE_ORDER_STATUSES]
        open_ships = list(session.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATUSES))))
        summaries = [shipments.summarize(x, now, s.shipment_stale_days) for x in open_ships]
        lots = inventory.lots(session, now, s.sell_through_days, s.lot_policy)
        expiring = [l for l in lots if l["days_left"] is not None and l["days_left"] <= s.expiry_warn_days]
        alert_counts = dict(session.execute(select(Alert.severity, func.count()).where(Alert.status == "open")
                                            .group_by(Alert.severity)).all())
        over = Decimal("0")
        for inv in session.scalars(select(Invoice).where(Invoice.status != "approved")):
            over += Decimal(billing.audit(session, inv, s.billing_tolerance)["overbilled"])
        last = session.scalar(select(func.max(SyncRun.finished_at)).where(SyncRun.status == "ok"))
        return {
            "now": iso(now), "demo_mode": s.demo_mode, "timezone": s.timezone, "last_sync": iso(last),
            "orders": {
                "active": len(active),
                "overdue": sum(1 for o in active if o.required_by and o.required_by < now),
                "due_24h": sum(1 for o in active if o.required_by and now <= o.required_by
                               <= now + timedelta(hours=s.deadline_urgent_hours)),
                "flagged": sum(1 for o in active if o.note_flags),
                "priority": sum(1 for o in active if o.order_type == "priority"),
                "hazmat": sum(1 for o in active if o.is_hazmat),
            },
            "alerts": {k: alert_counts.get(k, 0) for k in ("urgent", "warn", "info")},
            "shipments": {"open": len(summaries), "late": sum(1 for x in summaries if x["late"]),
                          "stale": sum(1 for x in summaries if x["stale"]),
                          "hazmat": sum(1 for x in summaries if x["hazmat"])},
            "inventory": {"expiring_lots": len(expiring), "expiring_units": sum(l["qty"] for l in expiring),
                          "at_risk_units": sum(l["at_risk_units"] for l in expiring)},
            "billing": {"open_overbilled": str(over)},
        }

    # alerts

    @r.get("/alerts")
    def list_alerts(status: str = "open", session: Session = Depends(db.get_db)):
        q = select(Alert)
        if status != "all":
            q = q.where(Alert.status.in_(status.split(",")))
        rank = {"urgent": 0, "warn": 1, "info": 2}
        rows = sorted(session.scalars(q), key=lambda a: (rank.get(a.severity, 3), a.kind, a.title))
        return [{"id": a.id, "kind": a.kind, "severity": a.severity, "status": a.status, "title": a.title,
                 "detail": a.detail, "subject_type": a.subject_type, "subject_id": a.subject_id,
                 "created_at": iso(a.created_at), "last_seen_at": iso(a.last_seen_at)} for a in rows]

    @r.post("/alerts/{alert_id}/{action}")
    def alert_action(alert_id: int, action: str, session: Session = Depends(db.get_db)):
        a = session.get(Alert, alert_id) or _404("alert")
        new = {"ack": "acked", "dismiss": "dismissed", "reopen": "open"}.get(action) or _400("unknown action")
        a.status = new
        session.commit()
        return {"id": a.id, "status": a.status}

    # orders

    @r.get("/orders")
    def list_orders(view: str = "watch", q: str | None = None, session: Session = Depends(db.get_db),
                    s: Settings = Depends(settings_dep)):
        now = utcnow()
        alerted = {a.subject_id for a in session.scalars(select(Alert).where(Alert.subject_type == "order",
                                                                              Alert.status == "open"))}
        rows = session.scalars(select(Order).where(Order.linked_order_id.is_(None)).options(
            selectinload(Order.lines), selectinload(Order.warehouse_records), selectinload(Order.ship_from)))
        out = []
        for o in rows:
            d = orders.to_dict(o, now)
            active = d["status"] in ACTIVE_ORDER_STATUSES
            if view == "watch" and not (active and (o.required_by or o.note_flags or o.is_hazmat or o.id in alerted
                                                    or o.order_type != "standard")):
                continue
            if view == "active" and not active:
                continue
            if view in ("priority", "subscription", "seasonal", "wholesale") and (o.order_type != view or not active):
                continue
            if view == "hazmat" and not (o.is_hazmat and active):
                continue
            if q and q.lower() not in f"{o.number} {o.customer_name} {o.notes}".lower():
                continue
            d["has_alert"] = o.id in alerted
            out.append(d)
        far = "9999"
        out.sort(key=lambda d: (d["required_by"] or far, d["placed_at"] or far) if view != "all"
                 else (d["placed_at"] or ""), reverse=view == "all")
        return out[:500]

    @r.get("/orders/{order_id}")
    def get_order(order_id: int, session: Session = Depends(db.get_db)):
        o = session.get(Order, order_id) or _404("order")
        d = orders.to_dict(o, utcnow())
        d["shipments"] = [x.reference for x in session.scalars(select(Shipment).where(Shipment.order_id == o.id))]
        return d

    # inventory

    @r.get("/inventory/stock")
    def stock(session: Session = Depends(db.get_db)):
        return inventory.stock_matrix(session)

    @r.get("/inventory/sell-through")
    def sell_through(days: int | None = None, session: Session = Depends(db.get_db),
                     s: Settings = Depends(settings_dep)):
        days = days or s.sell_through_days
        return {"days": days, "rows": inventory.sell_through(session, days, utcnow())}

    @r.get("/inventory/lots")
    def lots(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        return {"policy": s.lot_policy, "warn_days": s.expiry_warn_days,
                "rows": inventory.lots(session, utcnow(), s.sell_through_days, s.lot_policy)}

    @r.get("/inventory/allocate")
    def allocate(sku: str, qty: int, location: str | None = None, min_days: int = 0, ship_date: str | None = None,
                 session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        return inventory.allocate(session, sku, qty, location_code=location, min_days_left=min_days,
                                  on_date=parse_date(ship_date) or utcnow().date(), policy=s.lot_policy)

    # shipments

    @r.get("/shipments")
    def list_shipments(status: str = "open", session: Session = Depends(db.get_db),
                       s: Settings = Depends(settings_dep)):
        q = select(Shipment).options(selectinload(Shipment.legs), selectinload(Shipment.order))
        if status == "open":
            q = q.where(Shipment.status.in_(OPEN_SHIPMENT_STATUSES))
        now = utcnow()
        rows = [shipments.summarize(x, now, s.shipment_stale_days) for x in session.scalars(q)]
        return sorted(rows, key=lambda x: (not (x["late"] or x["stale"]), x["eta"] or "9999"))

    @r.get("/shipments/{shipment_id}")
    def get_shipment(shipment_id: int, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        shp = session.get(Shipment, shipment_id) or _404("shipment")
        return shipments.detail(shp, utcnow(), s.shipment_stale_days)

    @r.post("/shipments/{shipment_id}/updates")
    def add_shipment_update(shipment_id: int, body: ShipmentUpdateIn, session: Session = Depends(db.get_db),
                            s: Settings = Depends(settings_dep)):
        """Log an update by hand: a call or WhatsApp from a driver, or a handoff to a new carrier/agent."""
        shp = session.get(Shipment, shipment_id) or _404("shipment")
        eta = parse_date(body.eta)
        u = NTrackingUpdate(occurred_at=parse_iso(body.occurred_at) or utcnow(), status=body.status,
                            shipment_ref=shp.reference, location_text=body.location, description=body.description,
                            eta=end_of_day_utc(eta, s.timezone) if eta else None, new_carrier=body.new_carrier,
                            new_agent=body.new_agent, new_tracking_ref=body.new_tracking_ref, source=body.source)
        shipments.apply_update(session, u)
        session.commit()
        sync.run_rules(session, s)
        return shipments.detail(session.get(Shipment, shipment_id), utcnow(), s.shipment_stale_days)

    # billing

    @r.get("/billing/invoices")
    def invoices(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        out = []
        for inv in session.scalars(select(Invoice).order_by(Invoice.period_start.desc())):
            a = billing.audit(session, inv, s.billing_tolerance)
            out.append({**a["invoice"], "billed_total": a["billed_total"], "expected_total": a["expected_total"],
                        "overbilled": a["overbilled"], "issues": a["issues"]})
        return out

    @r.get("/billing/invoices/{invoice_id}/audit")
    def invoice_audit(invoice_id: int, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        inv = session.get(Invoice, invoice_id) or _404("invoice")
        return billing.audit(session, inv, s.billing_tolerance)

    @r.post("/billing/invoices/{invoice_id}/status")
    def invoice_status(invoice_id: int, body: InvoiceStatusIn, session: Session = Depends(db.get_db),
                       s: Settings = Depends(settings_dep)):
        if body.status not in ("audited", "disputed", "approved"):
            _400("status must be audited, disputed or approved")
        inv = session.get(Invoice, invoice_id) or _404("invoice")
        inv.status = body.status
        session.commit()
        sync.run_rules(session, s)
        return {"id": inv.id, "status": inv.status}

    @r.get("/billing/rate-card")
    def rate_card(session: Session = Depends(db.get_db)):
        return [{"vendor": e.vendor, "charge_code": e.charge_code, "description": e.description, "unit": e.unit,
                 "rate": str(e.rate), "requires_approval": e.requires_approval,
                 "effective_from": e.effective_from.isoformat(),
                 "effective_to": e.effective_to.isoformat() if e.effective_to else None}
                for e in session.scalars(select(RateCardEntry).order_by(RateCardEntry.vendor, RateCardEntry.charge_code))]

    # connectors & sync

    @r.get("/connectors")
    def connectors(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        out = []
        for c in all_connectors():
            d = c.describe(s.demo_mode)
            run = session.scalar(select(SyncRun).where(SyncRun.connector == c.key).order_by(SyncRun.id.desc()))
            d["last_run"] = None if run is None else {"status": run.status, "mode": run.mode, "stats": run.stats,
                                                      "error": run.error, "finished_at": iso(run.finished_at)}
            out.append(d)
        return out

    @r.post("/connectors/{key}/sync")
    def sync_one(key: str, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        try:
            get_connector(key)
        except KeyError:
            _404("connector")
        run = sync.run_connector(session, key, s)
        rules_result = sync.run_rules(session, s)
        return {"connector": key, "status": run.status, "mode": run.mode, "stats": run.stats, "error": run.error,
                **rules_result}

    @r.post("/sync")
    def sync_everything(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        return sync.sync_all(session, s)

    @r.post("/rules/run")
    def rules_run(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        return sync.run_rules(session, s)

    @r.get("/notifications")
    def notifications(limit: int = 100, session: Session = Depends(db.get_db)):
        rows = session.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit))
        return [{"id": n.id, "channel": n.channel, "status": n.status, "body": n.body, "error": n.error,
                 "created_at": iso(n.created_at)} for n in rows]

    @r.get("/digest", response_class=PlainTextResponse)
    def digest_preview(session: Session = Depends(db.get_db)):
        return notify.digest_text(session)

    @r.post("/digest")
    def digest_send(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        text = notify.digest_text(session)
        channels = [c.strip() for c in (os.environ.get("DIGEST_CHANNELS") or "google_chat,telegram").split(",")]
        sent = [n.channel + ":" + n.status for c in channels if (n := notify.deliver(session, s, c, text))]
        session.commit()
        return {"sent": sent, "text": text}

    @r.get("/calendar/events")
    def calendar_events(days: int = 30, session: Session = Depends(db.get_db)):
        return calendar.build_events(session, utcnow(), days)

    @r.post("/calendar/publish")
    def calendar_publish(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        events = calendar.build_events(session, utcnow())
        conn = get_connector("google_calendar")
        if conn.mode(s.demo_mode) != "live":
            return {"published": 0, "mode": conn.mode(s.demo_mode), "would_publish": len(events)}
        try:
            conn.push_events(events)
        except NotWiredYet as exc:
            raise HTTPException(501, str(exc))
        return {"published": len(events), "mode": "live"}

    # exports (CSV, e.g. for a Google Sheets import)

    @r.get("/export/{name}.csv")
    def export(name: str, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        now = utcnow()
        if name == "stock":
            m = inventory.stock_matrix(session)
            codes = [l["code"] for l in m["locations"]]
            header = ["sku", "name", *codes, "total_on_hand", "total_available"]
            rows = [[x["sku"], x["name"], *[x["by_location"].get(c, {}).get("on_hand", "") for c in codes],
                     x["on_hand"], x["available"]] for x in m["rows"]]
        elif name == "sell-through":
            data = inventory.sell_through(session, s.sell_through_days, now)
            header = ["sku", "name", "sold", "on_hand", "sell_through_pct", "per_day", "days_of_supply"]
            rows = [[x[k] for k in header] for x in data]
        elif name == "lots":
            data = inventory.lots(session, now, s.sell_through_days, s.lot_policy)
            header = ["sku", "location", "lot_number", "received_on", "best_by", "days_left", "qty", "at_risk_units"]
            rows = [[x[k] for k in header] for x in data]
        elif name == "watchlist":
            data = list_orders("watch", None, session, s)
            header = ["number", "customer", "status", "order_type", "required_by", "note_flags", "notes"]
            rows = [[x["number"], x["customer"], x["status"], x["order_type"], x["required_by"],
                     "; ".join(x["note_flags"]), (x["notes"] or "").replace("\n", " ")] for x in data]
        elif name == "shipments":
            data = list_shipments("all", session, s)
            header = ["reference", "direction", "mode", "status", "carrier", "agent", "tracking_ref", "eta",
                      "hazmat", "last_event_at", "stale", "late"]
            rows = [[x[k] for k in header] for x in data]
        else:
            _404("export")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}-{now:%Y%m%d}.csv"'})

    return r


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


def _400(msg: str):
    raise HTTPException(400, msg)


app = create_app()
