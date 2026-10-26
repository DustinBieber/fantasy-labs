"""Nightly copies of the SQLite database (consistent even while Ops Hub is running)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import get_settings
from ..settings_store import effective_env
from ..timeutil import local_date, utcnow


def backup(dest_dir: str | None = None, keep: int | None = None) -> dict:
    s = get_settings()
    url = s.database_url
    if not url.startswith("sqlite:///") or url.endswith(":memory:"):
        return {"skipped": "only SQLite files are copied here; use your database's own backups"}
    env = effective_env()
    keep = keep or int(env.get("BACKUP_KEEP") or 14)
    dest = Path(dest_dir or env.get("BACKUP_DIR") or "backups")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"opshub-{local_date(utcnow(), s.timezone).isoformat()}.db"
    src = sqlite3.connect(url[len("sqlite:///"):])
    out = sqlite3.connect(target)
    try:
        src.backup(out)  # SQLite's online backup: safe while the app is writing
        out.execute("PRAGMA journal_mode=DELETE")  # one self-contained file, no -wal/-shm beside it
    finally:
        out.close()
        src.close()
    copies = sorted(dest.glob("opshub-*.db"))
    for old in copies[:-keep]:
        old.unlink()
    return {"file": str(target), "copies_kept": min(len(copies), keep)}
