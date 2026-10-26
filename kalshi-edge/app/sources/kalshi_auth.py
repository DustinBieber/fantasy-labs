"""Authenticated Kalshi client for the AI trader: balance, orders, fills, place and cancel.

Signing (Kalshi docs, "Authenticated Requests"): three headers per request,
  KALSHI-ACCESS-KEY        your API key id
  KALSHI-ACCESS-TIMESTAMP  milliseconds since epoch
  KALSHI-ACCESS-SIGNATURE  base64(sign(timestamp + METHOD + path)), path from the API root, no query string
RSA keys sign with RSA-PSS / SHA-256 (salt = digest length); Ed25519 keys sign directly.

Orders use the V2 single-book shape (POST /portfolio/events/orders): side "bid" buys YES at `price`;
side "ask" sells YES at `price`, which for someone holding no YES is buying NO at 1 - price. That mapping
is confirmed on your account by the live check before live mode can be turned on.

The private key never leaves this Mac and is never logged.
"""
from __future__ import annotations

import base64
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from .. import config
from .http import request

SOURCE = "kalshi_auth"


def configured() -> bool:
    return bool(config.KALSHI_API_KEY_ID and config.KALSHI_PRIVATE_KEY_PATH
                and Path(config.KALSHI_PRIVATE_KEY_PATH).expanduser().exists())


def key_file_exposed() -> bool:
    """True if the private key file is readable by anyone but you (it should be chmod 600)."""
    try:
        return bool(os.stat(Path(config.KALSHI_PRIVATE_KEY_PATH).expanduser()).st_mode & 0o077)
    except OSError:
        return False


_key_cache: Dict[str, Any] = {}


def _private_key():
    path = str(Path(config.KALSHI_PRIVATE_KEY_PATH).expanduser())
    if path not in _key_cache:
        with open(path, "rb") as f:
            _key_cache[path] = serialization.load_pem_private_key(f.read(), password=None)
    return _key_cache[path]


def sign(private_key, message: str) -> str:
    data = message.encode()
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        sig = private_key.sign(data)
    elif isinstance(private_key, rsa.RSAPrivateKey):
        sig = private_key.sign(data, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                               hashes.SHA256())
    else:
        raise ValueError("Unsupported key type: use the RSA or Ed25519 key Kalshi gave you")
    return base64.b64encode(sig).decode()


def auth_headers(method: str, url: str, key_id: Optional[str] = None, private_key=None,
                 now_ms: Optional[int] = None) -> Dict[str, str]:
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    path = urlparse(url).path
    return {
        "KALSHI-ACCESS-KEY": key_id or config.KALSHI_API_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sign(private_key or _private_key(), ts + method.upper() + path),
    }


def _call(method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None,
          idempotent: bool = True) -> Any:
    if not configured():
        raise RuntimeError("Kalshi API key not configured: set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env")
    url = f"{config.KALSHI_API_BASE}{path}"
    data, _, _ = request(SOURCE, method, url, params=params, json_body=body,
                         headers=auth_headers(method, url), idempotent=idempotent)
    return data


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dollars(d: Dict[str, Any], name: str) -> Optional[float]:
    """`name_dollars` (string dollars) or `name` (integer cents)."""
    v = _num(d.get(f"{name}_dollars"))
    if v is None and _num(d.get(name)) is not None:
        v = _num(d.get(name)) / 100.0
    return v


def balance() -> Dict[str, Any]:
    b = _call("GET", "/portfolio/balance")
    return {"balance": _dollars(b, "balance"), "portfolio_value": _dollars(b, "portfolio_value"), "raw": b}


