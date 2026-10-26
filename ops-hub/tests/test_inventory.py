from datetime import date, timedelta

from opshub.models import InventoryLot, Location, Product, SaleLine, StockLevel
from opshub.services import inventory
from opshub.timeutil import utcnow


def _setup(session):
    loc = Location(code="W1", name="W1", kind="warehouse")
    p = Product(sku="A", name="A")
    session.add_all([loc, p])
    session.flush()
    today = date.today()
    # received order differs from best-by order, to tell FIFO from FEFO
    session.add_all([
        InventoryLot(product_id=p.id, location_id=loc.id, lot_number="OLD-RECV-LATE-BB", received_on=today - timedelta(60),
                     best_by=today + timedelta(200), qty_on_hand=10, source="t"),
        InventoryLot(product_id=p.id, location_id=loc.id, lot_number="NEW-RECV-EARLY-BB", received_on=today - timedelta(10),
                     best_by=today + timedelta(30), qty_on_hand=10, source="t"),
        StockLevel(product_id=p.id, location_id=loc.id, on_hand=20, available=20, source="t"),
    ])
    session.flush()
    return p, loc


def test_fefo_vs_fifo(session):
    _setup(session)
    fefo = inventory.allocate(session, "A", 5, policy="fefo")
    fifo = inventory.allocate(session, "A", 5, policy="fifo")
    assert fefo["picks"][0]["lot_number"] == "NEW-RECV-EARLY-BB"
    assert fifo["picks"][0]["lot_number"] == "OLD-RECV-LATE-BB"


def test_min_shelf_life_skips_short_dated(session):
    _setup(session)
    plan = inventory.allocate(session, "A", 15, min_days_left=90)
    assert [p["lot_number"] for p in plan["picks"]] == ["OLD-RECV-LATE-BB"]
    assert plan["shortfall"] == 5 and plan["skipped"][0]["lot_number"] == "NEW-RECV-EARLY-BB"
    assert inventory.allocate(session, "NOPE", 1)["error"] == "unknown SKU"


def test_sell_through_and_expiry_risk(session):
    p, loc = _setup(session)
    now = utcnow()
    for i in range(30):  # 1 unit/day for 30 days
        session.add(SaleLine(source="t", external_id=str(i), product_id=p.id, location_id=loc.id,
                             sold_at=now - timedelta(days=i, hours=1), qty=1))
    session.flush()
    row = inventory.sell_through(session, 30, now)[0]
    assert (row["sold"], row["on_hand"], row["sell_through_pct"], row["per_day"], row["days_of_supply"]) == \
           (30, 20, 60.0, 1.0, 20.0)
    lots = {l["lot_number"]: l for l in inventory.lots(session, now, 30)}
    # 10 units, 30 days left at 1/day: sells out in time; later lot has 200 days for 10 units after it.
    assert lots["NEW-RECV-EARLY-BB"]["at_risk_units"] == 0
    assert lots["OLD-RECV-LATE-BB"]["at_risk_units"] == 0


def test_low_stock_suggests_replenishment(session):
    p, w = _setup(session)
    store = Location(code="S1", name="Store", kind="store")
    session.add(store)
    session.flush()
    session.add(StockLevel(product_id=p.id, location_id=store.id, on_hand=2, available=2, source="t"))
    now = utcnow()
    session.add_all([SaleLine(source="t", external_id=f"s{i}", product_id=p.id, location_id=store.id,
                              sold_at=now - timedelta(days=i), qty=1) for i in range(30)])
    session.flush()
    low = inventory.low_stock(session, 30, now, threshold_days=14)
    assert low[0]["location"] == "S1" and low[0]["days_of_supply"] == 2.0
    assert low[0]["replenish_from"] == [{"location": "W1", "available": 20}]
