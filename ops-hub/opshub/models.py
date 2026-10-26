"""Unified data model.

Every connector maps its vendor's records into these tables, so the dashboard,
rules and reports never need to know which system a record came from.
Datetimes are naive UTC (see timeutil).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, Dec
from .timeutil import utcnow

# --- vocabularies -----------------------------------------------------------

LOCATION_KINDS = ("warehouse", "partner_3pl", "store", "online", "in_transit", "unknown")
ORDER_STATUSES = ("open", "allocated", "processing", "partial", "on_hold", "shipped", "delivered", "cancelled")
ACTIVE_ORDER_STATUSES = ("open", "allocated", "processing", "partial", "on_hold")
ORDER_TYPES = ("standard", "priority", "seasonal", "subscription", "wholesale")
SHIPMENT_STATUSES = ("booked", "picked_up", "in_transit", "at_terminal", "handed_off", "out_for_delivery",
                     "delivered", "exception", "cancelled")
OPEN_SHIPMENT_STATUSES = ("booked", "picked_up", "in_transit", "at_terminal", "handed_off", "out_for_delivery",
                          "exception")
SEVERITIES = ("info", "warn", "urgent")


# --- master data --------------------------------------------------------------

class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default="unknown")
    # The one system whose stock numbers are trusted for this location.
    source: Mapped[str | None] = mapped_column(String(40))
    external_id: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(Text)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    upc: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(120))
    hazmat: Mapped[bool] = mapped_column(Boolean, default=False)
    hazmat_class: Mapped[str | None] = mapped_column(String(20))
    un_number: Mapped[str | None] = mapped_column(String(20))
    seasonal: Mapped[bool] = mapped_column(Boolean, default=False)
    season: Mapped[str | None] = mapped_column(String(60))
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    unit_cost: Mapped[Decimal | None] = mapped_column(Dec)
    # Created on the fly because a connector reported a SKU nobody set up in the product sheet.
    auto_created: Mapped[bool] = mapped_column(Boolean, default=False)


class SkuAlias(Base):
    """Maps a system's own item id or SKU to our SKU when they differ."""

    __tablename__ = "sku_aliases"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(120))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))


# --- inventory ------------------------------------------------------------------

