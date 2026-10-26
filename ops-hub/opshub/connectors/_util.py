"""Small parsing helpers shared by normalizers."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def as_list(value) -> list:
    """Wrap a lone object in a list. XML-derived APIs (Lightspeed) return one child as an object."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def to_int(value, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def to_dec(value, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return default


def truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "x"}


def slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", text.upper()).strip("-")


def rows_to_dicts(values: list[list[str]]) -> list[dict[str, str]]:
    """Sheets ``values`` (header row first) to dicts keyed by lower-case header."""
    if not values:
        return []
    header = [h.strip().lower() for h in values[0]]
    out = []
    for row in values[1:]:
        if not any(str(c).strip() for c in row):
            continue
        padded = list(row) + [""] * (len(header) - len(row))
        out.append({h: str(v).strip() for h, v in zip(header, padded)})
    return out
