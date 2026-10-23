"""Write a connector's SyncBatch into the unified tables.

Upserts are keyed on each record's natural key, so re-running a sync is safe.
Master-data sources (the ops Google Sheet) overwrite product, location and
carrier fields; other sources only fill gaps or create what is missing.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..connectors.base import SyncBatch
from ..models import (Carrier, InventoryLot, Location, Order, OrderLine, Product, RateCardEntry, SaleLine, SkuAlias,
                      StandingOrder, StockLevel, StockSnapshot, WarehouseActivity)
from ..timeutil import utcnow
from . import billing, documents, shipments


class Resolver:
    """Finds (or creates) the location and product a source's reference points at."""

    def __init__(self, session: Session, source: str):
        self.s, self.source = session, source
        self._locs: dict[str, Location] = {}
        self._prods: dict[str, Product] = {}
        self.created_products: list[str] = []

    def location(self, ref: str | None) -> Location | None:
        if not ref:
            return None
        ref = str(ref)
        if ref not in self._locs:
            loc = (self.s.scalar(select(Location).where(Location.source == self.source, Location.external_id == ref))
                   or self.s.scalar(select(Location).where(Location.code == ref)))
            if loc is None:
                loc = Location(code=f"{self.source}:{ref}"[:64], name=f"{self.source} {ref}", kind="unknown",
                               source=self.source, external_id=ref)
                self.s.add(loc)
                self.s.flush()
            self._locs[ref] = loc
        return self._locs[ref]

    def product(self, ref: str | None, title: str | None = None) -> Product | None:
        if not ref:
            return None
        ref = str(ref)
        if ref not in self._prods:
            alias = self.s.scalar(select(SkuAlias).where(SkuAlias.source == self.source, SkuAlias.external_id == ref))
            p = (self.s.get(Product, alias.product_id) if alias
                 else self.s.scalar(select(Product).where(Product.sku == ref)))
            if p is None:
                p = Product(sku=ref, name=title or ref, auto_created=True)
                self.s.add(p)
                self.s.flush()
                self.created_products.append(ref)
            self._prods[ref] = p
        return self._prods[ref]


