"""Encrypts integration secrets (OAuth tokens, API keys, passwords) before they hit the database."""
import hashlib
import hmac
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet

from .config import settings

log = logging.getLogger(__name__)
_KEY_FILE = Path(os.environ.get("FAMILY_HUB_KEY_FILE", ".family_hub_secret"))


@lru_cache
def _fernet() -> Fernet:
    return Fernet(_key())


@lru_cache
def _key() -> bytes:
    key = settings.secret_key
    if not key:
        if _KEY_FILE.exists():
            key = _KEY_FILE.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            _KEY_FILE.write_text(key)
            _KEY_FILE.chmod(0o600)
            log.warning("FAMILY_HUB_SECRET_KEY not set; generated one in %s. Back it up.", _KEY_FILE)
    return key.encode()


def encrypt_json(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(blob: str) -> dict:
    return json.loads(_fernet().decrypt(blob.encode()))


def sign(text: str) -> str:
    """Short HMAC used for unguessable file links."""
    key = hashlib.sha256(b"family-hub-links:" + _key()).digest()
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]


def verify_signature(text: str, sig: str) -> bool:
    return hmac.compare_digest(sign(text), sig or "")
