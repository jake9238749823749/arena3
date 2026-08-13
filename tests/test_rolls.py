from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.broker import Broker, BrokerConfig
from engine.fills import Fill
from engine.orders import Order, OrderType
from engine.portfolio import Portfolio
from futures.rolls import RollCalendar, assert_not_continuous
from tests.helpers import bar, es_mes_registry

NY = ZoneInfo("America/New_York")


def test_continuous_identifier_rejected():
    import pytest

    with pytest.raises(ValueError):
        assert_not_continuous("CONTINUOUS")
    with pytest.raises(ValueError):
        assert_not_continuous("ES1!")


def test_scheduled_rolls_are_dated_contracts():
    spec = es_mes_registry()["MES"]
    cal = RollCalendar(spec, days_before=8)
    start = datetime(2021, 1, 1, tzinfo=NY)
    end = datetime(2021, 12, 31, tzinfo=NY)
    evs = cal.events(start, end)
    assert evs
    for ev in evs:
        assert ev.old_contract.startswith("MES")
        assert ev.new_contract.startswith("MES")
        assert ev.old_contract != ev.new_contract


def test_roll_transfer_leaves_no_phantom_old_position():
    specs = es_mes_registry().specs
    port = Portfolio(100_000)
    for s in specs.values():
        port.register_spec(s)
    broker = Broker(port, specs, BrokerConfig(slippage_ticks=0))
    t0 = datetime(2021, 3, 12, 16, 0, tzinfo=NY)
    t1 = t0 + timedelta(minutes=30)
    # Seed a long MESH21.
    port.apply_fill(
        Fill(
            fill_id=1,
            order_id=1,
            ts=t0,
            instrument="MES",
            contract="MESH21",
            side=+1,
            quantity=2,
            price=4000.0,
            commission=0.0,
            slippage_ticks=0,
            slippage_paid=0.0,
            liquidity="taker",
            reason="seed",
        )
    )
    out = broker.submit(
        Order(
            order_id=0,
            instrument="MES",
            contract="MESH21",
            side=-1,
            quantity=2,
            order_type=OrderType.MARKET,
            submitted_ts=t0,
            min_bars_before_fill=1,
            meta={"role": "roll_out"},
        )
    )
    inn = broker.submit(
        Order(
            order_id=0,
            instrument="MES",
            contract="MESM21",
            side=+1,
            quantity=2,
            order_type=OrderType.MARKET,
            submitted_ts=t0,
            min_bars_before_fill=1,
            meta={"role": "roll_in"},
        )
    )
    broker.on_bar(bar(t1, "MES", 4000.0, 4001.0, 3999.0, 4000.0, contract="MESH21"))
    broker.on_bar(bar(t1, "MES", 4002.0, 4003.0, 4001.0, 4002.0, contract="MESM21"))
    assert port.position_for("MES", "MESH21").is_flat
    assert port.position_for("MES", "MESM21").quantity == 2
    assert port.open_position_count() == 1
    port.assert_cash_identity()
    # Calendar P&L is real: closed 4000, nothing invented from a continuous gap.
    # Open of new is 4002 with 0 slippage in this test.
    assert out.is_terminal
    assert inn.is_terminal
