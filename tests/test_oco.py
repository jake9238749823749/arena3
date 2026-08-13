from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.broker import Broker, BrokerConfig
from engine.orders import Order, OrderType
from engine.portfolio import Portfolio
from tests.helpers import bar, es_mes_registry

NY = ZoneInfo("America/New_York")
T0 = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
T1 = T0 + timedelta(minutes=1)


def test_same_bar_stop_and_target_fills_stop_only():
    specs = es_mes_registry().specs
    port = Portfolio(100_000)
    for s in specs.values():
        port.register_spec(s)
    broker = Broker(port, specs, BrokerConfig(slippage_ticks=1, intrabar_ambiguity="worst_case"))
    stop = Order(
        order_id=0,
        instrument="MES",
        contract="MESH21",
        side=-1,
        quantity=1,
        order_type=OrderType.STOP_MARKET,
        submitted_ts=T0,
        stop_price=3990.0,
        oco_group="g1",
        min_bars_before_fill=1,
        meta={"role": "stop"},
    )
    target = Order(
        order_id=0,
        instrument="MES",
        contract="MESH21",
        side=-1,
        quantity=1,
        order_type=OrderType.LIMIT,
        submitted_ts=T0,
        limit_price=4020.0,
        oco_group="g1",
        min_bars_before_fill=1,
        meta={"role": "target"},
    )
    broker.submit(stop)
    broker.submit(target)
    # Need an open long so the sell fills can book.
    from engine.fills import Fill

    port.apply_fill(
        Fill(
            fill_id=0,
            order_id=0,
            ts=T0,
            instrument="MES",
            contract="MESH21",
            side=+1,
            quantity=1,
            price=4000.0,
            commission=0.0,
            slippage_ticks=0,
            slippage_paid=0.0,
            liquidity="taker",
            reason="seed",
        )
    )
    # Bar trades through BOTH levels.
    fills = broker.on_bar(bar(T1, "MES", 4000.0, 4030.0, 3980.0, 4010.0))
    stop_fills = [f for f in fills if f.order_id == stop.order_id]
    target_fills = [f for f in fills if f.order_id == target.order_id]
    assert len(stop_fills) == 1
    assert target_fills == []
    assert stop_fills[0].ambiguous is True
    assert target.is_terminal
    assert port.open_position_count() == 0
