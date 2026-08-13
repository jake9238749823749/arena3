from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.helpers import compressed_breakout_bars, make_engine, pair_bars

NY = ZoneInfo("America/New_York")


def test_frozen_baseline_takes_the_planted_breakout():
    result = make_engine().run(compressed_breakout_bars(after=8))
    assert result.trades or result.fills
    entries = [f for f in result.fills if f.reason == "market_next_open"]
    assert entries, "planted breakout must produce at least one entry"
    assert entries[0].side == 1
    assert entries[0].instrument == "MES"


def test_short_breakout_is_symmetric():
    result = make_engine().run(compressed_breakout_bars(direction=-1, after=8))
    entries = [f for f in result.fills if f.reason == "market_next_open"]
    assert entries
    assert entries[0].side == -1


def test_outside_entry_window_is_ignored():
    start = datetime(2021, 3, 1, 14, 0, tzinfo=NY)  # after 13:00 cutoff
    bars = compressed_breakout_bars(start=start, after=4)
    result = make_engine().run(bars)
    entries = [f for f in result.fills if f.reason == "market_next_open"]
    assert entries == []


def test_one_position_blocks_a_second_market():
    """Simultaneous ES and NQ candidates: only the higher score trades."""
    from engine.engine import Engine, EngineConfig
    from strategy.frs import FRSStrategy
    from tests.helpers import bar, baseline_cfg, es_mes_registry

    cfg = baseline_cfg()
    cfg["execution"]["pairs"] = [
        {"name": "ES", "signal": "ES", "trade": "MES"},
        {"name": "NQ", "signal": "NQ", "trade": "MNQ"},
    ]
    start = datetime(2021, 3, 1, 9, 30, tzinfo=NY)
    bars = []
    for i in range(48):
        ts = start + timedelta(minutes=i)
        if i < 42:
            es = (4000, 4002, 3998, 4000)
            nq = (13000, 13004, 12996, 13000)
        else:
            es = (4000, 4000.5, 3999.5, 4000)
            nq = (13000, 13001, 12999, 13000)
        bars.extend(
            [
                bar(ts, "ES", *es, contract="ESH21"),
                bar(ts, "MES", *es, contract="MESH21"),
                bar(ts, "NQ", *nq, contract="NQH21"),
                bar(ts, "MNQ", *nq, contract="MNQH21"),
            ]
        )
    ts = start + timedelta(minutes=48)
    # Both break out; NQ travels further in ATR units so it should win.
    bars.extend(
        [
            bar(ts, "ES", 4002, 4004, 4001.75, 4003.50, contract="ESH21"),
            bar(ts, "MES", 4002, 4004, 4001.75, 4003.50, contract="MESH21"),
            bar(ts, "NQ", 13004, 13040, 13003, 13035, contract="NQH21"),
            bar(ts, "MNQ", 13004, 13040, 13003, 13035, contract="MNQH21"),
        ]
    )
    ts2 = start + timedelta(minutes=49)
    bars.extend(
        [
            bar(ts2, "ES", 4003.5, 4004, 4003, 4003.75, contract="ESH21"),
            bar(ts2, "MES", 4003.5, 4004, 4003, 4003.75, contract="MESH21"),
            bar(ts2, "NQ", 13035, 13050, 13030, 13045, contract="NQH21"),
            bar(ts2, "MNQ", 13035, 13050, 13030, 13045, contract="MNQH21"),
        ]
    )
    engine = Engine(FRSStrategy(cfg), es_mes_registry(), EngineConfig())
    result = engine.run(bars)
    entries = [f for f in result.fills if f.reason == "market_next_open"]
    assert len(entries) == 1
    assert entries[0].instrument == "MNQ"
    outranked = [r for r in result.rejected_signals if r.get("reason") == "outranked"]
    assert any(r.get("name") == "ES" for r in outranked)


def test_force_flat_submits_exit_at_1545():
    from datetime import time as dtime

    bars = compressed_breakout_bars(after=1)
    engine = make_engine()
    # Inject a 15:45 timer on the same day.
    stamp = datetime(2021, 3, 1, 15, 45, tzinfo=NY)
    engine.schedule_force_flats([stamp])
    # Add a later bar so the flatten can fill.
    last_ts = max(b.ts for b in bars)
    if last_ts < stamp:
        later = stamp + timedelta(minutes=15)
        last = [b for b in bars if b.instrument == "MES"][-1]
        bars = list(bars) + pair_bars(later, last.close, last.close + 0.25, last.close - 0.25, last.close)
    result = engine.run(bars)
    tags = [o.tag for o in result.orders]
    assert any("15:45" in t for t in tags)
