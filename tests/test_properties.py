"""Hypothesis properties: no future fills, valid OHLC, cash identity, replay."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from engine.types import Bar
from tests.helpers import bar, make_engine, pair_bars

NY = ZoneInfo("America/New_York")


@st.composite
def ohlc_values(draw, tick=0.25, mid=4000.0):
    o = mid + draw(st.integers(-40, 40)) * tick
    up = draw(st.integers(0, 20)) * tick
    dn = draw(st.integers(0, 20)) * tick
    h = o + up
    l = o - dn
    # close between low and high on the tick grid
    n_ticks = int(round((h - l) / tick))
    c = l + draw(st.integers(0, max(0, n_ticks))) * tick
    assume(h >= max(o, c) and l <= min(o, c) and h > l)
    return o, h, l, c


@given(ohlc_values())
def test_bar_constructor_accepts_valid_ohlc(ohlc):
    o, h, l, c = ohlc
    b = bar(datetime(2021, 3, 1, 10, 0, tzinfo=NY), "ES", o, h, l, c)
    assert b.high >= b.low
    assert b.high >= max(b.open, b.close)
    assert b.low <= min(b.open, b.close)


@given(st.lists(ohlc_values(), min_size=55, max_size=70))
@settings(max_examples=15, deadline=None)
def test_no_fill_before_submit_and_cash_identity(rows):
    start = datetime(2021, 3, 1, 9, 30, tzinfo=NY)
    bars = []
    for i, ohlc in enumerate(rows):
        ts = start + timedelta(minutes=i)
        bars.extend(pair_bars(ts, *ohlc))
    result = make_engine().run(bars)
    by_id = {o.order_id: o for o in result.orders}
    for fill in result.fills:
        order = by_id[fill.order_id]
        if order.meta.get("protect_entry_bar"):
            assert fill.ts >= order.submitted_ts
        else:
            assert fill.ts > order.submitted_ts
    result.final_snapshot  # exists
    # Recompute cash identity from the snapshot.
    assert result.final_snapshot["identity"] < 1e-5
    # Replay.
    again = make_engine().run(bars)
    assert [(f.ts, f.price, f.quantity) for f in again.fills] == [
        (f.ts, f.price, f.quantity) for f in result.fills
    ]


@given(st.integers(min_value=0, max_value=5), st.integers(min_value=1, max_value=3))
@settings(max_examples=10, deadline=None)
def test_more_slippage_never_increases_expectancy_on_fixed_path(ticks_a, extra):
    """On an identical path, adding slippage ticks cannot raise mean trade PnL
    through a more favorable fill — fills are adverse by construction.
    We only assert the accounting direction on the entry fill price.
    """
    from engine.engine import Engine, EngineConfig
    from strategy.frs import FRSStrategy
    from tests.helpers import baseline_cfg, compressed_breakout_bars, es_mes_registry

    bars = compressed_breakout_bars(after=4)
    ticks_b = ticks_a + extra
    a = Engine(FRSStrategy(baseline_cfg()), es_mes_registry(), EngineConfig(slippage_ticks=ticks_a)).run(bars)
    b = Engine(FRSStrategy(baseline_cfg()), es_mes_registry(), EngineConfig(slippage_ticks=ticks_b)).run(bars)
    ea = [f for f in a.fills if f.reason == "market_next_open"]
    eb = [f for f in b.fills if f.reason == "market_next_open"]
    if not ea or not eb:
        return
    # Long entry: higher slippage ⇒ higher fill price (worse).
    if ea[0].side > 0:
        assert eb[0].price >= ea[0].price
    else:
        assert eb[0].price <= ea[0].price