class StockLevel(Base):
    __tablename__ = "stock_levels"
    __table_args__ = (UniqueConstraint("product_id", "location_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    allocated: Mapped[int] = mapped_column(Integer, default=0)
    available: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    product: Mapped[Product] = relationship()
    location: Mapped[Location] = relationship()


class StockSnapshot(Base):
    """Daily on-hand history, for trends and month-end storage checks."""

    __tablename__ = "stock_snapshots"
    __table_args__ = (UniqueConstraint("product_id", "location_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    as_of: Mapped[date] = mapped_column(Date)
    on_hand: Mapped[int] = mapped_column(Integer)


class InventoryLot(Base):
    """Lot-tracked stock with best-by dates (from systems that track lots, e.g. Extensiv)."""

    __tablename__ = "inventory_lots"
    __table_args__ = (UniqueConstraint("product_id", "location_id", "lot_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    lot_number: Mapped[str] = mapped_column(String(120))
    received_on: Mapped[date | None] = mapped_column(Date)
    best_by: Mapped[date | None] = mapped_column(Date)
    qty_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    product: Mapped[Product] = relationship()
    location: Mapped[Location] = relationship()


class SaleLine(Base):
    __tablename__ = "sale_lines"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(160))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    sold_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    qty: Mapped[int] = mapped_column(Integer)
    net_amount: Mapped[Decimal | None] = mapped_column(Dec)
    employee: Mapped[str | None] = mapped_column(String(160))


# --- orders -----------------------------------------------------------------------

class StandingOrder(Base):
    """A recurring customer order (standing subscription) that ships on a cadence."""

    __tablename__ = "standing_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    cadence_days: Mapped[int] = mapped_column(Integer)
    next_due: Mapped[date] = mapped_column(Date)
    # The customer will reject product with fewer days than this left before best-by.
    min_days_to_best_by: Mapped[int] = mapped_column(Integer, default=0)
    ship_from_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{"sku": ..., "qty": ...}]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    ship_from: Mapped[Location | None] = relationship()


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(120))
    number: Mapped[str] = mapped_column(String(120), index=True)
    # For warehouse records: the channel order number this fulfils (Extensiv reference number).
    reference: Mapped[str | None] = mapped_column(String(120), index=True)
    # Set when this record is the warehouse copy of another order (e.g. Extensiv copy of a Shopify order).
    linked_order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    channel: Mapped[str | None] = mapped_column(String(60))
    customer_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="open")
    raw_status: Mapped[str | None] = mapped_column(String(80))
    order_type: Mapped[str] = mapped_column(String(32), default="standard")
    placed_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # A deadline the source system stores itself (e.g. Extensiv ship-cancel date).
    system_deadline: Mapped[datetime | None] = mapped_column(DateTime)
    # A deadline set from outside the order systems (Muse handoff, dashboard).
    manual_deadline: Mapped[datetime | None] = mapped_column(DateTime)
    manual_deadline_source: Mapped[str | None] = mapped_column(String(40))
    # Notes added outside the order systems: [{"source": "muse", "note": ..., "at": iso}]
    external_notes: Mapped[list | None] = mapped_column(JSON)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Warehouse milestones (Extensiv) and delivery (carrier), for outbound and productivity tracking.
    picked_at: Mapped[datetime | None] = mapped_column(DateTime)
    packed_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    handled_by: Mapped[str | None] = mapped_column(String(160))
    # Earliest deadline found anywhere (system, attributes, tags, notes, AI, linked warehouse records).
    required_by: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    required_by_source: Mapped[str | None] = mapped_column(String(40))  # system|attribute|tag|note|ai|linked
    required_by_text: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    note_flags: Mapped[list] = mapped_column(JSON, default=list)  # matched keywords / AI highlights
    notes_hash: Mapped[str | None] = mapped_column(String(64))
    ai_signals: Mapped[dict | None] = mapped_column(JSON)
    is_hazmat: Mapped[bool] = mapped_column(Boolean, default=False)
    ship_from_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    standing_order_id: Mapped[int | None] = mapped_column(ForeignKey("standing_orders.id"))
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lines: Mapped[list[OrderLine]] = relationship(back_populates="order", cascade="all, delete-orphan")
    linked_order: Mapped[Order | None] = relationship(remote_side=[id], back_populates="warehouse_records")
    warehouse_records: Mapped[list[Order]] = relationship(back_populates="linked_order")
    ship_from: Mapped[Location | None] = relationship()
    standing_order: Mapped[StandingOrder | None] = relationship()


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    sku: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[int] = mapped_column(Integer, default=0)
    qty_fulfilled: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped[Order] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship()


# --- freight & shipments ----------------------------------------------------------------

class Carrier(Base):
    __tablename__ = "carriers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    hazmat_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    tracking_url_template: Mapped[str | None] = mapped_column(String(400))  # use {ref}
    contact: Mapped[str | None] = mapped_column(String(300))


class Shipment(Base):
    """One movement of goods, made of one or more legs as carriers and agents hand it off."""

    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(120), unique=True)  # PO, BOL, container or our own ref
    direction: Mapped[str] = mapped_column(String(20))  # inbound | outbound | transfer
    mode: Mapped[str] = mapped_column(String(20))  # parcel | ltl | ftl | ocean | air | courier
    status: Mapped[str] = mapped_column(String(32), default="booked")
    priority: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str | None] = mapped_column(String(200))
    destination: Mapped[str | None] = mapped_column(String(200))
    destination_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    eta: Mapped[datetime | None] = mapped_column(DateTime)
    hazmat: Mapped[bool] = mapped_column(Boolean, default=False)
    un_numbers: Mapped[list] = mapped_column(JSON, default=list)
    hazmat_notes: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)

    legs: Mapped[list[ShipmentLeg]] = relationship(back_populates="shipment", order_by="ShipmentLeg.seq",
                                                   cascade="all, delete-orphan")
    events: Mapped[list[ShipmentEvent]] = relationship(back_populates="shipment",
                                                       order_by="ShipmentEvent.occurred_at",
                                                       cascade="all, delete-orphan")
    order: Mapped[Order | None] = relationship()
    destination_location: Mapped[Location | None] = relationship()


class ShipmentLeg(Base):
    __tablename__ = "shipment_legs"

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    seq: Mapped[int] = mapped_column(Integer)
    carrier_id: Mapped[int | None] = mapped_column(ForeignKey("carriers.id"))
    carrier_name: Mapped[str] = mapped_column(String(160))
    agent_name: Mapped[str | None] = mapped_column(String(160))  # forwarder/broker/agent running this leg
    mode: Mapped[str | None] = mapped_column(String(20))
    tracking_ref: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="booked")
    departed_at: Mapped[datetime | None] = mapped_column(DateTime)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime)
    eta: Mapped[datetime | None] = mapped_column(DateTime)

    shipment: Mapped[Shipment] = relationship(back_populates="legs")
    carrier: Mapped[Carrier | None] = relationship()


class ShipmentEvent(Base):
    __tablename__ = "shipment_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    leg_id: Mapped[int | None] = mapped_column(ForeignKey("shipment_legs.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32))
    location_text: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40))  # carrier_api | email | whatsapp | manual | ...

    shipment: Mapped[Shipment] = relationship(back_populates="events")


# --- 3PL billing --------------------------------------------------------------------------

