"""Emails and files: 3PL invoices (CSV) and carrier/forwarder status updates."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..connectors.base import NDocument, NTrackingUpdate
from ..models import ProcessedDocument
from ..timeutil import end_of_day_utc, utcnow
from . import ai, billing, shipments, signals

SHIPPING_WORDS = re.compile(r"\b(?:tracking|shipment|container|pro\b|bol\b|freight|eta\b|pickup|delivery|"
                            r"interline|drayage|consignment|waybill)", re.IGNORECASE)


def process(session: Session, doc: NDocument, source: str, settings: Settings,
            env: dict[str, str] | None = None) -> str:
    """Returns what the document turned out to be: invoice | tracking_update | unmatched | ignored | seen."""
    if session.scalar(select(ProcessedDocument).where(ProcessedDocument.source == source,
                                                      ProcessedDocument.external_id == doc.external_id)):
        return "seen"
    kind, summary = "ignored", None
    for filename, content in doc.attachments:
        if filename.lower().endswith(".csv"):
            n = billing.parse_invoice_csv(content, source_ref=f"{source}:{doc.external_id}:{filename}")
            if n:
                inv, _ = billing.upsert_invoice(session, n, source)
                kind, summary = "invoice", f"Invoice {n.number} from {n.vendor}, ${n.total}"
    if kind == "ignored":
        text = f"{doc.title}\n{doc.text}"
        received = doc.received_at or utcnow()
        update = _tracking_update(session, text, received, settings, env)
        if update is not None:
            shp = shipments.apply_update(session, update)
            if shp is not None:
                kind = "tracking_update"
                summary = f"{shp.reference}: {update.status}" + (f" -> {update.new_carrier}" if update.new_carrier else "")
            else:
                kind, summary = "unmatched", doc.title
        elif SHIPPING_WORDS.search(text):
            kind, summary = "unmatched", doc.title
    session.add(ProcessedDocument(source=source, external_id=doc.external_id, kind=kind,
                                  summary=summary or doc.title[:300]))
    return kind


def _tracking_update(session: Session, text: str, received, settings: Settings, env) -> NTrackingUpdate | None:
    refs = shipments.open_refs(session)
    if not refs:
        return None
    if ai.configured_providers(settings, env):
        r = ai.read_tracking_email(settings, text, refs, env)
        if r and r.get("ref") in refs:
            eta = signals.parse_date_value(str(r.get("eta") or ""), received.date())
            return NTrackingUpdate(
                occurred_at=received, status=r.get("status") or "in_transit", tracking_ref=r["ref"],
                location_text=r.get("location"), description=r.get("summary") or text[:400],
                eta=end_of_day_utc(eta, settings.timezone) if eta else None,
                new_carrier=r.get("new_carrier"), new_tracking_ref=r.get("new_tracking_ref"), source="email",
            )
    return shipments.read_email(text, refs, received, settings.timezone)
