"""LLM extraction with provider fallback (AI_PROVIDERS order), returning parsed JSON or None.

Callers always have a rule-based path, so None just means "no AI help this time".
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

from ..config import Settings
from ..connectors import ai_providers

log = logging.getLogger(__name__)

SYSTEM = ("You extract structured facts for a retail operations team from messy text. "
          "Reply with a single JSON object and nothing else. Use null when a value is not stated.")


def configured_providers(settings: Settings, env: dict[str, str] | None = None) -> list:
    providers = ai_providers(env)
    return [providers[k] for k in settings.ai_providers if k in providers and providers[k].is_configured()]


def _parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def complete_json(settings: Settings, prompt: str, env: dict[str, str] | None = None) -> tuple[dict | None, str | None]:
    """(result, provider key) from the first configured provider that answers with valid JSON."""
    for provider in configured_providers(settings, env):
        try:
            result = _parse_json(provider.complete(SYSTEM, prompt))
        except Exception as exc:  # network, auth, quota: try the next provider
            log.warning("AI provider %s failed: %s", provider.key, exc)
            continue
        if result is not None:
            return result, provider.key
    return None, None


def read_order_notes(settings: Settings, notes: str, placed_on: date, env: dict[str, str] | None = None) -> dict | None:
    prompt = (
        f"Order placed {placed_on.isoformat()}. Order notes:\n\"\"\"\n{notes}\n\"\"\"\n\n"
        "Return JSON: {\"deadline\": \"YYYY-MM-DD\" (date the customer needs it by, if any), "
        "\"deadline_quote\": the words that state it, "
        "\"important\": [short phrases staff must act on: special handling, holds, address changes, "
        "delivery instructions, cancellation requests], "
        "\"priority\": true if the customer is asking for rush/expedited handling, "
        "\"hazmat\": true if the notes mention batteries, aerosols, flammables or dangerous goods}"
    )
    result, provider = complete_json(settings, prompt, env)
    if result is not None:
        result["provider"] = provider
    return result


def read_tracking_email(settings: Settings, text: str, open_refs: list[str],
                        env: dict[str, str] | None = None) -> dict | None:
    prompt = (
        f"Open shipment and tracking references: {', '.join(open_refs)}\n\n"
        f"Carrier / forwarder message:\n\"\"\"\n{text}\n\"\"\"\n\n"
        "Return JSON: {\"ref\": the reference above this message is about (or null), "
        "\"status\": one of booked|picked_up|in_transit|at_terminal|handed_off|out_for_delivery|delivered|exception, "
        "\"location\": where it is now, \"eta\": \"YYYY-MM-DD\" or null, "
        "\"new_carrier\": carrier or agent it was handed to (or null), "
        "\"new_tracking_ref\": the new PRO/BOL/reference after the handoff (or null), "
        "\"summary\": one sentence}"
    )
    return complete_json(settings, prompt, env)[0]
