"""Connector framework.

A connector pulls records from one outside system and returns them as a
``SyncBatch`` of normalized records; ``services.ingest`` writes the batch to the
database. Normalizers take the vendor's own payload shape, so live API responses
and the built-in demo fixtures go through exactly the same mapping code.

Modes:
  live     - all ``settings_keys`` are set; ``pull_live`` calls the real API
  demo     - credentials missing and DEMO_MODE on; ``pull_demo`` serves sample payloads
  disabled - credentials missing and DEMO_MODE off
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

import httpx


class NotWiredYet(RuntimeError):
    """The live API call for this connector has not been built yet."""


# --- normalized records ------------------------------------------------------

@dataclass
class NLocation:
    code: str
    name: str
    kind: str = "unknown"
    external_id: str | None = None
    address: str | None = None
    source: str | None = None  # system whose stock counts for this location; defaults to the batch source


@dataclass
class NProduct:
    sku: str
    name: str
    upc: str | None = None
    category: str | None = None
    hazmat: bool | None = None
    hazmat_class: str | None = None
    un_number: str | None = None
    seasonal: bool | None = None
    season: str | None = None
    shelf_life_days: int | None = None
    unit_cost: Decimal | None = None
    external_id: str | None = None  # the source system's own item id, stored as a SkuAlias


@dataclass
class NOrderLine:
    sku: str | None
    qty: int
    qty_fulfilled: int = 0
    title: str | None = None


@dataclass
class NOrder:
    external_id: str
    number: str
    status: str
    raw_status: str | None = None
    channel: str | None = None
    customer_name: str | None = None
    placed_at: datetime | None = None
    updated_at: datetime | None = None
    notes: str | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
    lines: list[NOrderLine] = field(default_factory=list)
    reference: str | None = None  # channel order number, for warehouse records
    required_by: datetime | None = None  # a deadline the system itself stores (e.g. Extensiv cancel date)
    ship_from: str | None = None  # location ref (see SyncBatch)
    order_type: str | None = None  # when the source knows (e.g. wholesale)
    shipped_at: datetime | None = None


@dataclass
class NStock:
    sku: str
    location: str
    on_hand: int
    allocated: int = 0
    available: int | None = None


@dataclass
class NLot:
    sku: str
    location: str
    lot_number: str
    qty_on_hand: int
    best_by: date | None = None
    received_on: date | None = None


@dataclass
class NSale:
    external_id: str
    sku: str
    location: str
    sold_at: datetime
    qty: int
    net_amount: Decimal | None = None


@dataclass
class NShipment:
    reference: str
    direction: str
    mode: str
    carrier_name: str
    tracking_ref: str | None = None
    agent_name: str | None = None
    status: str = "booked"
    origin: str | None = None
    destination: str | None = None
    destination_location: str | None = None
    order_number: str | None = None
    eta: datetime | None = None
    hazmat: bool = False
    un_numbers: list[str] = field(default_factory=list)
    priority: bool = False
    notes: str | None = None
    booked_at: datetime | None = None


@dataclass
class NTrackingUpdate:
    """A status update for an existing shipment, possibly handing it to a new carrier/agent."""

    occurred_at: datetime
    status: str
    tracking_ref: str | None = None  # matches a leg's tracking ref...
    shipment_ref: str | None = None  # ...or the shipment's own reference
    location_text: str | None = None
    description: str | None = None
    eta: datetime | None = None
    # Handoff: the shipment moves to a new leg with this carrier/agent/tracking number.
    new_carrier: str | None = None
    new_agent: str | None = None
    new_tracking_ref: str | None = None
    new_mode: str | None = None
    source: str = "carrier_api"


@dataclass
class NActivity:
    vendor: str
    charge_code: str
    activity_date: date
    qty: Decimal
    reference: str = ""


@dataclass
class NInvoiceLine:
    description: str
    qty: Decimal
    rate: Decimal
    amount: Decimal
    charge_code: str | None = None


@dataclass
class NInvoice:
    vendor: str
    number: str
    period_start: date
    period_end: date
    total: Decimal
    lines: list[NInvoiceLine] = field(default_factory=list)
    invoice_date: date | None = None
    source_ref: str | None = None


@dataclass
class NRate:
    vendor: str
    charge_code: str
    description: str
    unit: str
    rate: Decimal
    effective_from: date
    effective_to: date | None = None
    min_charge: Decimal | None = None
    requires_approval: bool = False


@dataclass
class NCarrier:
    name: str
    aliases: list[str] = field(default_factory=list)
    hazmat_approved: bool = False
    tracking_url_template: str | None = None
    contact: str | None = None


@dataclass
class NStandingOrder:
    code: str
    customer_name: str
    cadence_days: int
    next_due: date
    lines: list[dict]
    min_days_to_best_by: int = 0
    ship_from: str | None = None
    notes: str | None = None
    active: bool = True


@dataclass
class NDocument:
    """An email or file for the document pipeline (tracking updates, invoices)."""

    external_id: str
    title: str
    text: str
    received_at: datetime | None = None
    sender: str | None = None
    attachments: list[tuple[str, str | bytes]] = field(default_factory=list)  # (filename, text or raw bytes)
    # What the document is known to be, when its source says so (e.g. "muse" for the Muse inbox folder).
    hint: str | None = None


@dataclass
class SyncBatch:
    """Everything one pull returned.

    Location refs (``NStock.location`` etc.) are the source system's own location id
    (Shopify location gid, Lightspeed shopID, ...). Ingest resolves them through the
    Locations master sheet (source + external_id), then by our location code.
    SKUs resolve through SkuAlias (source + external id), then by our SKU.
    """

    locations: list[NLocation] = field(default_factory=list)
    products: list[NProduct] = field(default_factory=list)
    orders: list[NOrder] = field(default_factory=list)
    stock: list[NStock] = field(default_factory=list)
    lots: list[NLot] = field(default_factory=list)
    sales: list[NSale] = field(default_factory=list)
    shipments: list[NShipment] = field(default_factory=list)
    tracking: list[NTrackingUpdate] = field(default_factory=list)
    activities: list[NActivity] = field(default_factory=list)
    invoices: list[NInvoice] = field(default_factory=list)
    rates: list[NRate] = field(default_factory=list)
    carriers: list[NCarrier] = field(default_factory=list)
    standing_orders: list[NStandingOrder] = field(default_factory=list)
    documents: list[NDocument] = field(default_factory=list)
    # When true, lots for (sku, location) pairs in this batch replace what is stored.
    lots_complete: bool = True


# --- connector base classes --------------------------------------------------------

CAPABILITIES = ("orders", "inventory", "lots", "sales", "shipments", "billing", "master_data",
                "documents", "notify", "calendar", "export", "ai")


class Connector:
    key: ClassVar[str]
    name: ClassVar[str]
    category: ClassVar[str]  # commerce | pos | warehouse | workspace | messaging | ai | other
    capabilities: ClassVar[tuple[str, ...]] = ()
    settings_keys: ClassVar[tuple[str, ...]] = ()
    docs_url: ClassVar[str | None] = None
    # One line on what the live integration does or still needs; shown on the Connectors page.
    status_note: ClassVar[str] = ""
    # Listed so it shows up, but nothing is built yet.
    planned: ClassVar[bool] = False

    # Tests swap in an httpx.MockTransport here.
    transport: ClassVar[httpx.BaseTransport | None] = None

    def __init__(self, env: dict[str, str] | None = None):
        if env is None:
            from ..settings_store import effective_env

            env = effective_env()
        self.env = env

    def http(self) -> httpx.Client:
        return httpx.Client(timeout=30, transport=type(self).transport)

    def setting(self, key: str, default: str | None = None) -> str | None:
        return self.env.get(key) or default

    def missing_settings(self) -> list[str]:
        return [k for k in self.settings_keys if not self.env.get(k)]

    def is_configured(self) -> bool:
        return bool(self.settings_keys) and not self.missing_settings()

    def files_ready(self) -> bool:
        """Set up to import export files (see connectors.files) while the API isn't connected."""
        return False

    def mode(self, demo_mode: bool) -> str:
        """live (API) | files (export-file imports) | demo (sample data) | disabled | planned"""
        if self.planned:
            return "planned"
        if self.is_configured():
            return "live"
        if self.files_ready():
            return "files"
        return "demo" if demo_mode else "disabled"

    @property
    def pulls(self) -> bool:
        """Whether this connector has anything to sync (notifiers and AI providers do not)."""
        return type(self).pull_demo is not Connector.pull_demo or type(self).pull_live is not Connector.pull_live

    def pull(self, demo_mode: bool, since: datetime | None = None) -> SyncBatch:
        mode = self.mode(demo_mode)
        if mode == "live":
            return self.pull_live(since)
        if mode == "files":
            return self.pull_files(since)
        if mode == "demo":
            return self.pull_demo()
        raise RuntimeError(f"{self.name} is not configured; set {', '.join(self.settings_keys)}")

    def pull_live(self, since: datetime | None) -> SyncBatch:
        raise NotWiredYet(f"{self.name}: live sync is not built yet. {self.status_note}")

    def pull_demo(self) -> SyncBatch:
        return SyncBatch()

    def pull_files(self, since: datetime | None) -> SyncBatch:
        return SyncBatch()

    def test(self) -> str:
        """Make a small real call and say what it found; raise on failure."""
        raise NotImplementedError("No connection test for this connector")

    @property
    def imports(self) -> bool:
        return False

    def describe(self, demo_mode: bool) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "capabilities": list(self.capabilities),
            "settings_keys": list(self.settings_keys),
            "missing_settings": self.missing_settings(),
            "mode": self.mode(demo_mode),
            "pulls": self.pulls,
            "imports": self.imports,
            "testable": type(self).test is not Connector.test,
            "docs_url": self.docs_url,
            "status_note": self.status_note,
        }


class Notifier(Connector):
    """A channel alerts can be sent to."""

    capabilities = ("notify",)

    def send(self, text: str) -> None:
        raise NotImplementedError

    def send_ready(self) -> bool:
        """Configured enough to send (a connector may be connected for reading but have no recipient)."""
        return self.is_configured()


class AIProvider(Connector):
    """A large language model used to read free text (order notes, carrier emails, invoices)."""

    category = "ai"
    capabilities = ("ai",)

    def complete(self, system: str, prompt: str) -> str:
        raise NotImplementedError

    def test(self) -> str:
        reply = self.complete("Reply with JSON only.", 'Return {"ok": true}.')
        return f"Model replied: {reply.strip()[:60]}"


REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    REGISTRY[cls.key] = cls
    return cls
