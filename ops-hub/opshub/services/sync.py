"""Run connectors, then rules and notifications."""
from __future__ import annotations

import logging
import threading
import traceback

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..connectors import get_connector, sync_keys
from ..connectors.base import NotWiredYet
from ..models import SyncRun
from ..timeutil import utcnow
from . import billing, ingest, notify, orders, rules

log = logging.getLogger(__name__)
# One sync at a time per process: the scheduler thread and a dashboard click must not ingest together.
SYNC_LOCK = threading.RLock()


def run_connector(session: Session, key: str, settings: Settings, env: dict[str, str] | None = None) -> SyncRun:
    with SYNC_LOCK:
        return _run_connector(session, key, settings, env)


def _run_connector(session: Session, key: str, settings: Settings, env: dict[str, str] | None) -> SyncRun:
    conn = get_connector(key, env)
    mode = conn.mode(settings.demo_mode)
    run = SyncRun(connector=key, mode=mode, status="running", started_at=utcnow())
    if not conn.pulls:
        run.status, run.error = "skipped", "Nothing to pull (send-only connector)"
    elif mode in ("disabled", "planned"):
        run.status, run.error = "skipped", f"Not configured: set {', '.join(conn.settings_keys)}"
    else:
        last_ok = session.scalar(select(SyncRun).where(SyncRun.connector == key, SyncRun.status == "ok",
                                                       SyncRun.mode == mode).order_by(SyncRun.id.desc()))
        try:
            batch = conn.pull(settings.demo_mode, since=last_ok.started_at if last_ok else None)
            stats = ingest.apply_batch(session, batch, key, settings,
                                       authoritative="master_data" in conn.capabilities, env=env)
            if key == "extensiv" and mode == "live":
                codes = conn.codes
                if codes.get("hazmat_order"):
                    session.flush()
                    stats["hazmat_activity"] = billing.derive_hazmat_activity(session, conn.vendor,
                                                                              codes["hazmat_order"])
            stats["orders_linked"] = orders.link_orders(session)
            session.flush()
            stats.update({f"signals_{k}": v for k, v in orders.refresh_signals(session, settings, env=env).items()})
            run.status, run.stats = "ok", stats
        except NotWiredYet as exc:
            session.rollback()
            run.status, run.error = "not_wired", str(exc)
        except Exception as exc:
            session.rollback()
            log.exception("Sync of %s failed", key)
            run.status, run.error = "error", f"{exc}\n{traceback.format_exc(limit=3)}"[:2000]
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
    session.expire_all()
    return run


def run_rules(session: Session, settings: Settings, env: dict[str, str] | None = None) -> dict:
    with SYNC_LOCK:
        result = {"rules": rules.run(session, settings), "notifications": notify.dispatch(session, settings, env)}
        session.commit()
        return result


def sync_all(session: Session, settings: Settings, env: dict[str, str] | None = None) -> dict:
    with SYNC_LOCK:
        runs = [run_connector(session, key, settings, env) for key in sync_keys()]
        result = run_rules(session, settings, env)
    result["runs"] = [{"connector": r.connector, "mode": r.mode, "status": r.status, "stats": r.stats,
                       "error": r.error} for r in runs]
    return result
