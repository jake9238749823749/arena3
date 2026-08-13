"""FRS signal primitives.

These functions are pure. They consume only completed bars that have
already been handed to the strategy. The current (breakout) bar is
excluded from boundary and ATR inputs — that is part of the frozen
baseline, not an optional trick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from engine.types import SignalBar


def true_range(current: SignalBar, previous: SignalBar | None) -> float:
    if previous is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def average_true_range(bars: list[SignalBar]) -> float:
    """Wilder-style mean TR matching the QuantConnect prototype.

    The first bar in the window contributes high-low only (no prior
    close). This is a frozen-baseline choice, not a claim about ATR.
    """
    if not bars:
        return 0.0
    trs = [bars[0].high - bars[0].low]
    for previous, current in zip(bars[:-1], bars[1:]):
        trs.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(trs) / len(trs)


def energy(bar: SignalBar) -> float:
    """E = (close - open) / (high - low). 0 if the bar has no range."""
    span = bar.high - bar.low
    if span <= 0:
        return 0.0
    return (bar.close - bar.open) / span


@dataclass
class FRSCandidate:
    name: str
    signal_instrument: str
    trade_instrument: str
    direction: int
    score: float
    energy: float
    compression: float
    atr: float
    signal_price: float
    raw_close: float
    breakout_distance: float
    upper: float
    lower: float
    ts: datetime
    bar_count: int
    meta: dict = field(default_factory=dict)


def compute_frs_candidate(
    name: str,
    signal_instrument: str,
    trade_instrument: str,
    bars: list[SignalBar],
    *,
    boundary_lookback: int,
    short_atr_period: int,
    long_atr_period: int,
    compression_threshold: float,
    energy_threshold: float,
    bar_count: int,
    exclude_current_bar: bool = True,
) -> FRSCandidate | None:
    """Return a continuation candidate or None.

    Requires ``long_atr_period + 1`` bars so the current bar can be
    excluded from the lookbacks.
    """
    need = long_atr_period + 1 if exclude_current_bar else long_atr_period
    if len(bars) < need:
        return None
    current = bars[-1]
    prior = bars[:-1] if exclude_current_bar else bars
    if len(prior) < long_atr_period:
        return None
    if current.high <= current.low:
        return None

    prior_boundary = prior[-boundary_lookback:]
    prior_short = prior[-short_atr_period:]
    prior_long = prior[-long_atr_period:]
    atr_short = average_true_range(prior_short)
    atr_long = average_true_range(prior_long)
    if atr_short <= 0 or atr_long <= 0:
        return None

    compression = atr_short / atr_long
    e = energy(current)
    upper = max(x.high for x in prior_boundary)
    lower = min(x.low for x in prior_boundary)

    direction = 0
    breakout_distance = 0.0
    if current.close > upper and e >= energy_threshold:
        direction = 1
        breakout_distance = current.close - upper
    elif current.close < lower and e <= -energy_threshold:
        direction = -1
        breakout_distance = lower - current.close
    if direction == 0:
        return None
    if compression >= compression_threshold:
        return None

    score = abs(e) * (breakout_distance / atr_long) * max(0.0, 1.0 - compression)
    return FRSCandidate(
        name=name,
        signal_instrument=signal_instrument,
        trade_instrument=trade_instrument,
        direction=direction,
        score=score,
        energy=e,
        compression=compression,
        atr=atr_long,
        signal_price=current.close,
        raw_close=current.raw_close,
        breakout_distance=breakout_distance,
        upper=upper,
        lower=lower,
        ts=current.ts,
        bar_count=bar_count,
        meta={
            "atr_short": atr_short,
            "atr_long": atr_long,
            "range": current.high - current.low,
        },
    )
