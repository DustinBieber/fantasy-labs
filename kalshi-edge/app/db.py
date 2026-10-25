"""Database schema (SQLAlchemy Core) and small helpers.

SQLite for Phase 1. All timestamps are integer epoch seconds (UTC) so the schema
moves to Postgres by changing DATABASE_URL only.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, Float, Index, Integer, MetaData, String, Table, Text,
    UniqueConstraint, create_engine, event, insert, select, update,
)
from sqlalchemy.engine import Engine

from . import config

metadata = MetaData()

data_pulls = Table(
    "data_pulls", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(40), nullable=False, index=True),
    Column("endpoint", Text, nullable=False),
    Column("params", Text),
    Column("started_ts", Float, nullable=False, index=True),
    Column("duration_ms", Integer),
    Column("http_status", Integer),
    Column("ok", Boolean, nullable=False, default=False),
    Column("rows", Integer, default=0),
    Column("error", Text),
)

series = Table(
    "series", metadata,
    Column("series_ticker", String(80), primary_key=True),
    Column("title", Text),
    Column("category", String(80)),
    Column("frequency", String(40)),
    Column("fee_type", String(60)),
    Column("fee_multiplier", Float),
    Column("settlement_sources", Text),
    Column("updated_ts", BigInteger),
    Column("pull_id", Integer),
)

events = Table(
    "events", metadata,
    Column("event_ticker", String(120), primary_key=True),
    Column("series_ticker", String(80), index=True),
    Column("title", Text),
    Column("sub_title", Text),
    Column("kalshi_category", String(80)),
    Column("strike_date", BigInteger),
    Column("mutually_exclusive", Boolean),
    Column("fee_type_override", String(60)),
    Column("fee_multiplier_override", Float),
    Column("updated_ts", BigInteger),
)

markets = Table(
    "markets", metadata,
    Column("ticker", String(160), primary_key=True),
    Column("event_ticker", String(120), index=True),
    Column("series_ticker", String(80), index=True),
    Column("category", String(40), index=True),
    Column("kalshi_category", String(80)),
    Column("is_prop", Boolean, default=False),
    Column("title", Text),
    Column("subtitle", Text),
    Column("yes_sub_title", Text),
    Column("no_sub_title", Text),
    Column("event_title", Text),
    Column("strike_type", String(40)),
    Column("floor_strike", Float),
    Column("cap_strike", Float),
    Column("open_ts", BigInteger),
    Column("close_ts", BigInteger, index=True),
    Column("expiration_ts", BigInteger),
    Column("status", String(30), index=True),
    Column("result", String(20)),
    Column("expiration_value", String(60)),
    Column("yes_bid", Float),
    Column("yes_ask", Float),
    Column("no_bid", Float),
    Column("no_ask", Float),
    Column("last_price", Float),
    Column("volume", Float),
    Column("volume_24h", Float),
    Column("open_interest", Float),
    Column("liquidity", Float),
    Column("tick_size", Float),
    Column("price_ranges", Text),  # JSON [[start, end, step], ...] from Kalshi's price grid
    Column("settlement_value", Float),  # YES settlement value in dollars (1, 0, or e.g. 0.5 on a tie)
    Column("rules_primary", Text),
    Column("updated_ts", BigInteger),
    Column("pull_id", Integer),
)

market_snapshots = Table(
    "market_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ticker", String(160), nullable=False),
    Column("ts", BigInteger, nullable=False),
    Column("yes_bid", Float),
    Column("yes_ask", Float),
    Column("no_bid", Float),
    Column("no_ask", Float),
    Column("last_price", Float),
    Column("volume", Float),
    Column("open_interest", Float),
    Column("status", String(30)),
    Column("pull_id", Integer),
    Column("kind", String(10)),  # "minute" (active set) or "scan" (change-only full scan)
)
Index("ix_snap_ticker_ts", market_snapshots.c.ticker, market_snapshots.c.ts)

odds_lines = Table(
    "odds_lines", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sport", String(20), nullable=False),
    Column("source", String(20), nullable=False),  # odds_api | espn | manual
    Column("book", String(60), nullable=False),
    Column("home_team", String(8), nullable=False),  # canonical team code
    Column("away_team", String(8), nullable=False),
    Column("commence_ts", BigInteger),
    Column("home_price", Integer, nullable=False),  # American odds
    Column("away_price", Integer, nullable=False),
    Column("ts", BigInteger, nullable=False, index=True),
    Column("pull_id", Integer),
    Column("note", Text),
)
Index("ix_odds_game", odds_lines.c.sport, odds_lines.c.home_team, odds_lines.c.away_team, odds_lines.c.ts)

aaa_prices = Table(
    "aaa_prices", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", String(10), nullable=False),  # YYYY-MM-DD (date AAA published the average)
    Column("value", Float, nullable=False),
    Column("source", String(30), nullable=False),  # aaa_site | kalshi_settlement | manual
    Column("ts", BigInteger, nullable=False),
    Column("pull_id", Integer),
    Column("note", Text),
    UniqueConstraint("date", "source", name="uq_aaa_date_source"),
)

ladder_marks = Table(
    "ladder_marks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_ticker", String(120), nullable=False),
    Column("series_ticker", String(80)),
    Column("resolution_date", String(10)),
    Column("as_of_ts", BigInteger, nullable=False),
    Column("source", String(20), nullable=False),  # candles | snapshots
    Column("implied_median", Float),
    Column("model_median", Float),
    Column("actual", Float),
    Column("ladder_json", Text),
    UniqueConstraint("event_ticker", "source", name="uq_mark_event_source"),
)

edge_history = Table(
    "edge_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", BigInteger, nullable=False, index=True),
    Column("ticker", String(160), nullable=False, index=True),
    Column("side", String(3), nullable=False),
    Column("module", String(20)),
    Column("fair", Float),
    Column("maker_price", Float),
    Column("taker_price", Float),
    Column("raw_edge_pts", Float),
    Column("net_ev_pct", Float),
    Column("tier", String(4)),
    Column("fair_source", String(40)),
)

experts = Table(
    "experts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("sport", String(20), nullable=False),
    Column("proof_url", Text),
    Column("proof_type", String(20)),  # published | independent | none
    Column("proof_picks", Integer, default=0),
    Column("proof_full_season", Boolean, default=False),
    Column("proof_units", Float, default=0.0),
    Column("notes", Text),
    Column("active", Boolean, default=True),
    Column("created_ts", BigInteger),
    Column("source", String(20), default="manual"),  # manual | polymarket
    Column("wallet", String(64), index=True),
    Column("stats", Text),  # JSON grading detail for wallets graded by the app
    Column("graded_ts", BigInteger),
)

expert_picks = Table(
    "expert_picks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("expert_id", Integer, nullable=False, index=True),
    Column("ticker", String(160), nullable=False, index=True),
    Column("side", String(3), nullable=False),
    Column("price", Float),
    Column("picked_ts", BigInteger),
    Column("source_url", Text),
    Column("result", String(10)),  # win | loss | push | None
    Column("units", Float),
    Column("status", String(10), default="open"),  # open | closed (position exited before settlement)
    Column("size_usd", Float),
    Column("first_seen_ts", BigInteger),
    Column("last_seen_ts", BigInteger),
    Column("external_ref", Text),  # e.g. Polymarket event slug + outcome
    Column("label", Text),
)

trades = Table(
    "trades", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("mode", String(6), nullable=False),  # paper | real
    Column("ticker", String(160), nullable=False, index=True),
    Column("event_ticker", String(120)),
    Column("series_ticker", String(80)),
    Column("category", String(40)),
    Column("title", Text),
    Column("side", String(3), nullable=False),  # yes | no
    Column("order_type", String(6), default="maker"),  # maker | taker
    Column("limit_price", Float, nullable=False),
    Column("contracts", Integer, nullable=False),
    Column("fees", Float, nullable=False, default=0.0),
    Column("status", String(10), nullable=False),  # resting | open | closed | settled | cancelled
    Column("tier", String(4)),
    Column("reason", Text),
    Column("expert_source", Text),
    Column("news_link", Text),
    Column("fair_at_entry", Float),
    Column("edge_at_entry", Float),
    Column("created_ts", BigInteger, nullable=False),
    Column("filled_ts", BigInteger),
    Column("closed_ts", BigInteger),
    Column("exit_price", Float),
    Column("exit_fees", Float),
    Column("settlement_result", String(10)),
    Column("realized_pnl", Float),
    Column("closing_price", Float),
    Column("clv_pts", Float),
    Column("fill_note", Text),
    Column("combo_legs", Text),  # JSON [{ticker, side, label}] for parlays (Kalshi Combos)
)

job_runs = Table(
    "job_runs", metadata,
    Column("name", String(60), primary_key=True),
    Column("last_start_ts", BigInteger),
    Column("last_ok_ts", BigInteger),
    Column("last_error", Text),
    Column("last_error_ts", BigInteger),
    Column("last_rows", Integer),
    Column("last_duration_ms", Integer),
    Column("detail", Text),
)

app_settings = Table(
    "app_settings", metadata,
    Column("key", String(80), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_ts", BigInteger),
)

_engine: Optional[Engine] = None
_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = make_engine(config.DATABASE_URL)
    return _engine


def make_engine(url: str) -> Engine:
    kwargs: Dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
    metadata.create_all(eng)
    return eng


def set_engine(eng: Engine) -> None:
    """Used by tests to point the app at a temporary database."""
    global _engine
    with _lock:
        _engine = eng


def rows(result) -> List[Dict[str, Any]]:
    return [dict(r._mapping) for r in result]


def fetch_all(stmt) -> List[Dict[str, Any]]:
    with get_engine().connect() as conn:
        return rows(conn.execute(stmt))


def fetch_one(stmt) -> Optional[Dict[str, Any]]:
    with get_engine().connect() as conn:
        r = conn.execute(stmt).first()
        return dict(r._mapping) if r else None


def upsert(table: Table, key_cols: Iterable[str], values: Dict[str, Any], conn=None) -> None:
    """Portable upsert (select then insert/update) that works on SQLite and Postgres."""
    key_cols = list(key_cols)

    def _do(c):
        cond = [table.c[k] == values[k] for k in key_cols]
        existing = c.execute(select(*[table.c[k] for k in key_cols]).where(*cond)).first()
        if existing:
            c.execute(update(table).where(*cond).values(**values))
        else:
            c.execute(insert(table).values(**values))

    if conn is not None:
        _do(conn)
    else:
        with get_engine().begin() as c:
            _do(c)


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)
