"""3PL invoice audit: check the partner warehouse's invoice against the rate card and
what its WMS says it actually did for us that period.

Each invoice line gets an expected amount (activity qty x contracted rate) and
findings that explain any difference: rate above contract, more units than the
activity supports, duplicate lines, charges not on the rate card, arithmetic
errors, and charges that need a signed-off work order before they are payable.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.base import NInvoice, NInvoiceLine
from ..models import Invoice, InvoiceLine, RateCardEntry, WarehouseActivity
from ..timeutil import parse_date

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _money(v: Decimal) -> Decimal:
    return v.quantize(CENT)


def _dec(value: str) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip() or "0")
    except Exception:
        return ZERO


def parse_invoice_csv(text: str, default_vendor: str | None = None, source_ref: str | None = None) -> NInvoice | None:
    """Invoice CSV: ``Key,Value`` header rows, then a line table, then an optional Total row.

    Header keys recognised: Vendor, Invoice Number, Invoice Date, Billing Period (start,end).
    Line columns are found by name: charge code, description, quantity/qty, rate, amount.
    """
    rows = list(csv.reader(io.StringIO(text)))
    meta: dict[str, list[str]] = {}
    table_at = None
    for i, row in enumerate(rows):
        if not row or not any(c.strip() for c in row):
            continue
        cells = [c.strip().lower() for c in row]
        if "description" in cells and "amount" in cells:
            table_at = i
            break
        meta[cells[0]] = [c.strip() for c in row[1:]]
    if table_at is None or "invoice number" not in meta:
        return None
    header = [c.strip().lower() for c in rows[table_at]]

    def col(*names: str) -> int | None:
        return next((header.index(n) for n in names if n in header), None)

    c_code, c_desc = col("charge code", "code", "sku"), col("description")
    c_qty, c_rate, c_amt = col("quantity", "qty", "units"), col("rate", "unit price", "price"), col("amount", "total")
    lines, stated_total = [], None
    for row in rows[table_at + 1:]:
        if not row or not any(c.strip() for c in row):
            continue
        cell = lambda idx: row[idx].strip() if idx is not None and idx < len(row) else ""  # noqa: E731
        if row[0].strip().lower() in {"total", "invoice total", "grand total"}:
            stated_total = _dec(cell(c_amt) or next((c for c in reversed(row) if c.strip()), "0"))
            continue
        lines.append(NInvoiceLine(charge_code=cell(c_code) or None, description=cell(c_desc),
                                  qty=_dec(cell(c_qty)), rate=_dec(cell(c_rate)), amount=_dec(cell(c_amt))))
    period = meta.get("billing period") or []
    start, end = (parse_date(period[0]) if period else None), (parse_date(period[1]) if len(period) > 1 else None)
    if not (start and end):
        return None
    vendor = (meta.get("vendor") or [default_vendor or "Unknown 3PL"])[0]
    return NInvoice(vendor=vendor, number=meta["invoice number"][0], period_start=start, period_end=end,
                    invoice_date=parse_date((meta.get("invoice date") or [""])[0]),
                    total=stated_total if stated_total is not None else sum((l.amount for l in lines), ZERO),
                    lines=lines, source_ref=source_ref)


@dataclass
class LineAudit:
    line_id: int
    charge_code: str | None
    description: str
    qty: Decimal
    rate: Decimal
    amount: Decimal
    contract_rate: Decimal | None = None
    supported_qty: Decimal | None = None
    expected: Decimal = ZERO
    findings: list[dict] = field(default_factory=list)

    @property
    def variance(self) -> Decimal:
        return _money(self.amount - self.expected)


def _rates_for(session: Session, inv: Invoice) -> dict[str, RateCardEntry]:
    rows = session.scalars(select(RateCardEntry).where(
        RateCardEntry.vendor == inv.vendor, RateCardEntry.effective_from <= inv.period_end))
    rates: dict[str, RateCardEntry] = {}
    for r in sorted(rows, key=lambda r: r.effective_from):
        if r.effective_to is None or r.effective_to >= inv.period_start:
            rates[r.charge_code] = r  # latest effective entry wins
    return rates


def _activity_for(session: Session, inv: Invoice) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for a in session.scalars(select(WarehouseActivity).where(
            WarehouseActivity.vendor == inv.vendor, WarehouseActivity.activity_date >= inv.period_start,
            WarehouseActivity.activity_date <= inv.period_end)):
        totals[a.charge_code] += a.qty
    return dict(totals)


def audit(session: Session, inv: Invoice, tolerance: Decimal = CENT) -> dict:
    rates = _rates_for(session, inv)
    activity = _activity_for(session, inv)
    remaining = dict(activity)
    seen: set[tuple] = set()
    audits: list[LineAudit] = []

    for line in inv.lines:
        la = LineAudit(line_id=line.id, charge_code=line.charge_code, description=line.description,
                       qty=line.qty, rate=line.rate, amount=line.amount)
        audits.append(la)
        code = line.charge_code
        if not code or code not in rates:
            desc = line.description.lower()
            code = next((c for c, r in rates.items() if r.description.lower() in desc), code)
        card = rates.get(code) if code else None

        if abs(line.qty * line.rate - line.amount) > tolerance:
            la.findings.append({"code": "math_error", "message":
                                f"{line.qty} x ${line.rate} = ${_money(line.qty * line.rate)}, billed ${line.amount}"})
        key = (code, line.description.strip().lower(), line.qty, line.rate, line.amount)
        if key in seen:
            la.findings.append({"code": "duplicate", "message": "Same charge already billed on this invoice"})
            continue
        seen.add(key)
        if card is None:
            la.findings.append({"code": "unknown_charge", "message": "Not on the rate card for this period"})
            continue
        la.charge_code, la.contract_rate = code, card.rate
        if line.rate - card.rate > tolerance:
            la.findings.append({"code": "rate_above_contract",
                                "message": f"Billed ${line.rate} {card.unit}; contract is ${card.rate}"})
        elif card.rate - line.rate > tolerance:
            la.findings.append({"code": "rate_below_contract",
                                "message": f"Billed ${line.rate}; contract is ${card.rate} (in our favour)"})
        have = remaining.get(code, ZERO)
        if code not in activity:
            if card.requires_approval:
                la.findings.append({"code": "needs_approval", "message":
                                    f"{card.description} needs a signed-off work order; none on file for the period"})
            else:
                la.findings.append({"code": "no_activity", "message": "No matching warehouse activity in the period"})
            la.supported_qty = ZERO
            continue
        supported = min(line.qty, have)
        remaining[code] = have - supported
        la.supported_qty = supported
        if line.qty > supported:
            la.findings.append({"code": "qty_over_activity", "message":
                                f"Billed {line.qty}; warehouse activity supports {supported}"})
        expected = supported * min(line.rate, card.rate)
        if card.min_charge and expected < card.min_charge:
            expected = card.min_charge
        la.expected = _money(expected)

    unbilled = [{"charge_code": c, "qty": str(q), "description": rates[c].description if c in rates else c}
                for c, q in remaining.items() if q > 0 and c in activity and q == activity[c]]
    billed = _money(sum((l.amount for l in inv.lines), ZERO))
    expected_total = _money(sum((a.expected for a in audits), ZERO))
    over = _money(sum((a.variance for a in audits if a.variance > 0), ZERO))
    header_findings = []
    if abs(billed - inv.total) > tolerance:
        header_findings.append({"code": "total_mismatch",
                                "message": f"Lines add up to ${billed}; invoice total says ${inv.total}"})
    issues = sum(1 for a in audits for f in a.findings if f["code"] != "rate_below_contract") + len(header_findings)
    return {
        "invoice": {"id": inv.id, "vendor": inv.vendor, "number": inv.number, "status": inv.status,
                    "period_start": inv.period_start.isoformat(), "period_end": inv.period_end.isoformat(),
                    "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                    "stated_total": str(_money(inv.total)), "source_ref": inv.source_ref},
        "billed_total": str(billed),
        "expected_total": str(expected_total),
        "overbilled": str(over),
        "issues": issues,
        "findings": header_findings,
        "not_billed": unbilled,
        "lines": [{"line_id": a.line_id, "charge_code": a.charge_code, "description": a.description,
                   "qty": str(a.qty), "rate": str(a.rate), "amount": str(_money(a.amount)),
                   "contract_rate": str(a.contract_rate) if a.contract_rate is not None else None,
                   "supported_qty": str(a.supported_qty) if a.supported_qty is not None else None,
                   "expected": str(a.expected), "variance": str(a.variance), "findings": a.findings}
                  for a in audits],
    }


def upsert_invoice(session: Session, n: NInvoice, source: str) -> tuple[Invoice, bool]:
    inv = session.scalar(select(Invoice).where(Invoice.vendor == n.vendor, Invoice.number == n.number))
    if inv is not None:
        if inv.status != "received":  # already reviewed; don't overwrite what was audited
            return inv, False
        inv.lines.clear()
    else:
        inv = Invoice(vendor=n.vendor, number=n.number, source=source)
        session.add(inv)
    inv.invoice_date, inv.period_start, inv.period_end = n.invoice_date, n.period_start, n.period_end
    inv.total, inv.source_ref = n.total, n.source_ref
    for l in n.lines:
        inv.lines.append(InvoiceLine(charge_code=l.charge_code, description=l.description, qty=l.qty,
                                     rate=l.rate, amount=l.amount))
    return inv, True