def normalize_order(o: Dict[str, Any]) -> Dict[str, Any]:
    """One shape for legacy (side yes/no + action) and V2 (side bid/ask + price) order objects."""
    side, action = (o.get("side") or "").lower(), (o.get("action") or "").lower()
    price = _num(o.get("price")) if o.get("price") not in (None, "") else None
    if side in ("bid", "ask"):
        if side == "bid":
            side, action = "yes", "buy"
        else:
            side, action = "no", "buy"
            price = round(1 - price, 4) if price is not None else None
    if price is None:
        price = _dollars(o, "yes_price") if side == "yes" else _dollars(o, "no_price")
    if price is not None and price > 1:
        price = price / 100.0
    remaining = _num(o.get("remaining_count_fp"))
    if remaining is None:
        remaining = _num(o.get("remaining_count"))
    if remaining is None and _num(o.get("remaining_centicount")) is not None:
        remaining = _num(o.get("remaining_centicount")) / 100.0
    return {"order_id": o.get("order_id") or o.get("id"), "client_order_id": o.get("client_order_id"),
            "ticker": o.get("ticker"), "side": side, "action": action, "price": price,
            "remaining": remaining, "status": (o.get("status") or "").lower(), "raw": o}


def orders(status: str = "resting", ticker: Optional[str] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"status": status, "limit": 200}
    if ticker:
        params["ticker"] = ticker
    body = _call("GET", "/portfolio/orders", params)
    return [normalize_order(o) for o in (body.get("orders") or [])]


def _fee(f: Dict[str, Any]) -> float:
    """Fill fee in dollars. Kalshi sends `fee_cost` as a fixed-point dollar string ("0.0700"); an integer
    there is legacy cents."""
    v = _num(f.get("fee_cost_dollars"))
    if v is not None:
        return v
    raw = f.get("fee_cost")
    if isinstance(raw, str) and "." in raw:
        return _num(raw) or 0.0
    if _num(raw) is not None:
        return _num(raw) / 100.0
    return _num(f.get("fee")) or 0.0


def fills(min_ts: Optional[int] = None) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"limit": 200}
    if min_ts:
        params["min_ts"] = int(min_ts)
    body = _call("GET", "/portfolio/fills", params)
    out = []
    for f in body.get("fills") or []:
        side = (f.get("side") or "").lower()
        count = _num(f.get("count_fp"))
        if count is None:
            count = _num(f.get("count"))
        out.append({"order_id": f.get("order_id"), "ticker": f.get("ticker"), "side": side,
                    "action": (f.get("action") or "").lower(), "count": count or 0.0,
                    "price": _dollars(f, "yes_price") if side == "yes" else _dollars(f, "no_price"),
                    "fee": _fee(f), "raw": f})
    return out


def order_body(ticker: str, side: str, price: float, count: int, client_order_id: str,
               post_only: bool = True) -> Dict[str, Any]:
    """V2 body for buying `side` (yes/no) at `price` dollars."""
    if side not in ("yes", "no"):
        raise ValueError("side must be yes or no")
    if not 0 < price < 1 or count <= 0:
        raise ValueError("price must be between 0 and 1 and count positive")
    book_side, book_price = ("bid", price) if side == "yes" else ("ask", round(1 - price, 4))
    return {"ticker": ticker, "client_order_id": client_order_id, "side": book_side, "count": f"{int(count)}.00",
            "price": f"{book_price:.4f}", "time_in_force": "good_till_canceled", "post_only": post_only,
            "reduce_only": False, "self_trade_prevention_type": "cancel_resting"}


def new_client_order_id() -> str:
    return f"ke-{uuid.uuid4()}"


def create_order(ticker: str, side: str, price: float, count: int, client_order_id: Optional[str] = None,
                 post_only: bool = True) -> Dict[str, Any]:
    """Place a resting (maker) buy order. Never retried on a timeout: check orders() by client id instead."""
    coid = client_order_id or new_client_order_id()
    body = _call("POST", "/portfolio/events/orders", body=order_body(ticker, side, price, count, coid, post_only),
                 idempotent=False)
    order = body.get("order") if isinstance(body.get("order"), dict) else body
    n = normalize_order(dict(order, client_order_id=order.get("client_order_id") or coid, ticker=order.get("ticker") or ticker))
    return n


def cancel_order(order_id: str) -> Dict[str, Any]:
    return _call("DELETE", f"/portfolio/events/orders/{order_id}", idempotent=True)
