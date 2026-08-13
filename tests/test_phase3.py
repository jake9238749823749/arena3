from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from data.consolidate import consolidate
from engine.broker import Broker, BrokerConfig
from engine.fills import Fill
from engine.orders import Order, OrderType
from engine.portfolio import Portfolio
from research.metrics import block_bootstrap_expectancy, deflated_sharpe, permutation_expectancy
from research.opportunity import analyze_rejected
from tests.helpers import bar, es_mes_registry

NY = ZoneInfo("America/New_York")


def test_target_first_fills_limit_when_both_touched():
    specs = es_mes_registry().specs
    port = Portfolio(100_000)
    for s in specs.values():
        port.register_spec(s)
    broker = Broker(port, specs, BrokerConfig(slippage_ticks=1, intrabar_ambiguity="target_first"))
    t0 = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
    t1 = t0 + timedelta(minutes=1)
    port.apply_fill(
        Fill(
            fill_id=0,
            order_id=0,
            ts=t0,
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
    stop = broker.submit(
        Order(
            order_id=0,
            instrument="MES",
            contract="MESH21",
            side=-1,
            quantity=1,
            order_type=OrderType.STOP_MARKET,
            submitted_ts=t0,
            stop_price=3990.0,
            oco_group="g1",
            min_bars_before_fill=1,
            meta={"role": "stop"},
        )
    )
    target = broker.submit(
        Order(
            order_id=0,
            instrument="MES",
            contract="MESH21",
            side=-1,
            quantity=1,
            order_type=OrderType.LIMIT,
            submitted_ts=t0,
            limit_price=4020.0,
            oco_group="g1",
            min_bars_before_fill=1,
            meta={"role": "target"},
        )
    )
    fills = broker.on_bar(bar(t1, "MES", 4000.0, 4030.0, 3980.0, 4010.0))
    assert any(f.order_id == target.order_id for f in fills)
    assert not any(f.order_id == stop.order_id for f in fills)
    assert fills[0].reason == "oco_target_first"


def test_consolidate_30m_from_1m():
    start = datetime(2021, 3, 1, 10, 1, tzinfo=NY)
    bars = []
    px = 4000.0
    for i in range(30):
        ts = start + timedelta(minutes=i)
        bars.append(bar(ts, "ES", px, px + 1, px - 1, px + 0.25, contract="ESH21"))
        px += 0.25
    out = consolidate(bars, 30)
    assert len(out) == 1
    assert out[0].ts == datetime(2021, 3, 1, 10, 30, tzinfo=NY)
    assert out[0].open == 4000.0
    assert out[0].close == 4007.5
    assert out[0].high >= out[0].close


def test_block_bootstrap_and_permutation_on_known_sample():
    pnls = [10.0] * 20 + [-10.0] * 20
    block = block_bootstrap_expectancy(pnls, block=4, n=300, seed=1)
    assert block["ci_low"] <= 0.0 <= block["ci_high"]
    perm = permutation_expectancy(pnls, n=400, seed=1)
    assert perm["p_value"] is not None
    assert perm["p_value"] > 0.2


def test_deflated_sharpe_penalizes_many_trials():
    one = deflated_sharpe(1.5, n_obs=80, n_trials=1)
    many = deflated_sharpe(1.5, n_obs=80, n_trials=40)
    assert many["expected_max_sr"] > (one["expected_max_sr"] or 0)
    assert many["prob_skill"] < one["prob_skill"]


def test_opportunity_cost_counts_reasons():
    rejected = [
        {"reason": "outranked", "score": 1.2, "name": "ES", "winner": "NQ"},
        {"reason": "blocked_by_position", "score": 0.8, "name": "GC"},
        {"reason": "outranked", "score": 0.4, "name": "GC", "winner": "NQ"},
    ]
    rec = analyze_rejected(rejected)
    assert rec["n_rejected"] == 3
    assert rec["n_outranked"] == 2
    assert rec["n_blocked_by_position"] == 1