class RateCardEntry(Base):
    __tablename__ = "rate_card"
    __table_args__ = (UniqueConstraint("vendor", "charge_code", "effective_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(160))
    charge_code: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(60))  # per pallet/month, per order, per hour, ...
    rate: Mapped[Decimal] = mapped_column(Dec)
    min_charge: Mapped[Decimal | None] = mapped_column(Dec)
    # Charges that need a signed-off work order or backup before they are payable (projects, labor).
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class WarehouseActivity(Base):
    """What the warehouse actually did for us (receipts, picks, storage), from its WMS."""

    __tablename__ = "warehouse_activity"
    __table_args__ = (UniqueConstraint("vendor", "charge_code", "activity_date", "reference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(160))
    charge_code: Mapped[str] = mapped_column(String(60))
    activity_date: Mapped[date] = mapped_column(Date, index=True)
    qty: Mapped[Decimal] = mapped_column(Dec)
    reference: Mapped[str] = mapped_column(String(160), default="")
    source: Mapped[str] = mapped_column(String(40))


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("vendor", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(160))
    number: Mapped[str] = mapped_column(String(80))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    total: Mapped[Decimal] = mapped_column(Dec)
    status: Mapped[str] = mapped_column(String(20), default="received")  # received|audited|disputed|approved
    source: Mapped[str | None] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(300))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                    order_by="InvoiceLine.id")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    charge_code: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(Dec)
    rate: Mapped[Decimal] = mapped_column(Dec)
    amount: Mapped[Decimal] = mapped_column(Dec)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


# --- alerts, notifications, bookkeeping ---------------------------------------------------------

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identity of the condition, e.g. "deadline:order:12". Re-running rules updates instead of duplicating.
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str | None] = mapped_column(Text)
    # open | acked | dismissed (by a person; stays quiet unless it escalates) | resolved (condition cleared)
    status: Mapped[str] = mapped_column(String(12), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Severity at the last notification; a rise above it re-notifies.
    notified_severity: Mapped[str | None] = mapped_column(String(10))


class Notification(Base):
    """Outbox of every message sent (or, in demo mode, that would have been sent)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(40))
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12))  # sent | failed | demo
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProcessedDocument(Base):
    """Emails and files already read, so they are not parsed twice."""

    __tablename__ = "processed_documents"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # tracking_update | invoice | ignored | failed
    summary: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Receipt(Base):
    """Inbound stock: a 3PL receiver, a POS purchase order, an incoming transfer."""

    __tablename__ = "receipts"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20), default="receipt")  # receipt | purchase_order | transfer
    reference: Mapped[str | None] = mapped_column(String(160), index=True)  # PO / ASN / receiver number
    vendor: Mapped[str | None] = mapped_column(String(200))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    status: Mapped[str] = mapped_column(String(20), default="expected")  # expected | receiving | received | cancelled
    expected_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime)
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    units_expected: Mapped[int] = mapped_column(Integer, default=0)
    units_received: Mapped[int] = mapped_column(Integer, default=0)
    pallets: Mapped[int | None] = mapped_column(Integer)
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{"sku", "expected", "received"}]
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    location: Mapped[Location | None] = relationship()


class LaborShift(Base):
    """Clocked hours (POS time clock), for sales and units per labor hour."""

    __tablename__ = "labor_shifts"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(160))
    employee: Mapped[str] = mapped_column(String(160))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    clock_in: Mapped[datetime] = mapped_column(DateTime, index=True)
    clock_out: Mapped[datetime | None] = mapped_column(DateTime)

    location: Mapped[Location | None] = relationship()


class KeyValue(Base):
    """Small persistent state: rotated OAuth tokens, poll offsets, last run of daily jobs."""

    __tablename__ = "key_values"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InboxItem(Base):
    """Something another system or AI handed us to act on (Muse alerts, tasks, notes, files)."""

    __tablename__ = "inbox_items"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # alert | task | note | file
    severity: Mapped[str] = mapped_column(String(10), default="info")
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str | None] = mapped_column(Text)
    due: Mapped[date | None] = mapped_column(Date)
    subject_type: Mapped[str | None] = mapped_column(String(40))
    subject_id: Mapped[int | None] = mapped_column(Integer)
    file_name: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | done | superseded
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Forecast(Base):
    """Expected units per day for a SKU (optionally at one location), e.g. from Muse."""

    __tablename__ = "forecasts"
    __table_args__ = (UniqueConstraint("source", "product_id", "location_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    units_per_day: Mapped[Decimal] = mapped_column(Dec)
    as_of: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship()
    location: Mapped[Location | None] = relationship()


class HandoffFile(Base):
    """Files exchanged with other tools through shared folders (Muse)."""

    __tablename__ = "handoff_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    partner: Mapped[str] = mapped_column(String(40))
    direction: Mapped[str] = mapped_column(String(10))  # out | in
    name: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(400))  # Drive file id or local path
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    connector: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(10))  # live | demo
    status: Mapped[str] = mapped_column(String(12))  # ok | error
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
