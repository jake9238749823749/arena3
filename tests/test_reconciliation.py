from engine.engine import EngineConfig
from tests.helpers import compressed_breakout_bars, es_mes_registry, make_engine


def test_cash_identity_after_full_frs_path():
    result = make_engine().run(compressed_breakout_bars(after=8))
    snap = result.final_snapshot
    assert snap["identity"] < 1e-6
    # Equity equals cash + unrealized by construction.
    if result.equity:
        last = result.equity[-1]
        assert abs(last.equity - (last.cash + last.unrealized)) < 1e-6


def test_commissions_and_slippage_are_explicit():
    cfg_ticks = EngineConfig(slippage_ticks=3, signal_to_fill_bars=1)
    from engine.engine import Engine
    from strategy.frs import FRSStrategy
    from tests.helpers import baseline_cfg

    engine = Engine(FRSStrategy(baseline_cfg()), es_mes_registry(), cfg_ticks)
    result = engine.run(compressed_breakout_bars(after=8))
    if result.fills:
        assert all(f.commission > 0 for f in result.fills)
        assert any(f.slippage_paid > 0 for f in result.fills)


def test_zero_slippage_differs_from_three_ticks():
    from engine.engine import Engine
    from strategy.frs import FRSStrategy
    from tests.helpers import baseline_cfg

    bars = compressed_breakout_bars(after=8)
    a = Engine(FRSStrategy(baseline_cfg()), es_mes_registry(), EngineConfig(slippage_ticks=0)).run(bars)
    b = Engine(FRSStrategy(baseline_cfg()), es_mes_registry(), EngineConfig(slippage_ticks=3)).run(bars)
    slip_a = sum(f.slippage_paid for f in a.fills)
    slip_b = sum(f.slippage_paid for f in b.fills)
    assert slip_b > slip_a
