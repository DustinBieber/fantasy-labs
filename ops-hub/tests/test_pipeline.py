from datetime import timedelta

from sqlalchemy import func, select

from opshub.models import Alert, Notification, Order, SaleLine, Shipment
from opshub.services import rules, sync
from opshub.services.orders import effective_status
from opshub.timeutil import utcnow


def _kinds(session):
    return {(a.kind, a.title) for a in session.scalars(select(Alert).where(Alert.status == "open"))}


def test_demo_sync_raises_the_expected_alerts(synced):
    titles = " | ".join(t for _, t in _kinds(synced))
    kinds = {k for k, _ in _kinds(synced)}
    assert {"order_deadline", "order_notes", "order_hazmat", "order_priority_waiting", "order_stale",
            "shipment_late", "shipment_stale", "shipment_hazmat_carrier", "shipment_hazmat_paperwork",
            "lot_expiry", "low_stock", "standing_order_short", "invoice_variance"} <= kinds
    assert "Order #2048 (Rosa Delgado) is past its deadline" in titles
    assert "Hazmat Shipment IN-PO-7790 is with a carrier not approved for hazmat" in titles
    assert "Standing order SO-GVG (Green Valley Grocers)" in titles


def test_warehouse_record_rolls_up(synced):
    o = synced.scalar(select(Order).where(Order.number == "#2046", Order.source == "shopify"))
    assert [w.source for w in o.warehouse_records] == ["extensiv"]
    assert o.status == "open" and effective_status(o) == "allocated"
    hazmat = synced.scalar(select(Order).where(Order.number == "#2043", Order.source == "shopify"))
    assert "Lithium" in hazmat.warehouse_records[0].notes and hazmat.is_hazmat


def test_notes_deadlines_resolve(synced):
    o = synced.scalar(select(Order).where(Order.number == "#2052"))
    assert o.required_by_source == "note" and o.order_type == "priority"
    assert synced.scalar(select(Order).where(Order.number == "WH-PO-88240")).required_by_source == "system"


def test_emails_moved_freight_to_new_legs(synced):
    shp = synced.scalar(select(Shipment).where(Shipment.reference == "IN-PO-7781"))
    assert [(l.carrier_name, l.tracking_ref) for l in shp.legs] == [
        ("Pacific Star Line", "MSCU7781234"), ("Harbor Drayage Co.", "HD-55120")]
    # Extensiv and Shopify both report the parcel; it's one shipment.
    assert synced.scalar(select(func.count()).select_from(Shipment)) == 6


def test_resync_is_idempotent(synced, settings):
    counts = lambda: (synced.scalar(select(func.count()).select_from(Order)),  # noqa: E731
                      synced.scalar(select(func.count()).select_from(SaleLine)),
                      synced.scalar(select(func.count()).select_from(Alert)),
                      synced.scalar(select(func.count()).select_from(Notification)))
    before = counts()
    result = sync.sync_all(synced, settings, env={})
    assert result["rules"]["new"] == 0 and result["notifications"]["messages"] == 0
    assert counts() == before


def test_alert_lifecycle(synced, settings):
    alert = synced.scalar(select(Alert).where(Alert.kind == "order_notes", Alert.severity == "info"))
    alert.status = "dismissed"
    synced.commit()
    rules.run(synced, settings)
    assert alert.status == "dismissed"  # stays quiet while unchanged

    # Escalation reopens: the overdue order gets more urgent... simulate by lowering the stored severity.
    late = synced.scalar(select(Alert).where(Alert.kind == "order_deadline", Alert.severity == "urgent"))
    late.status, late.severity = "dismissed", "warn"
    rules.run(synced, settings)
    assert late.status == "open"

    # Condition clears -> auto-resolved.
    order = synced.get(Order, late.subject_id)
    order.status = "shipped"
    rules.run(synced, settings)
    assert late.status == "resolved"


def test_stale_shipment_clears_after_manual_update(synced, settings):
    from opshub.connectors.base import NTrackingUpdate
    from opshub.services import shipments

    shp = synced.scalar(select(Shipment).where(Shipment.reference == "TR-HQ-0921"))
    shipments.apply_update(synced, NTrackingUpdate(occurred_at=utcnow(), status="in_transit",
                                                   shipment_ref=shp.reference, eta=utcnow() + timedelta(days=2),
                                                   source="manual"))
    rules.run(synced, settings)
    open_kinds = {a.kind for a in synced.scalars(select(Alert).where(Alert.subject_type == "shipment",
                                                                      Alert.subject_id == shp.id,
                                                                      Alert.status == "open"))}
    assert not open_kinds & {"shipment_stale", "shipment_late"}


def test_demo_notifications_go_to_outbox(synced):
    statuses = set(synced.scalars(select(Notification.status)))
    assert statuses == {"demo"}
    channels = set(synced.scalars(select(Notification.channel)))
    assert {"google_chat", "telegram", "whatsapp"} <= channels