def apply_batch(session: Session, batch: SyncBatch, source: str, settings: Settings, authoritative: bool = False,
                now: datetime | None = None, env: dict[str, str] | None = None) -> dict:
    now = now or utcnow()
    stats: Counter = Counter()
    r = Resolver(session, source)

    for n in batch.locations:
        owner = n.source or source
        loc = None
        if n.external_id:
            loc = session.scalar(select(Location).where(Location.source == owner, Location.external_id == n.external_id))
        loc = loc or session.scalar(select(Location).where(Location.code == n.code))
        if loc is None:
            loc = Location(code=n.code, name=n.name, kind=n.kind, source=owner, external_id=n.external_id,
                           address=n.address)
            session.add(loc)
            stats["locations_created"] += 1
        elif authoritative:
            loc.code, loc.name, loc.kind = n.code, n.name, n.kind
            loc.source, loc.external_id, loc.address = owner, n.external_id, n.address
        session.flush()

    for n in batch.products:
        p = session.scalar(select(Product).where(Product.sku == n.sku))
        if p is None:
            p = Product(sku=n.sku, name=n.name, auto_created=not authoritative)
            session.add(p)
            stats["products_created"] += 1
        fields = ("name", "upc", "category", "hazmat", "hazmat_class", "un_number", "seasonal", "season",
                  "shelf_life_days", "unit_cost")
        for f in fields:
            value = getattr(n, f)
            if value is None:
                continue
            if authoritative or getattr(p, f) in (None, "") or (f == "name" and p.auto_created):
                setattr(p, f, value)
        if authoritative:
            p.auto_created = False
        session.flush()
        if n.external_id and n.external_id != n.sku:
            alias = session.scalar(select(SkuAlias).where(SkuAlias.source == source, SkuAlias.external_id == n.external_id))
            if alias is None:
                session.add(SkuAlias(source=source, external_id=n.external_id, product_id=p.id))
            else:
                alias.product_id = p.id

    for n in batch.carriers:
        c = session.scalar(select(Carrier).where(Carrier.name == n.name)) or Carrier(name=n.name)
        c.aliases, c.hazmat_approved = n.aliases, n.hazmat_approved
        c.tracking_url_template, c.contact = n.tracking_url_template, n.contact
        session.add(c)
        stats["carriers"] += 1

    for n in batch.rates:
        e = session.scalar(select(RateCardEntry).where(RateCardEntry.vendor == n.vendor,
                                                       RateCardEntry.charge_code == n.charge_code,
                                                       RateCardEntry.effective_from == n.effective_from))
        e = e or RateCardEntry(vendor=n.vendor, charge_code=n.charge_code, effective_from=n.effective_from)
        e.description, e.unit, e.rate, e.min_charge = n.description, n.unit, n.rate, n.min_charge
        e.requires_approval, e.effective_to = n.requires_approval, n.effective_to
        session.add(e)
        stats["rates"] += 1

    for n in batch.standing_orders:
        so = session.scalar(select(StandingOrder).where(StandingOrder.code == n.code)) or StandingOrder(code=n.code)
        so.customer_name, so.cadence_days, so.next_due = n.customer_name, n.cadence_days, n.next_due
        so.min_days_to_best_by, so.lines, so.notes, so.active = n.min_days_to_best_by, n.lines, n.notes, n.active
        loc = r.location(n.ship_from)
        so.ship_from_location_id = loc.id if loc else None
        session.add(so)
        stats["standing_orders"] += 1
    session.flush()

    _apply_stock(session, batch, source, r, now, stats)
    _apply_lots(session, batch, source, r, now, stats)
    _apply_sales(session, batch, source, r, stats)
    _apply_orders(session, batch, source, r, now, stats)

    for n in batch.shipments:
        _, created = shipments.upsert_shipment(session, n, source, authoritative, r.location)
        stats["shipments_created" if created else "shipments_updated"] += 1
        session.flush()
    for u in batch.tracking:
        stats["tracking_applied" if shipments.apply_update(session, u) else "tracking_unmatched"] += 1

    for n in batch.activities:
        a = session.scalar(select(WarehouseActivity).where(
            WarehouseActivity.vendor == n.vendor, WarehouseActivity.charge_code == n.charge_code,
            WarehouseActivity.activity_date == n.activity_date, WarehouseActivity.reference == n.reference))
        if a is None:
            session.add(WarehouseActivity(vendor=n.vendor, charge_code=n.charge_code, activity_date=n.activity_date,
                                          qty=n.qty, reference=n.reference, source=source))
        else:
            a.qty = n.qty
        stats["activities"] += 1
    for n in batch.invoices:
        _, changed = billing.upsert_invoice(session, n, source)
        stats["invoices"] += int(changed)
    session.flush()

    for doc in batch.documents:
        stats[f"documents_{documents.process(session, doc, source, settings, env)}"] += 1
        session.flush()

    if r.created_products:
        stats["unknown_skus"] = len(r.created_products)
    return dict(stats)


def _apply_stock(session, batch, source, r: Resolver, now, stats) -> None:
    seen_by_loc: dict[int, set[int]] = {}
    today = now.date()
    for n in batch.stock:
        loc = r.location(n.location)
        if loc.source and loc.source != source:
            stats["stock_skipped_not_owner"] += 1
            continue
        p = r.product(n.sku)
        sl = session.scalar(select(StockLevel).where(StockLevel.product_id == p.id, StockLevel.location_id == loc.id))
        if sl is None:
            sl = StockLevel(product_id=p.id, location_id=loc.id, source=source)
            session.add(sl)
        sl.on_hand, sl.allocated = n.on_hand, n.allocated
        sl.available = n.available if n.available is not None else n.on_hand - n.allocated
        sl.source, sl.updated_at = source, now
        seen_by_loc.setdefault(loc.id, set()).add(p.id)
        snap = session.scalar(select(StockSnapshot).where(StockSnapshot.product_id == p.id,
                                                          StockSnapshot.location_id == loc.id,
                                                          StockSnapshot.as_of == today))
        if snap is None:
            session.add(StockSnapshot(product_id=p.id, location_id=loc.id, as_of=today, on_hand=n.on_hand))
        else:
            snap.on_hand = n.on_hand
        stats["stock"] += 1
    # A full stock pull that omits an item means the location no longer has any.
    for loc_id, pids in seen_by_loc.items():
        for sl in session.scalars(select(StockLevel).where(StockLevel.location_id == loc_id,
                                                           StockLevel.source == source)):
            if sl.product_id not in pids and sl.on_hand:
                sl.on_hand = sl.allocated = sl.available = 0
                sl.updated_at = now
                stats["stock_zeroed"] += 1
    session.flush()


