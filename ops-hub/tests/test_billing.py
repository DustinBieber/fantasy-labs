from decimal import Decimal

from sqlalchemy import select

from opshub.demo import fixtures
from opshub.models import Invoice
from opshub.services import billing
from opshub.timeutil import utcnow


def test_parse_invoice_csv():
    _, text = fixtures.invoice_csv(utcnow(), back=1)
    inv = billing.parse_invoice_csv(text)
    assert inv.vendor == "Riverside 3PL" and inv.number.endswith("-114")
    assert len(inv.lines) == 9 and inv.total == Decimal("2371.20")
    assert inv.lines[6].charge_code is None and inv.lines[6].description == "Fuel surcharge"
    assert billing.parse_invoice_csv("just,some\nrandom,csv\n") is None


def test_audit_finds_each_overbilling(synced, settings):
    inv = synced.scalar(select(Invoice).where(Invoice.number.like("%-114")))
    a = billing.audit(synced, inv, settings.billing_tolerance)
    assert (a["billed_total"], a["expected_total"], a["overbilled"]) == ("2371.20", "1848.70", "522.50")
    codes = {l["description"]: [f["code"] for f in l["findings"]] for l in a["lines"]}
    assert codes["Pallet storage - " + inv.period_start.strftime("%B")] == ["rate_above_contract"]
    assert codes["Order pick & pack"] == ["qty_over_activity"]
    assert codes["Fuel surcharge"] == ["unknown_charge"]
    assert codes["Special project labor - relabel"] == ["needs_approval"]
    assert codes["Retail labeling"] == ["math_error"]
    hazmat = [l for l in a["lines"] if l["charge_code"] == "HAZ-HNDL"]
    assert [l["variance"] for l in hazmat] == ["0.00", "70.00"]
    assert [f["code"] for f in hazmat[1]["findings"]] == ["duplicate"]
    assert a["issues"] == 6


def test_clean_invoice_passes(synced, settings):
    inv = synced.scalar(select(Invoice).where(Invoice.number.like("%-097")))
    a = billing.audit(synced, inv, settings.billing_tolerance)
    assert a["overbilled"] == "0.00" and a["issues"] == 0 and a["billed_total"] == a["expected_total"]


def test_reviewed_invoice_is_not_overwritten(synced):
    inv = synced.scalar(select(Invoice).where(Invoice.number.like("%-114")))
    inv.status = "approved"
    n = billing.parse_invoice_csv(fixtures.invoice_csv(utcnow(), back=1)[1].replace("920.00", "1.00"))
    _, changed = billing.upsert_invoice(synced, n, "gmail")
    assert not changed and inv.lines[0].amount == Decimal("920.00")
