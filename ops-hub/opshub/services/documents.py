"""Emails and files: 3PL invoices (CSV, Excel, PDF), Muse handoff files, carrier/forwarder updates."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..connectors.base import NDocument, NTrackingUpdate
from ..models import ProcessedDocument
from ..timeutil import end_of_day_utc, utcnow
from . import ai, billing, files, muse, shipments, signals

SHIPPING_WORDS = re.compile(r"\b(?:tracking|shipment|container|pro\b|bol\b|freight|eta\b|pickup|delivery|"
                            r"interline|drayage|consignment|waybill)", re.IGNORECASE)


def process(session: Session, doc: NDocument, source: str, settings: Settings,
            env: dict[str, str] | None = None) -> str:
    """Returns what the document turned out to be: invoice | invoice_unread | tracking_update | muse |
    unmatched | ignored | seen."""
    if session.scalar(select(ProcessedDocument).where(ProcessedDocument.source == source,
                                                      ProcessedDocument.external_id == doc.external_id)):
        return "seen"
    kind, summary = "ignored", None
    if doc.hint == "muse":
        kind = muse.import_document(session, doc, settings, env)
        summary = f"Muse handoff: {doc.title}"
    else:
        for filename, content in doc.attachments:
            n = _read_invoice(filename, content, f"{source}:{doc.external_id}:{filename}", settings, env)
            if n is None:
                continue
            if n == "unread":
                kind, summary = "invoice_unread", f"{filename}: PDF invoice needs an AI key (or a CSV copy) to read"
                continue
            billing.upsert_invoice(session, n, source)
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
                                  summary=(summary or doc.title)[:300]))
    return kind


def _read_invoice(filename: str, content, source_ref: str, s: Settings, env):
    low = filename.lower()
    if low.endswith((".csv", ".tsv", ".xlsx", ".xlsm")):
        try:
            rows = files.read_table(filename, content)
        except Exception:
            return None
        return billing.parse_invoice_csv(files.table_to_csv(rows), source_ref=source_ref)
    if low.endswith(".pdf") and isinstance(content, bytes):
        try:
            text = files.pdf_text(content)
        except Exception:
            return None
        if "invoice" not in text.lower():
            return None
        if not ai.configured_providers(s, env):
            return "unread"
        return billing.invoice_from_text(text, s, env, source_ref) or "unread"
    return None


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
