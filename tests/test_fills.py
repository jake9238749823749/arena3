from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.fills import match_order, stop_fill_price
from engine.orders import Order, OrderStatus, OrderType
from tests.helpers import bar, es_mes_registry

NY = ZoneInfo("America/New_York")
T0 = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
T1 = T0 + timedelta(minutes=1)


def _mkt(side=1):
    return Order(
        order_id=1,
        instrument="MES",
        contract="MESH21",
        side=side,
        quantity=1,
        order_type=OrderType.MARKET,
        submitted_ts=T0,
        status=OrderStatus.NEW,
        min_bars_before_fill=1,
        bars_seen=1,
    )


def test_market_does_not_fill_on_submit_bar():
    spec = es_mes_registry()["MES"]
    o = _mkt()
    o.bars_seen = 0
    dec = match_order(o, bar(T0, "MES", 4000, 4001, 3999, 4000), spec, slippage_ticks=1)
    assert dec.fills is False


def test_market_fills_next_open_plus_adverse_tick():
    spec = es_mes_registry()["MES"]
    o = _mkt(side=1)
    dec = match_order(o, bar(T1, "MES", 4000, 4002, 3999, 4001), spec, slippage_ticks=1)
    assert dec.fills
    assert dec.price == 4000.25  # buy pays +1 tick


def test_market_sell_slippage_is_adverse():
    spec = es_mes_registry()["MES"]
    o = _mkt(side=-1)
    dec = match_order(o, bar(T1, "MES", 4000, 4002, 3999, 4001), spec, slippage_ticks=2)
    assert dec.price == 3999.50  # sell receives -2 ticks


def test_limit_no_price_improvement():
    spec = es_mes_registry()["MES"]
    o = Order(
        order_id=2,
        instrument="MES",
        contract="MESH21",
        side=-1,
        quantity=1,
        order_type=OrderType.LIMIT,
        submitted_ts=T0,
        status=OrderStatus.NEW,
        limit_price=4010.0,
        min_bars_before_fill=1,
        bars_seen=1,
    )
    # Bar trades through the limit up to 4015 — still fill at 4010.
    dec = match_order(o, bar(T1, "MES", 4008, 4015, 4007, 4012), spec, slippage_ticks=1)
    assert dec.fills
    assert dec.price == 4010.0


def test_stop_gap_uses_worse_of_open_and_stop():
    spec = es_mes_registry()["MES"]
    o = Order(
        order_id=3,
        instrument="MES",
        contract="MESH21",
        side=-1,  # sell stop (long exit)
        quantity=1,
        order_type=OrderType.STOP_MARKET,
        submitted_ts=T0,
        status=OrderStatus.NEW,
        stop_price=3990.0,
        min_bars_before_fill=1,
        bars_seen=1,
    )
    b = bar(T1, "MES", 3980.0, 3985.0, 3975.0, 3982.0)  # gapped through the stop
    px = stop_fill_price(o, b, spec, slippage_ticks=1)
    # worse of open 3980 and stop 3990 for a sell is 3980, then -1 tick
    assert px == 3979.75
