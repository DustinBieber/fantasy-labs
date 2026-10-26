"""Editable settings stored in the DB, layered over config.DEFAULT_SETTINGS."""
from __future__ import annotations

import copy
import json
from typing import Any, Dict

from sqlalchemy import select

from . import db
from .config import DEFAULT_SETTINGS
from .timeutil import now_ts

_KEY = "settings"


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> Dict[str, Any]:
    row = db.fetch_one(select(db.app_settings.c.value).where(db.app_settings.c.key == _KEY))
    stored = json.loads(row["value"]) if row else {}
    return _deep_merge(DEFAULT_SETTINGS, stored)


def _validate(merged: Dict[str, Any]) -> None:
    def num(path, lo=None, hi=None):
        cur = merged
        for p in path:
            cur = cur[p]
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            raise ValueError(f"{'.'.join(path)} must be a number")
        if lo is not None and cur < lo:
            raise ValueError(f"{'.'.join(path)} must be >= {lo}")
        if hi is not None and cur > hi:
            raise ValueError(f"{'.'.join(path)} must be <= {hi}")

    num(["bankroll"], 0)
    num(["kelly_fraction"], 0, 1)
    num(["per_strike_cap_pct"], 0, 100)
    num(["correlated_cap_pct"], 0, 100)
    for k in ("s_min_edge_pts", "s_min_net_ev_pct", "g_min_edge_pts", "g_min_net_ev_pct", "w_min_edge_pts"):
        num(["tiers", k], 0, 100)
    num(["tiers", "g_size_multiplier"], 0, 1)
    num(["fees", "taker_rate"], 0, 1)
    num(["fees", "maker_rate"], 0, 1)
    num(["gas", "model_weight"], 0, 1)
    for k in ("watched_tickers", "news_feeds", "active_series"):
        if not isinstance(merged.get(k), list):
            raise ValueError(f"{k} must be a list")


def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    row = db.fetch_one(select(db.app_settings.c.value).where(db.app_settings.c.key == _KEY))
    stored = json.loads(row["value"]) if row else {}
    new_stored = _deep_merge(stored, patch)
    merged = _deep_merge(DEFAULT_SETTINGS, new_stored)
    _validate(merged)
    db.upsert(db.app_settings, ["key"], {"key": _KEY, "value": json.dumps(new_stored), "updated_ts": now_ts()})
    return merged


def reset_settings() -> Dict[str, Any]:
    db.upsert(db.app_settings, ["key"], {"key": _KEY, "value": "{}", "updated_ts": now_ts()})
    return get_settings()
