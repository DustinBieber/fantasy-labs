"""Phone alerts through Telegram (works on Android and iPhone). Optional: silent if not configured."""
from __future__ import annotations

import logging

from .. import config
from .http import FetchError, request

log = logging.getLogger("kalshi_edge.notify")


def configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send(text: str) -> bool:
    if not configured():
        return False
    try:
        request("telegram", "POST", f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json_body={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000], "disable_web_page_preview": True},
                idempotent=True)
        return True
    except FetchError as e:
        log.warning("telegram alert failed: %s", e)
        return False
