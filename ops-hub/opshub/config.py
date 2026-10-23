"""Runtime settings, read from environment variables and an optional .env file.

Connector credentials are read by each connector directly (see the connector's
``settings_keys``); this module only holds app-wide behaviour knobs.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

# Deadline phrases ("need by", "no later than") are read by the deadline parser, not flagged here.
DEFAULT_NOTE_KEYWORDS = [
    "urgent", "rush", "asap", "priority", "do not", "don't", "fragile",
    "hazmat", "call before", "appointment", "liftgate", "gift", "vip", "cancel",
    "hold", "address change", "change address", "replacement", "damaged",
    "best by", "fresh", "short dated",
]

# Shopify note attributes / custom attributes that carry a customer deadline.
DEFAULT_DEADLINE_KEYS = [
    "need by", "needed by", "deadline", "delivery date", "deliver by", "ship by",
    "event date", "in hands date", "in-hands date",
]

DEFAULT_ALERT_ROUTES = {
    "urgent": ["google_chat", "telegram", "whatsapp"],
    "warn": ["google_chat"],
    "info": [],
}


def load_dotenv(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines into os.environ. Variables already set win."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in {'"', "'"} and value[0] in value[1:]:
            value = value[1:value.index(value[0], 1)]
        else:
            value = "" if value.startswith("#") else re.split(r"\s+#", value, maxsplit=1)[0].strip()  # inline comments
        os.environ.setdefault(key.strip(), value)


def _list(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return list(default)
    return [v.strip() for v in value.split(",") if v.strip()]


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    database_url: str = "sqlite:///opshub.db"
    # Connectors without credentials serve built-in sample data instead of being skipped.
    demo_mode: bool = True
    # Business timezone: "need by Nov 3" means end of Nov 3 here; daily jobs run on this clock.
    timezone: str = "Pacific/Honolulu"
    # Background work while the server runs: sync every N minutes (0 = off) and daily jobs at HH:MM.
    scheduler: bool = True
    sync_interval_minutes: int = 15
    digest_time: str = "07:00"
    muse_export_time: str = "06:00"

    deadline_warn_hours: int = 72
    deadline_urgent_hours: int = 24
    order_stale_days: int = 3
    priority_unallocated_hours: int = 12
    shipment_stale_days: int = 4
    expiry_warn_days: int = 45
    low_stock_days: int = 14
    sell_through_days: int = 30
    standing_order_lookahead_days: int = 10
    # fefo = earliest best-by first (FIFO on receipt date breaks ties); fifo = receipt date only.
    lot_policy: str = "fefo"
    billing_tolerance: Decimal = Decimal("0.01")

    note_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_NOTE_KEYWORDS))
    deadline_keys: list[str] = field(default_factory=lambda: list(DEFAULT_DEADLINE_KEYS))
    ai_providers: list[str] = field(default_factory=lambda: ["claude", "gemini", "openai"])
    alert_routes: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_ALERT_ROUTES))

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        d = cls()

        def i(name: str, default: int) -> int:
            return int(e.get(name) or default)

        routes = d.alert_routes
        if e.get("ALERT_ROUTES"):
            routes = json.loads(e["ALERT_ROUTES"])
        return cls(
            database_url=e.get("DATABASE_URL") or d.database_url,
            demo_mode=_bool(e.get("DEMO_MODE"), d.demo_mode),
            timezone=e.get("BUSINESS_TIMEZONE") or d.timezone,
            scheduler=_bool(e.get("OPSHUB_SCHEDULER"), d.scheduler),
            sync_interval_minutes=i("SYNC_INTERVAL_MINUTES", d.sync_interval_minutes),
            digest_time=e.get("DIGEST_TIME") or d.digest_time,
            muse_export_time=e.get("MUSE_EXPORT_TIME") or d.muse_export_time,
            deadline_warn_hours=i("DEADLINE_WARN_HOURS", d.deadline_warn_hours),
            deadline_urgent_hours=i("DEADLINE_URGENT_HOURS", d.deadline_urgent_hours),
            order_stale_days=i("ORDER_STALE_DAYS", d.order_stale_days),
            priority_unallocated_hours=i("PRIORITY_UNALLOCATED_HOURS", d.priority_unallocated_hours),
            shipment_stale_days=i("SHIPMENT_STALE_DAYS", d.shipment_stale_days),
            expiry_warn_days=i("EXPIRY_WARN_DAYS", d.expiry_warn_days),
            low_stock_days=i("LOW_STOCK_DAYS", d.low_stock_days),
            sell_through_days=i("SELL_THROUGH_DAYS", d.sell_through_days),
            standing_order_lookahead_days=i("STANDING_ORDER_LOOKAHEAD_DAYS", d.standing_order_lookahead_days),
            lot_policy=(e.get("LOT_POLICY") or d.lot_policy).lower(),
            billing_tolerance=Decimal(e.get("BILLING_TOLERANCE") or d.billing_tolerance),
            note_keywords=_list(e.get("NOTE_KEYWORDS"), d.note_keywords),
            deadline_keys=_list(e.get("DEADLINE_KEYS"), d.deadline_keys),
            ai_providers=_list(e.get("AI_PROVIDERS"), d.ai_providers),
            alert_routes=routes,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        load_dotenv()
        _settings = Settings.from_env()
    return _settings


def set_settings(settings: Settings | None) -> None:
    """Override the cached settings (tests) or clear them (None)."""
    global _settings
    _settings = settings
