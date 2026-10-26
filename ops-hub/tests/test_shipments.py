from datetime import timedelta

from opshub.connectors.base import NShipment, NTrackingUpdate
from opshub.models import Carrier
from opshub.services import shipments
from opshub.timeutil import utcnow


def _book(session, **kw):
    n = NShipment(reference="REF-1", direction="inbound", mode="ltl", carrier_name="QuickHaul", tracking_ref="PRO-111",
                  booked_at=utcnow() - timedelta(days=2), **kw)
    return shipments.upsert_shipment(session, n, "test", True, lambda ref: None)[0]


def test_carrier_aliases(session):
    session.add(Carrier(name="QuickHaul Logistics", aliases=["QHL"]))
    session.flush()
    assert shipments.match_carrier(session, "quickhaul logistics, inc.").name == "QuickHaul Logistics"
    assert shipments.match_carrier(session, "QHL").name == "QuickHaul Logistics"
    assert shipments.match_carrier(session, "Someone Else") is None


def test_handoff_creates_leg_and_later_updates_follow_new_ref(session):
    shp = _book(session)
    session.flush()
    now = utcnow()
    shipments.apply_update(session, NTrackingUpdate(occurred_at=now - timedelta(hours=5), status="in_transit",
                                                    tracking_ref="PRO-111", new_carrier="Central Plains Freight",
                                                    new_tracking_ref="CPF-9"))
    session.flush()
    assert [l.tracking_ref for l in shp.legs] == ["PRO-111", "CPF-9"]
    assert shp.legs[0].status == "handed_off" and shp.status == "in_transit"
    # The next carrier quotes only its own reference.
    shipments.apply_update(session, NTrackingUpdate(occurred_at=now, status="delivered", tracking_ref="CPF-9"))
    session.flush()
    assert shp.status == "delivered" and shp.legs[1].status == "delivered"
    # Same update again is ignored; unknown ref matches nothing.
    before = len(shp.events)
    shipments.apply_update(session, NTrackingUpdate(occurred_at=now, status="delivered", tracking_ref="CPF-9"))
    assert len(shp.events) == before
    assert shipments.apply_update(session, NTrackingUpdate(occurred_at=now, status="x", tracking_ref="??")) is None


def test_booking_sources_never_move_status_backwards(session):
    shp = _book(session)
    shp.status = "delivered"
    n = NShipment(reference="OTHER", direction="outbound", mode="parcel", carrier_name="X", tracking_ref="PRO-111",
                  status="in_transit")
    same, created = shipments.upsert_shipment(session, n, "other", False, lambda ref: None)
    assert same is shp and not created and shp.status == "delivered"


def test_read_email_handoff():
    now = utcnow()
    text = ("Container MSCU7781234 was discharged at Port of Long Beach. It has been handed off to Harbor Drayage Co. "
            "for final delivery under ref HD-55120.\nRevised ETA: 10/27/2026.")
    u = shipments.read_email(text, ["MSCU7781234", "IN-PO-7781"], now, "America/New_York")
    assert (u.tracking_ref, u.status, u.new_carrier, u.new_tracking_ref) == \
           ("MSCU7781234", "handed_off", "Harbor Drayage Co", "HD-55120")
    assert u.eta.date().isoformat() in ("2026-10-27", "2026-10-28")  # end of day local, stored as UTC

    u = shipments.read_email("PRO 4471983 has been interlined to Central Plains Freight at the Dallas terminal. "
                             "New PRO: CPF-220931. Estimated delivery Oct 26.", ["4471983"], now, "America/New_York")
    assert (u.new_carrier, u.new_tracking_ref) == ("Central Plains Freight", "CPF-220931")
    u = shipments.read_email("Shipment PRO-1 is delayed due to weather", ["PRO-1"], now, "UTC")
    assert u.status == "exception" and u.new_carrier is None
    assert shipments.read_email("nothing we know about", ["PRO-1"], now, "UTC") is None
