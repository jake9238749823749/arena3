"""Shared builders for deterministic engine tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.engine import Engine, EngineConfig
from engine.types import Bar
from futures.contracts import InstrumentRegistry, InstrumentSpec
from strategy.frs import FRSStrategy

NY = ZoneInfo("America/New_York")


def spec(symbol: str, role: str, multiplier: float, tick: float, commission: float, **extra) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=symbol,
        name=symbol,
        exchange="CME",
        asset_class="index",
        role=role,
        currency="USD",
        multiplier=multiplier,
        tick_size=tick,
        tick_value=multiplier * tick,
        contract_months=("H", "M", "U", "Z"),
        cycle="quarterly",
        settlement="financial",
        rth_start="09:30",
        rth_end="16:00",
        commission_per_side=commission,
        **extra,
    )


def es_mes_registry() -> InstrumentRegistry:
    return InstrumentRegistry(
        {
            "ES": spec("ES", "signal", 50.0, 0.25, 2.25, related_micro="MES"),
            "MES": spec("MES", "trade", 5.0, 0.25, 0.62, related_full="ES"),
            "NQ": spec("NQ", "signal", 20.0, 0.25, 2.25, related_micro="MNQ"),
            "MNQ": spec("MNQ", "trade", 2.0, 0.25, 0.62, related_full="NQ"),
            "GC": spec("GC", "signal", 100.0, 0.10, 2.50, related_micro="MGC"),
            "MGC": spec("MGC", "trade", 10.0, 0.10, 0.50, related_full="GC"),
        }
    )


def baseline_cfg(**overrides) -> dict:
    cfg = {
        "definition_id": "frs_baseline_v1",
        "account": {"starting_cash": 100_000.0, "currency": "USD", "timezone": "America/New_York", "max_positions": 1},
        "session": {"entry_start": "09:30", "entry_cutoff": "13:00", "force_flat": "15:45"},
        "signal": {
            "bar_minutes": 30,
            "series": "continuous_backward_ratio",
            "boundary_lookback": 12,
            "short_atr_period": 6,
            "long_atr_period": 48,
            "compression_threshold": 0.75,
            "energy_threshold": 0.65,
            "exclude_current_bar": True,
        },
        "execution": {
            "pairs": [{"name": "ES", "signal": "ES", "trade": "MES"}],
            "delay": "next_bar",
            "signal_to_fill_bars": 1,
            "slippage_ticks": 1,
            "intrabar_ambiguity": "worst_case",
            "limit_fill_on_touch": True,
            "stop_gap_policy": "worse_of_open_and_stop",
        },
        "exits": {"stop_atr": 1.0, "target_atr": 2.0, "max_hold_bars": 8, "flatten_on_roll": True},
        "sizing": {
            "method": "percent_equity_risk",
            "risk_fraction": 0.0025,
            "notional_cap": 1.0,
            "fixed_contracts": 1,
            "fixed_dollar_risk": 250.0,
            "vol_target_annual": 0.10,
        },
        "research": {"seed": 42},
    }
    cfg.update(overrides)
    return cfg


def bar(
    ts: datetime,
    instrument: str,
    o: float,
    h: float,
    l: float,
    c: float,
    contract: str | None = None,
) -> Bar:
    contract = contract or ("MESH21" if instrument == "MES" else "ESH21")
    return Bar(
        ts=ts,
        instrument=instrument,
        contract=contract,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000,
        session_date=ts.date(),
        is_rth=True,
        cont_open=o,
        cont_high=h,
        cont_low=l,
        cont_close=c,
    )


def pair_bars(ts: datetime, o: float, h: float, l: float, c: float) -> list[Bar]:
    return [bar(ts, "ES", o, h, l, c), bar(ts, "MES", o, h, l, c)]


def compressed_breakout_bars(
    *,
    n_prior: int = 48,
    start: datetime | None = None,
    direction: int = 1,
    after: int = 6,
) -> list[Bar]:
    """Quiet-after-wide history plus a high-energy breakout. Guaranteed FRS setup."""
    start = start or datetime(2021, 3, 1, 9, 30, tzinfo=NY)
    bars: list[Bar] = []
    px = 4000.0
    for i in range(n_prior):
        ts = start + timedelta(minutes=i)
        # First 42 bars wide, last 6 compressed — short ATR << long ATR.
        if i < n_prior - 6:
            o = h = l = c = None  # noqa: F841
            o, c = px, px
            h, l = px + 2.0, px - 2.0
        else:
            o, c = px, px
            h, l = px + 0.50, px - 0.50
        bars.extend(pair_bars(ts, o, h, l, c))
    ts = start + timedelta(minutes=n_prior)
    if direction > 0:
        o, h, l, c = 4002.0, 4006.0, 4001.75, 4005.50
    else:
        o, h, l, c = 3998.0, 3998.25, 3994.0, 3994.50
    bars.extend(pair_bars(ts, o, h, l, c))
    last = c
    for j in range(1, after + 1):
        ts = start + timedelta(minutes=n_prior + j)
        step = 1.5 * direction
        o = last
        c = last + step
        if direction > 0:
            h, l = c + 0.25, o - 0.25
        else:
            h, l = o + 0.25, c - 0.25
        bars.extend(pair_bars(ts, o, h, l, c))
        last = c
    return bars


def make_engine(cfg: dict | None = None, **engine_kwargs) -> Engine:
    cfg = cfg or baseline_cfg()
    econf = EngineConfig(
        starting_cash=cfg["account"]["starting_cash"],
        max_positions=cfg["account"]["max_positions"],
        slippage_ticks=cfg["execution"]["slippage_ticks"],
        signal_to_fill_bars=cfg["execution"]["signal_to_fill_bars"],
        **engine_kwargs,
    )
    return Engine(FRSStrategy(cfg), es_mes_registry(), econf)
