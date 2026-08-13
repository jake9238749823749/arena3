from datetime import datetime
from zoneinfo import ZoneInfo

from engine.fills import Fill
from engine.portfolio import Portfolio
from tests.helpers import es_mes_registry

NY = ZoneInfo("America/New_York")
T0 = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
T1 = datetime(2021, 3, 1, 10, 30, tzinfo=NY)


def _fill(fid, oid, side, qty, price, ts, commission=0.62, slip=1.25):
    return Fill(
        fill_id=fid,
        order_id=oid,
        ts=ts,
        instrument="MES",
        contract="MESH21",
        side=side,
        quantity=qty,
        price=price,
        commission=commission,
        slippage_ticks=1,
        slippage_paid=slip,
        liquidity="taker",
        reason="test",
    )


def test_multiplier_drives_pnl():
    """1 MES long, +4 ticks = 1.00 point * $5 = $5 gross."""
    spec = es_mes_registry()["MES"]
    port = Portfolio(100_000.0)
    port.register_spec(spec)
    port.apply_fill(_fill(1, 1, +1, 1, 4000.00, T0, commission=0.62, slip=1.25))
    port.apply_fill(_fill(2, 2, -1, 1, 4001.00, T1, commission=0.62, slip=1.25))
    assert len(port.trades) == 1
    # variation 5.00, commissions 1.24
    assert abs(port.trades[0].pnl - (5.00 - 1.24)) < 1e-9
    port.assert_cash_identity()
    assert abs(port.cash - (100_000.0 + 5.00 - 1.24)) < 1e-9


def test_unrealized_then_realized_reconcile():
    spec = es_mes_registry()["MES"]
    port = Portfolio(50_000.0)
    port.register_spec(spec)
    port.apply_fill(_fill(1, 1, +1, 2, 4000.0, T0, commission=1.24, slip=2.50))
    port.set_mark("MES", 4010.0)
    # 2 * 10 points * $5 = $100
    assert abs(port.unrealized_pnl() - 100.0) < 1e-9
    assert abs(port.equity - (50_000.0 - 1.24 + 100.0)) < 1e-9
    port.apply_fill(_fill(2, 2, -1, 2, 4010.0, T1, commission=1.24, slip=2.50))
    port.assert_cash_identity()
    assert port.is_flat()
    assert abs(port.realized_pnl - 100.0) < 1e-9


def test_no_phantom_position_after_flat():
    spec = es_mes_registry()["MES"]
    port = Portfolio(10_000.0)
    port.register_spec(spec)
    port.apply_fill(_fill(1, 1, -1, 1, 4000.0, T0))
    port.apply_fill(_fill(2, 2, +1, 1, 4000.0, T1))
    assert port.is_flat()
    assert port.open_position_count() == 0
    assert "MES" not in port.open_trades
