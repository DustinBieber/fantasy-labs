"""Shared publishing pieces: encrypted tokens, post metadata, errors."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet

from ..config import Config


class PublishError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class PostMeta:
    title: str
    description: str
    tags: list[str]
    privacy: str = "public"
    public_url: str | None = None      # only for Instagram's url upload mode


class Vault:
    """Encrypts OAuth tokens at rest with a key from CLIPPER_SECRET_KEY or the data dir."""

    def __init__(self, cfg: Config):
        key = os.environ.get("CLIPPER_SECRET_KEY")
        if not key:
            path = cfg.data_dir / ".secret_key"
            if not path.exists():
                path.write_text(Fernet.generate_key().decode())
                path.chmod(0o600)
            key = path.read_text().strip()
        self._key = key.encode()
        self._fernet = Fernet(self._key)

    def seal(self, data: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def open(self, blob: str) -> dict[str, Any]:
        return json.loads(self._fernet.decrypt(blob.encode()))

    def sign(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:32]


def expires_at(expires_in: Any) -> float | None:
    try:
        return time.time() + int(expires_in)
    except (TypeError, ValueError):
        return None


def check(r: httpx.Response, what: str) -> dict[str, Any]:
    """Raise a PublishError with the platform's message on failure."""
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:500]}
    if r.status_code >= 400:
        retryable = r.status_code == 429 or r.status_code >= 500
        raise PublishError(f"{what} failed ({r.status_code}): {json.dumps(body)[:600]}", retryable=retryable)
    return body


def file_size(path: Path) -> int:
    return path.stat().st_size
