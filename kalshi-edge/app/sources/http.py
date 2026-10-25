"""Shared HTTP client: timeouts, retries with backoff, a simple rate limiter, and a pull log row
for every request so any number on screen can be traced to the call that produced it."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy import insert, update

from .. import config, db

log = logging.getLogger("kalshi_edge.http")

_SECRET_PARAMS = {"apikey", "api_key", "token", "key"}
_clients: Dict[str, httpx.Client] = {}
_client_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_call: Dict[str, float] = {}

# Minimum seconds between calls per source (Kalshi basic tier allows ~20 reads/sec).
MIN_INTERVAL = {"kalshi": 0.12, "espn": 0.5, "odds_api": 1.0, "aaa": 2.0, "polymarket": 0.25}


class FetchError(Exception):
    def __init__(self, msg: str, pull_id: Optional[int] = None, status: Optional[int] = None):
        super().__init__(msg)
        self.pull_id = pull_id
        self.status = status


def _client(transport: Optional[httpx.BaseTransport] = None) -> httpx.Client:
    key = "default" if transport is None else f"t{id(transport)}"
    with _client_lock:
        if key not in _clients:
            _clients[key] = httpx.Client(
                timeout=config.HTTP_TIMEOUT_S,
                headers={"User-Agent": "kalshi-edge/1.0 (personal read-only research)"},
                follow_redirects=True,
                transport=transport,
            )
        return _clients[key]


_transport_override: Optional[httpx.BaseTransport] = None


def set_transport(transport: Optional[httpx.BaseTransport]) -> None:
    """Tests inject an httpx.MockTransport here."""
    global _transport_override
    _transport_override = transport


def _safe_params(params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return ""
    clean = {k: ("***" if k.lower() in _SECRET_PARAMS else v) for k, v in params.items()}
    return db.dumps(clean)


def _throttle(source: str) -> None:
    gap = MIN_INTERVAL.get(source, 0.2)
    with _rate_lock:
        last = _last_call.get(source, 0.0)
        wait = last + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[source] = time.monotonic()


def log_pull(source: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> int:
    with db.get_engine().begin() as c:
        res = c.execute(insert(db.data_pulls).values(
            source=source, endpoint=endpoint, params=_safe_params(params), started_ts=time.time(), ok=False))
        return int(res.inserted_primary_key[0])


def finish_pull(pull_id: int, ok: bool, http_status: Optional[int] = None, rows: int = 0,
                error: Optional[str] = None, started: Optional[float] = None) -> None:
    vals: Dict[str, Any] = {"ok": ok, "http_status": http_status, "rows": rows, "error": (error or None)}
    if started is not None:
        vals["duration_ms"] = int((time.time() - started) * 1000)
    with db.get_engine().begin() as c:
        c.execute(update(db.data_pulls).where(db.data_pulls.c.id == pull_id).values(**vals))


def get(source: str, url: str, params: Optional[Dict[str, Any]] = None, expect: str = "json",
        retries: int = 3, headers: Optional[Dict[str, str]] = None) -> Tuple[Any, int, httpx.Response]:
    """GET with retries. Returns (parsed body, pull_id, response). Raises FetchError (already logged)."""
    endpoint = re.sub(r"apiKey=[^&]+", "apiKey=***", url)
    pull_id = log_pull(source, endpoint, params)
    started = time.time()
    last_err: Optional[str] = None
    status: Optional[int] = None
    client = _client(_transport_override)
    for attempt in range(retries):
        _throttle(source)
        try:
            resp = client.get(url, params=params, headers=headers)
            status = resp.status_code
            if status == 429 or status >= 500:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.5 * (2 ** attempt)
                last_err = f"HTTP {status}"
                time.sleep(min(delay, 10))
                continue
            if status >= 400:
                last_err = f"HTTP {status}: {resp.text[:200]}"
                break
            body = resp.json() if expect == "json" else resp.text
            finish_pull(pull_id, True, status, started=started)
            return body, pull_id, resp
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(1.5 * (2 ** attempt), 10))
        except ValueError as e:  # bad JSON
            last_err = f"Bad response body: {e}"
            break
    finish_pull(pull_id, False, status, error=last_err, started=started)
    log.warning("pull failed %s %s: %s", source, endpoint, last_err)
    raise FetchError(last_err or "request failed", pull_id, status)
