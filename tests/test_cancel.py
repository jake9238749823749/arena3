from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.broker import Broker, BrokerConfig
from engine.orders import Order, OrderStatus, OrderType
from engine.portfolio import Portfolio
from tests.helpers import bar, es_mes_registry

NY = ZoneInfo("America/New_York")


def test_canceled_market_order_does_not_fill():
    specs = es_mes_registry().specs
    port = Portfolio(100_000)
    for s in specs.values():
        port.register_spec(s)
    broker = Broker(port, specs, BrokerConfig(slippage_ticks=1))
    t0 = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
    t1 = t0 + timedelta(minutes=1)
    order = broker.submit(
        Order(
            order_id=0,
            instrument="MES",
            contract="MESH21",
            side=1,
            quantity=1,
            order_type=OrderType.MARKET,
            submitted_ts=t0,
            min_bars_before_fill=1,
        )
    )
    broker.cancel(order.order_id, "user")
    fills = broker.on_bar(bar(t1, "MES", 4000, 4001, 3999, 4000))
    assert fills == []
    assert order.status is OrderStatus.CANCELED
    assert port.is_flat()


def test_replace_leaves_old_order_unfilled():
    specs = es_mes_registry().specs
    port = Portfolio(100_000)
    for s in specs.values():
        port.register_spec(s)
    broker = Broker(port, specs, BrokerConfig(slippage_ticks=0))
    t0 = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
    t1 = t0 + timedelta(minutes=1)
    old = broker.submit(
        Order(
            order_id=0,
            instrument="MES",
            contract="MESH21",
            side=1,
            quantity=1,
            order_type=OrderType.LIMIT,
            submitted_ts=t0,
            limit_price=3990.0,
            min_bars_before_fill=1,
        )
    )
    new = broker.replace(old.order_id, limit_price=3980.0, ts=t0, tag="replace")
    assert old.status is OrderStatus.REPLACED
    # Bar touches 3990 (old) but not 3980 (new) — nothing should fill.
    fills = broker.on_bar(bar(t1, "MES", 3995, 3996, 3989, 3992))
    assert fills == []
    assert new.is_working
