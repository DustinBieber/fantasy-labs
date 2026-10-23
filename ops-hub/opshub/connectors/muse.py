"""Muse: placeholder until we know which product this is and what it should share."""
from __future__ import annotations

from .base import Connector, register


@register
class MuseConnector(Connector):
    key = "muse"
    name = "Muse"
    category = "other"
    capabilities = ()
    settings_keys = ("MUSE_API_KEY",)
    planned = True
    status_note = "Not built. Which Muse product is this, and what should it send or receive?"
