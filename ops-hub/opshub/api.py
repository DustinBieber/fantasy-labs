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

from . import db, settings_store
from .config import Settings, get_settings
from .connectors import all_connectors, get_connector
from .connectors.base import NotWiredYet, NTrackingUpdate
from .models import (ACTIVE_ORDER_STATUSES, OPEN_SHIPMENT_STATUSES, Alert, HandoffFile, InboxItem, Invoice,
                     KeyValue, Notification, Order, RateCardEntry, Shipment, SyncRun)
from .services import billing, calendar, chatops, inventory, jobs, notify, orders, shipments, sync, trackers
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


class SetupSaveIn(BaseModel):
    values: dict[str, str]


class ShipmentIn(BaseModel):
    reference: str
    direction: str = "inbound"
    mode: str = "ltl"
    carrier: str
    agent: str | None = None
    tracking_ref: str | None = None
    origin: str | None = None
    destination: str | None = None
    eta: str | None = None  # YYYY-MM-DD
    hazmat: bool = False
    un_numbers: list[str] = []
    priority: bool = False
    order_number: str | None = None
    notes: str | None = None


# --- app -------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.create_all()
    stop = threading.Event()
    if get_settings().scheduler:
        threading.Thread(target=jobs.run_forever, args=(stop,), daemon=True, name="opshub-scheduler").start()
    yield
    stop.set()