def _apply_lots(session, batch, source, r: Resolver, now, stats) -> None:
    seen: dict[int, set[int]] = {}
    for n in batch.lots:
        loc, p = r.location(n.location), r.product(n.sku)
        lot = session.scalar(select(InventoryLot).where(InventoryLot.product_id == p.id,
                                                        InventoryLot.location_id == loc.id,
                                                        InventoryLot.lot_number == n.lot_number))
        if lot is None:
            lot = InventoryLot(product_id=p.id, location_id=loc.id, lot_number=n.lot_number, source=source)
            session.add(lot)
        lot.qty_on_hand, lot.best_by, lot.received_on, lot.updated_at = n.qty_on_hand, n.best_by, n.received_on, now
        session.flush()
        seen.setdefault(loc.id, set()).add(lot.id)
        stats["lots"] += 1
    if batch.lots_complete:
        for loc_id, ids in seen.items():
            for lot in session.scalars(select(InventoryLot).where(InventoryLot.location_id == loc_id,
                                                                  InventoryLot.source == source)):
                if lot.id not in ids:
                    session.delete(lot)  # used up
                    stats["lots_closed"] += 1
    session.flush()


def _apply_sales(session, batch, source, r: Resolver, stats) -> None:
    if not batch.sales:
        return
    existing = {s.external_id: s for s in session.scalars(select(SaleLine).where(SaleLine.source == source))}
    for n in batch.sales:
        loc, p = r.location(n.location), r.product(n.sku)
        s = existing.get(n.external_id)
        if s is None:
            s = SaleLine(source=source, external_id=n.external_id)
            session.add(s)
            existing[n.external_id] = s
            stats["sales_created"] += 1
        s.product_id, s.location_id, s.sold_at, s.qty, s.net_amount = p.id, loc.id, n.sold_at, n.qty, n.net_amount
    session.flush()


def _apply_orders(session, batch, source, r: Resolver, now, stats) -> None:
    if not batch.orders:
        return
    existing = {o.external_id: o for o in session.scalars(select(Order).where(Order.source == source))}
    for n in batch.orders:
        o = existing.get(n.external_id)
        if o is None:
            o = Order(source=source, external_id=n.external_id, status=n.status,
                      status_changed_at=n.updated_at or n.placed_at or now)
            session.add(o)
            existing[n.external_id] = o
            stats["orders_created"] += 1
        elif o.status != n.status:
            o.status_changed_at = n.updated_at or now
            stats["orders_status_changed"] += 1
        o.number, o.reference, o.channel, o.customer_name = n.number, n.reference, n.channel, n.customer_name
        o.status, o.raw_status, o.placed_at, o.source_updated_at = n.status, n.raw_status, n.placed_at, n.updated_at
        o.notes, o.tags, o.attributes, o.system_deadline = n.notes, n.tags, n.attributes, n.required_by
        o.order_type = n.order_type or "standard"
        loc = r.location(n.ship_from)
        o.ship_from_location_id = loc.id if loc else None
        o.synced_at = now
        o.lines.clear()
        for l in n.lines:
            p = r.product(l.sku, l.title) if l.sku else None
            o.lines.append(OrderLine(product_id=p.id if p else None, sku=l.sku, title=l.title, qty=l.qty,
                                     qty_fulfilled=l.qty_fulfilled))
    session.flush()
