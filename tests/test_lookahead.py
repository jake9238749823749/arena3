"""Prove that completed bars cannot fill at their own close and that
future bars cannot influence earlier decisions.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from engine.clock import Clock
from engine.types import LookAheadError
from tests.helpers import bar, compressed_breakout_bars, make_engine, pair_bars

NY = ZoneInfo("America/New_York")


@pytest.mark.lookahead
def test_clock_refuses_to_move_backwards():
    c = Clock()
    t0 = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
    c.start(t0)
    with pytest.raises(LookAheadError):
        c.advance(t0 - timedelta(minutes=1))


@pytest.mark.lookahead
def test_signal_bar_cannot_produce_a_fill():
    bars = compressed_breakout_bars(after=1)
    # The breakout is at minute n_prior = 48 after 9:30 → 10:18
    start = datetime(2021, 3, 1, 9, 30, tzinfo=NY)
    signal_ts = start + timedelta(minutes=48)
    engine = make_engine()
    result = engine.run(bars)
    fills_on_signal = [f for f in result.fills if f.ts == signal_ts]
    assert fills_on_signal == []
    # The entry must occur on a strictly later bar.
    entries = [f for f in result.fills if f.reason == "market_next_open"]
    assert entries, "expected an entry fill on the bar after the signal"
    assert all(f.ts > signal_ts for f in entries)


@pytest.mark.lookahead
def test_every_fill_is_strictly_after_its_order():
    bars = compressed_breakout_bars(after=8)
    engine = make_engine()
    result = engine.run(bars)
    by_id = {o.order_id: o for o in result.orders}
    assert result.fills, "fixture should produce fills"
    for fill in result.fills:
        order = by_id[fill.order_id]
        protect = bool(order.meta.get("protect_entry_bar"))
        if protect:
            assert fill.ts >= order.submitted_ts
        else:
            assert fill.ts > order.submitted_ts


@pytest.mark.lookahead
def test_future_breakout_does_not_create_an_earlier_signal():
    """A monster bar at the end of the series must not leak into earlier scores."""
    start = datetime(2021, 3, 1, 9, 30, tzinfo=NY)
    bars = []
    # 60 flat bars — no breakout.
    for i in range(60):
        ts = start + timedelta(minutes=i)
        bars.extend(pair_bars(ts, 4000, 4000.25, 3999.75, 4000))
    engine = make_engine()
    quiet = engine.run(bars)
    quiet_entries = [f for f in quiet.fills if f.reason == "market_next_open"]

    # Append a huge future bar and rerun. Trades up to minute 59 must match.
    future = start + timedelta(minutes=60)
    bars2 = list(bars) + pair_bars(future, 4000, 5000, 3999, 4999)
    engine2 = make_engine()
    with_future = engine2.run(bars2)
    early_entries = [f for f in with_future.fills if f.ts <= start + timedelta(minutes=59) and f.reason == "market_next_open"]
    assert len(early_entries) == len(quiet_entries)
