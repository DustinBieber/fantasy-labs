"""SQLite storage. One connection per thread; the web server and worker share a process."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  channel_id TEXT PRIMARY KEY,
  handle TEXT, title TEXT, url TEXT,
  subscribers INTEGER, long_videos INTEGER, shorts INTEGER,
  avg_minutes REAL, last_upload TEXT,
  tiktok TEXT, tiktok_videos INTEGER, instagram TEXT, instagram_posts INTEGER,
  links TEXT, score REAL, reasons TEXT,
  status TEXT NOT NULL DEFAULT 'new',          -- new | qualified | pitched | client | passed
  pitch TEXT, found_via TEXT,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY,                          -- YouTube video id
  client TEXT NOT NULL, show TEXT NOT NULL,
  title TEXT, url TEXT, duration REAL,
  status TEXT NOT NULL DEFAULT 'new',           -- new | picked | rendered | done | skipped | failed
  transcript_source TEXT, source_path TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS videos_status ON videos(status);

CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id TEXT NOT NULL, client TEXT NOT NULL, show TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'single',          -- single | montage
  segments TEXT NOT NULL,                       -- JSON [[start, end], ...] in source seconds
  hook TEXT, title TEXT, caption TEXT, hashtags TEXT,
  category TEXT, score REAL, reason TEXT, picker TEXT,
  status TEXT NOT NULL DEFAULT 'pending',       -- pending | ready | approved | rejected | failed
  file_path TEXT, error TEXT,
  created_at TEXT, updated_at TEXT, reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS clips_status ON clips(status);

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id INTEGER NOT NULL, client TEXT NOT NULL, show TEXT NOT NULL, platform TEXT NOT NULL,
  scheduled_for TEXT NOT NULL,                  -- UTC ISO timestamp
  status TEXT NOT NULL DEFAULT 'queued',        -- queued | posting | posted | failed | canceled
  attempts INTEGER NOT NULL DEFAULT 0,
  remote_id TEXT, remote_url TEXT, error TEXT,
  created_at TEXT, updated_at TEXT,
  UNIQUE (clip_id, platform)
);
CREATE INDEX IF NOT EXISTS posts_due ON posts(status, scheduled_for);

CREATE TABLE IF NOT EXISTS accounts (
  client TEXT NOT NULL, show TEXT NOT NULL, platform TEXT NOT NULL,
  external_id TEXT, display_name TEXT,
  token TEXT NOT NULL,                          -- encrypted JSON
  expires_at TEXT, connected_at TEXT, refreshed_at TEXT,
  PRIMARY KEY (client, show, platform)
);

CREATE TABLE IF NOT EXISTS connect_links (
  token TEXT PRIMARY KEY, client TEXT NOT NULL, created_at TEXT
);

CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY, client TEXT, show TEXT, platform TEXT, link TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, model TEXT, purpose TEXT, ref TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, level TEXT, kind TEXT, message TEXT
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._local = threading.local()
        self.conn.executescript(SCHEMA)

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def tx(self):
        return _Tx(self.conn)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.one(sql, params)
        return row[0] if row else None

    def insert(self, table: str, row: dict[str, Any], *, or_ignore: bool = False) -> int:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
        cur = self.conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
        return cur.lastrowid

    def upsert(self, table: str, row: dict[str, Any], key: Iterable[str]) -> None:
        key = list(key)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        updates = ", ".join(f"{k}=excluded.{k}" for k in row if k not in key)
        self.conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks}) "
            f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}",
            tuple(row.values()),
        )

    def update(self, table: str, where: dict[str, Any], **fields: Any) -> None:
        if table in _TIMESTAMPED:
            fields.setdefault("updated_at", now_iso())
        sets = ", ".join(f"{k}=?" for k in fields)
        conds = " AND ".join(f"{k}=?" for k in where)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE {conds}", (*fields.values(), *where.values()))

    # --- small conveniences -------------------------------------------------
    def log(self, kind: str, message: str, level: str = "info") -> None:
        self.insert("events", {"ts": now_iso(), "level": level, "kind": kind, "message": message[:2000]})

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        v = self.scalar("SELECT value FROM kv WHERE key=?", (key,))
        return default if v is None else v

    def set_kv(self, key: str, value: str) -> None:
        self.upsert("kv", {"key": key, "value": value}, ["key"])


_TIMESTAMPED = {"leads", "videos", "clips", "posts"}


class _Tx:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


def dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"))


def loads(v: str | None, default: Any = None) -> Any:
    return json.loads(v) if v else default
