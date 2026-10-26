"""Connector registry. Importing this package registers every connector."""
from __future__ import annotations

from . import ai, extensiv, google, iqmetrix_rq, lightspeed, messaging, muse, shopify  # noqa: F401
from .base import REGISTRY, AIProvider, Connector, Notifier

# Master data first, so locations and products exist before stock and orders reference them.
SYNC_ORDER = ["google_sheets", "shopify", "extensiv", "lightspeed", "iqmetrix_rq", "gmail", "google_drive"]


def get_connector(key: str, env: dict[str, str] | None = None) -> Connector:
    if key not in REGISTRY:
        raise KeyError(f"Unknown connector: {key}")
    return REGISTRY[key](env)


def all_connectors(env: dict[str, str] | None = None) -> list[Connector]:
    return [cls(env) for cls in REGISTRY.values()]


def sync_keys() -> list[str]:
    pulling = [k for k, cls in REGISTRY.items() if cls(env={}).pulls]
    return [k for k in SYNC_ORDER if k in pulling] + [k for k in pulling if k not in SYNC_ORDER]


def notifiers(env: dict[str, str] | None = None) -> dict[str, Notifier]:
    return {k: cls(env) for k, cls in REGISTRY.items() if issubclass(cls, Notifier)}


def ai_providers(env: dict[str, str] | None = None) -> dict[str, AIProvider]:
    return {k: cls(env) for k, cls in REGISTRY.items() if issubclass(cls, AIProvider)}
