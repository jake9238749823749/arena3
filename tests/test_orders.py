import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

from engine.orders import LEGAL_TRANSITIONS, Order, OrderStatus, OrderType

NY = ZoneInfo("America/New_York")


def _order(status=OrderStatus.NEW):
    return Order(
        order_id=1,
        instrument="MES",
        contract="MESH21",
        side=1,
        quantity=2,
        order_type=OrderType.LIMIT,
        submitted_ts=datetime(2021, 3, 1, 10, 0, tzinfo=NY),
        status=status,
        limit_price=4000.0,
    )


def test_illegal_transition_rejected():
    o = _order(OrderStatus.FILLED)
    with pytest.raises(ValueError):
        o.transition(OrderStatus.NEW)


def test_legal_map_covers_every_status():
    assert set(LEGAL_TRANSITIONS) == set(OrderStatus)


def test_apply_fill_updates_average_and_terminal_state():
    o = _order()
    o.apply_fill(1, 4000.0, 0.62, 1.25)
    assert o.status is OrderStatus.PARTIALLY_FILLED
    assert o.remaining == 1
    o.apply_fill(1, 4002.0, 0.62, 1.25)
    assert o.status is OrderStatus.FILLED
    assert o.avg_fill_price == 4001.0
    assert o.remaining == 0