def create_app() -> FastAPI:
    app = FastAPI(title="Ops Hub", lifespan=lifespan)

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        """When OPSHUB_PASSWORD is set, every page and API call needs HTTP Basic auth, except reads made with
        the read-only API key (OPSHUB_READ_TOKEN) as a Bearer token or ?token= on /api URLs."""
        path = request.url.path
        # Webhooks authenticate with the sender's signature instead.
        # The phone's home-screen install fetches the manifest and icons without credentials.
        if path == "/healthz" or path.startswith(("/webhooks/", "/static/pwa/")):
            return await call_next(request)
        header = request.headers.get("authorization", "")
        key = header[7:].strip() if header.lower().startswith("bearer ") else (
            request.query_params.get("token") if path.startswith("/api/") else None)
        if key:
            expected = settings_store.effective_env().get("OPSHUB_READ_TOKEN") or ""
            if len(expected) < 16 or not secrets.compare_digest(key, expected):
                return Response(status_code=401, content="Unknown API key")
            readable = request.method in ("GET", "HEAD") and (
                path == "/openapi.json" or (path.startswith("/api/") and not path.startswith("/api/setup")))
            if not readable:
                return Response(status_code=403, content="The read-only API key can only read /api data")
            return await call_next(request)
        password = os.environ.get("OPSHUB_PASSWORD")
        if password:
            user = os.environ.get("OPSHUB_USER", "ops")
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
    app.include_router(router_setup())
    app.include_router(router_webhooks())
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
        if a.dedupe_key.startswith("inbox:") and action == "dismiss":  # a done Muse task/alert
            item = session.get(InboxItem, int(a.dedupe_key.split(":")[1]))
            if item:
                item.status = "done"
        session.commit()
        return {"id": a.id, "status": a.status}

    # orders

    @r.get("/orders")
    def list_orders(view: str = "watch", q: str | None = None, session: Session = Depends(db.get_db),
                    s: Settings = Depends(settings_dep)):
        return orders.list_view(session, utcnow(), view, q)

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

    @r.post("/shipments")
    def create_shipment(body: ShipmentIn, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        """Book freight by hand (carriers that only email or phone): it is then tracked like any other."""
        from .connectors.base import NShipment
        from .services.ingest import Resolver

        if session.scalar(select(Shipment).where(Shipment.reference == body.reference)):
            _400("a shipment with that reference exists")
        eta = parse_date(body.eta)
        n = NShipment(reference=body.reference, direction=body.direction, mode=body.mode, carrier_name=body.carrier,
                      agent_name=body.agent, tracking_ref=body.tracking_ref, origin=body.origin,
                      destination=body.destination, eta=end_of_day_utc(eta, s.timezone) if eta else None,
                      hazmat=body.hazmat, un_numbers=body.un_numbers, priority=body.priority,
                      order_number=body.order_number, notes=body.notes, booked_at=utcnow())
        shp, _ = shipments.upsert_shipment(session, n, "manual", True, Resolver(session, "manual").location)
        session.commit()
        sync.run_rules(session, s)
        return shipments.detail(shp, utcnow(), s.shipment_stale_days)

    # imports, inbox, handoffs, jobs

    @r.post("/import/{source}")
    async def import_file(source: str, request: Request, filename: str, session: Session = Depends(db.get_db),
                          s: Settings = Depends(settings_dep)):
        """Upload an export file (CSV/XLSX) as the raw request body: Shopify, Lightspeed, Extensiv or RQ."""
        from .connectors import get_connector
        from .services import ingest

        source = {"rq": "iqmetrix_rq"}.get(source, source)
        try:
            conn = get_connector(source)
        except KeyError:
            _404("source")
        if not conn.imports:
            _400(f"{source} doesn't take file imports")
        content = await request.body()
        if not content:
            _400("empty file")
        batch, report = conn.import_files([(filename, content)], s.timezone)
        if not (batch.stock or batch.sales or batch.orders or batch.lots):
            return {"imported": False, "files": report}
        stats = ingest.apply_batch(session, batch, source, s)
        stats["orders_linked"] = orders.link_orders(session)
        session.flush()
        orders.refresh_signals(session, s)
        session.add(SyncRun(connector=source, mode="upload", status="ok", stats=stats, finished_at=utcnow()))
        session.commit()
        sync.run_rules(session, s)
        return {"imported": True, "files": report, "stats": stats}

    @r.get("/inbox")
    def inbox(status: str = "open", session: Session = Depends(db.get_db)):
        q = select(InboxItem).order_by(InboxItem.received_at.desc())
        if status != "all":
            q = q.where(InboxItem.status == status)
        return [{"id": i.id, "source": i.source, "kind": i.kind, "severity": i.severity, "title": i.title,
                 "detail": i.detail, "due": i.due.isoformat() if i.due else None, "status": i.status,
                 "subject_type": i.subject_type, "subject_id": i.subject_id, "file": i.file_name,
                 "received_at": iso(i.received_at)} for i in session.scalars(q.limit(200))]

    @r.post("/inbox/{item_id}/done")
    def inbox_done(item_id: int, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        item = session.get(InboxItem, item_id) or _404("inbox item")
        item.status = "done"
        session.commit()
        sync.run_rules(session, s)
        return {"id": item.id, "status": item.status}

    @r.get("/handoffs")
    def handoffs(partner: str = "muse", session: Session = Depends(db.get_db)):
        rows = session.scalars(select(HandoffFile).where(HandoffFile.partner == partner)
                               .order_by(HandoffFile.id.desc()).limit(60))
        return [{"id": h.id, "direction": h.direction, "name": h.name, "location": h.location, "summary": h.summary,
                 "created_at": iso(h.created_at)} for h in rows]

    @r.get("/jobs")
    def jobs_status(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        import json as _json

        out = []
        for name in jobs.JOBS:
            row = session.get(KeyValue, f"job:{name}:last")
            out.append({"name": name, "last": _json.loads(row.value) if row else None})
        return {"scheduler": s.scheduler, "sync_interval_minutes": s.sync_interval_minutes,
                "digest_time": s.digest_time, "muse_export_time": s.muse_export_time, "timezone": s.timezone,
                "jobs": out}

    @r.post("/jobs/{name}")
    def run_job(name: str, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        if name not in jobs.JOBS:
            _404("job")
        try:
            return {"job": name, "result": jobs.run_job(session, name, s)}
        except NotWiredYet as exc:
            raise HTTPException(501, str(exc))
        except Exception as exc:
            session.rollback()
            raise HTTPException(502, f"{name} failed: {exc}")

    @r.get("/google/status")
    def google_status():
        from .connectors import google_auth

        return {"method": google_auth.method(settings_store.effective_env()), "requirement": google_auth.AUTH_REQUIREMENT}

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
            d["interval_minutes"] = s.interval_for(c.key) if c.pulls and s.scheduler else None
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
        result = jobs.run_job(session, "digest", s)
        return {**result, "text": notify.digest_text(session)}

    @r.get("/calendar/events")
    def calendar_events(days: int = 30, session: Session = Depends(db.get_db)):
        return calendar.build_events(session, utcnow(), days)

    @r.post("/calendar/publish")
    def calendar_publish(session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        return jobs.run_job(session, "calendar", s)

    # inbound / outbound / productivity trackers

    @r.get("/trackers/{name}")
    def tracker(name: str, days: int = 30, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        if name not in trackers.TRACKERS:
            _404("tracker")
        return trackers.TRACKERS[name](session, s, days=max(1, min(days, 365)))

    # exports (CSV, e.g. for a Google Sheets import)

    @r.get("/export/{name}.csv")
    def export(name: str, days: int = 30, session: Session = Depends(db.get_db), s: Settings = Depends(settings_dep)):
        now = utcnow()
        days = max(1, min(days, 365))
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
            data = orders.list_view(session, now, "watch")
            header = ["number", "customer", "status", "order_type", "required_by", "note_flags", "notes"]
            rows = [[x["number"], x["customer"], x["status"], x["order_type"], x["required_by"],
                     "; ".join(x["note_flags"]), (x["notes"] or "").replace("\n", " ")] for x in data]
        elif name == "shipments":
            data = list_shipments("all", session, s)
            header = ["reference", "direction", "mode", "status", "carrier", "agent", "tracking_ref", "eta",
                      "hazmat", "last_event_at", "stale", "late"]
            rows = [[x[k] for k in header] for x in data]
        elif name == "inbound":
            data = trackers.inbound(session, s, now, days)
            header = ["status", "reference", "kind", "source", "vendor", "location", "expected_at", "arrived_at",
                      "received_at", "units_expected", "units_received", "short_units", "pallets", "late", "days_late",
                      "dock_to_stock_hours"]
            rows = [[x[k] for k in header] for x in data["open"] + data["received"]]
        elif name == "outbound":
            data = trackers.outbound(session, s, now, days)
            header = ["number", "customer", "origin", "status", "placed_at", "picked_at", "packed_at", "shipped_at",
                      "delivered_at", "order_to_ship_hours", "handled_by", "units"]
            rows = [[x[k] for k in header] for x in data["shipped_orders"]]
        elif name == "backlog":
            data = trackers.outbound(session, s, now, days)
            header = ["number", "customer", "origin", "status", "order_type", "placed_at", "age_hours", "picked_at",
                      "packed_at", "required_by", "hours_left", "units"]
            rows = [[x[k] for k in header] for x in data["backlog"]]
        elif name == "productivity":
            data = trackers.productivity(session, s, now, days)
            header = ["name", "location", "source", "revenue", "units", "lines", "hours", "shifts", "revenue_per_hour",
                      "units_per_hour"]
            rows = [[x[k] for k in header] for x in data["people"]]
        elif name == "warehouse-handlers":
            data = trackers.productivity(session, s, now, days)
            header = ["name", "orders", "units", "days_active", "orders_per_day", "pick_to_ship_hours"]
            rows = [[x[k] for k in header] for x in data["handlers"]]
        else:
            _404("export")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}-{now:%Y%m%d}.csv"'})

    return r


def router_setup():
    import json as _json
    import secrets as _secrets
    import time as _time

    import httpx
    from fastapi import APIRouter
    from fastapi.responses import RedirectResponse

    from .connectors import google_auth
    from .services import google_setup, kv
    from .setup_spec import SECTIONS, mask

    r = APIRouter()

    def base_url(request: Request) -> str:
        return (settings_store.effective_env().get("OPSHUB_PUBLIC_URL") or str(request.base_url)).rstrip("/")

    def new_state(name: str) -> str:
        state = _secrets.token_urlsafe(20)
        kv.set(f"oauth_state:{name}", _json.dumps({"state": state, "at": _time.time()}))
        return state

    def check_state(name: str, state: str | None) -> bool:
        saved = _json.loads(kv.get(f"oauth_state:{name}") or "{}")
        return bool(state) and saved.get("state") == state and _time.time() - saved.get("at", 0) < 900

    def done(msg: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(f"/?setup={quote(msg)}#setup", status_code=303)

    @r.get("/api/setup")
    def setup_state(request: Request, s: Settings = Depends(settings_dep)):
        from .connectors import get_connector

        saved, env = settings_store.saved(), settings_store.effective_env()
        base = base_url(request)
        out = []
        for sec in SECTIONS:
            fields = []
            for key, label, secret, help_text in sec["fields"]:
                value = env.get(key, "")
                fields.append({"key": key, "label": label, "secret": secret, "help": help_text,
                               "value": (mask(value) if secret else value) if value else "",
                               "set": bool(value), "source": "env" if settings_store.from_env(key) else
                               ("saved" if key in saved else None)})
            conns = []
            for ck in sec["connectors"]:
                c = get_connector(ck)
                conns.append({"key": ck, "name": c.name, "mode": c.mode(s.demo_mode), "missing": c.missing_settings(),
                              "pulls": c.pulls})
            out.append({**{k: sec[k] for k in ("key", "title", "connectors")}, "intro": sec["intro"].replace("{base}", base),
                        "actions": sec.get("actions", []), "fields": fields, "status": conns})
        return {"base_url": base, "google": google_auth.method(env), "demo_mode": s.demo_mode,
                "lightspeed_token": bool(kv.get("lightspeed_refresh_token")), "sections": out}

    @r.post("/api/setup")
    def setup_save(body: SetupSaveIn):
        from .setup_spec import ALL_KEYS, SECRET_KEYS

        values = {k: v for k, v in body.values.items() if k in ALL_KEYS}
        # A masked secret coming back unchanged means "keep the current value".
        values = {k: v for k, v in values.items() if not (k in SECRET_KEYS and v.startswith("••••"))}
        skipped = settings_store.save(values)
        google_auth.clear_cache()
        return {"saved": sorted(k for k in values if k not in skipped), "kept_from_env": skipped}

    @r.post("/api/setup/test/{key}")
    def setup_test(key: str, s: Settings = Depends(settings_dep)):
        from .connectors import get_connector

        try:
            c = get_connector(key)
        except KeyError:
            _404("connector")
        try:
            if c.is_configured():
                return {"ok": True, "mode": "live", "message": c.test()}
            if c.files_ready():
                return {"ok": True, "mode": "files", "message": c.test_files()}
            return {"ok": False, "mode": c.mode(s.demo_mode), "message": "Missing: " + ", ".join(c.missing_settings())}
        except NotImplementedError as exc:
            return {"ok": False, "message": str(exc)}
        except httpx.HTTPStatusError as exc:
            return {"ok": False, "message": f"{exc.response.status_code} from {exc.request.url.host}: "
                                            f"{exc.response.text[:300]}"}
        except Exception as exc:
            return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:300]}"}

    @r.post("/api/setup/read-token")
    def new_read_token():
        if settings_store.from_env("OPSHUB_READ_TOKEN"):
            _400("OPSHUB_READ_TOKEN is set in .env; change it there")
        token = _secrets.token_urlsafe(32)
        settings_store.save({"OPSHUB_READ_TOKEN": token})
        return {"token": token}

    @r.get("/setup/google/start")
    def google_start(request: Request):
        env = settings_store.effective_env()
        if not (env.get("GOOGLE_OAUTH_CLIENT_ID") and env.get("GOOGLE_OAUTH_CLIENT_SECRET")):
            return done("Save the Google OAuth client ID and secret first")
        redirect = base_url(request) + "/setup/google/callback"
        return RedirectResponse(google_auth.consent_url(env["GOOGLE_OAUTH_CLIENT_ID"], redirect, new_state("google")))

    @r.get("/setup/google/callback")
    def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
        if error or not code:
            return done(f"Google sign-in cancelled: {error or 'no code'}")
        if not check_state("google", state):
            return done("Google sign-in expired; try again")
        env = settings_store.effective_env()
        try:
            tokens = google_auth.exchange_code(env["GOOGLE_OAUTH_CLIENT_ID"], env["GOOGLE_OAUTH_CLIENT_SECRET"], code,
                                               base_url(request) + "/setup/google/callback")
        except httpx.HTTPStatusError as exc:
            return done(f"Google rejected the sign-in: {exc.response.text[:200]}")
        if not tokens.get("refresh_token"):
            return done("Google gave no refresh token: remove Ops Hub at myaccount.google.com/permissions and retry")
        google_auth.store_refresh_token(tokens["refresh_token"])
        return done("Google connected. Next: Set up Drive & Sheets")

    @r.post("/api/setup/google/init")
    def google_init(s: Settings = Depends(settings_dep)):
        if not google_auth.method(settings_store.effective_env()):
            _400("Sign in with Google first")
        try:
            return google_setup.initialize(s)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, f"Google said {exc.response.status_code}: {exc.response.text[:300]}")

    @r.get("/setup/lightspeed/start")
    def lightspeed_start():
        from urllib.parse import urlencode

        env = settings_store.effective_env()
        if not (env.get("LIGHTSPEED_CLIENT_ID") and env.get("LIGHTSPEED_CLIENT_SECRET")):
            return done("Save the Lightspeed client ID and secret first")
        authorize = env.get("LIGHTSPEED_AUTHORIZE_URL", "https://cloud.lightspeedapp.com/auth/oauth/authorize")
        return RedirectResponse(authorize + "?" + urlencode({
            "response_type": "code", "client_id": env["LIGHTSPEED_CLIENT_ID"], "state": new_state("lightspeed"),
            "scope": env.get("LIGHTSPEED_SCOPE", "employee:all")}))

    @r.get("/setup/lightspeed/callback")
    def lightspeed_callback(code: str | None = None, state: str | None = None, error: str | None = None):
        from .connectors.lightspeed import KV_REFRESH, TOKEN_URL, _token_cache

        if error or not code:
            return done(f"Lightspeed authorization cancelled: {error or 'no code'}")
        if not check_state("lightspeed", state):
            return done("Lightspeed authorization expired; try again")
        env = settings_store.effective_env()
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(env.get("LIGHTSPEED_TOKEN_URL", TOKEN_URL), data={
                    "client_id": env["LIGHTSPEED_CLIENT_ID"], "client_secret": env["LIGHTSPEED_CLIENT_SECRET"],
                    "code": code, "grant_type": "authorization_code"})
                resp.raise_for_status()
                tokens = resp.json()
                acct = client.get("https://api.lightspeedapp.com/API/V3/Account.json",
                                  headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
        except httpx.HTTPStatusError as exc:
            return done(f"Lightspeed rejected the authorization: {exc.response.text[:200]}")
        kv.set(KV_REFRESH, tokens["refresh_token"])
        _token_cache.clear()
        account = acct.get("Account") or {}
        account = account[0] if isinstance(account, list) else account
        if account.get("accountID"):
            settings_store.save({"LIGHTSPEED_ACCOUNT_ID": str(account["accountID"])})
        return done(f"Lightspeed connected: {account.get('name', 'account')} {account.get('accountID', '')}")

    @r.get("/api/setup/telegram/chats")
    def telegram_chats():
        from .connectors import get_connector

        seen = _json.loads(kv.get("telegram_seen_chats") or "{}")
        tg = get_connector("telegram")
        if tg.setting("TELEGRAM_BOT_TOKEN"):
            try:
                with tg.http() as client:
                    body = client.get(tg._url("getUpdates"), params={"timeout": 0}).json()
                for up in body.get("result", []):
                    chat = (up.get("message") or up.get("edited_message") or up.get("my_chat_member") or {}).get("chat") or {}
                    if chat.get("id") is not None:
                        seen[str(chat["id"])] = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
            except httpx.HTTPError:
                pass
        return [{"id": k, "name": v} for k, v in seen.items()]

    return r


def router_webhooks():
    import hashlib
    import hmac

    from fastapi import APIRouter

    r = APIRouter(prefix="/webhooks")

    @r.get("/whatsapp", response_class=PlainTextResponse)
    def whatsapp_verify(request: Request):
        """Meta's one-time webhook verification handshake."""
        p = request.query_params
        token = settings_store.effective_env().get("WHATSAPP_VERIFY_TOKEN")
        if token and p.get("hub.mode") == "subscribe" and secrets.compare_digest(p.get("hub.verify_token", ""), token):
            return p.get("hub.challenge", "")
        raise HTTPException(403, "verification failed")

    @r.post("/whatsapp")
    async def whatsapp_inbound(request: Request, session: Session = Depends(db.get_db),
                               s: Settings = Depends(settings_dep)):
        raw = await request.body()
        secret = settings_store.effective_env().get("WHATSAPP_APP_SECRET")
        if secret:
            expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            if not secrets.compare_digest(request.headers.get("x-hub-signature-256", ""), expected):
                raise HTTPException(401, "bad signature")
        elif os.environ.get("WHATSAPP_ALLOW_UNSIGNED", "").lower() not in ("1", "true", "yes"):
            raise HTTPException(503, "set WHATSAPP_APP_SECRET to accept WhatsApp webhooks")
        import json as _json

        replied = chatops.handle_whatsapp(session, s, _json.loads(raw or b"{}"))
        session.commit()
        if replied:
            sync.run_rules(session, s)
        return {"ok": True, "replied": replied}

    return r


def _404(what: str):
    raise HTTPException(404, f"{what} not found")


def _400(msg: str):
    raise HTTPException(400, msg)


app = create_app()
