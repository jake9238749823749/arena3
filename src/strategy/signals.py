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
    kind: str = "continuation"
    boundary: str = "rolling_n"
    meta: dict = field(default_factory=dict)


def rolling_boundary(prior: list[SignalBar], lookback: int) -> tuple[float, float]:
    window = prior[-lookback:]
    return max(x.high for x in window), min(x.low for x in window)


def prior_session_boundary(prior: list[SignalBar], current: SignalBar) -> tuple[float, float] | None:
    """Previous CME session's high/low. Requires session_date on bars."""
    if current.session_date is None:
        return None
    prev = [b for b in prior if b.session_date is not None and b.session_date < current.session_date]
    if not prev:
        return None
    last_date = max(b.session_date for b in prev if b.session_date is not None)
    sess = [b for b in prev if b.session_date == last_date]
    return max(b.high for b in sess), min(b.low for b in sess)


def overnight_boundary(prior: list[SignalBar], current: SignalBar) -> tuple[float, float] | None:
    """Overnight / ETH extremes of the current session before RTH."""
    if current.session_date is None:
        return None
    eth = [
        b
        for b in prior
        if b.session_date == current.session_date and not b.is_rth
    ]
    if len(eth) < 2:
        return None
    return max(b.high for b in eth), min(b.low for b in eth)


def _levels(
    prior: list[SignalBar],
    current: SignalBar,
    *,
    boundary: str,
    lookback: int,
) -> tuple[float, float] | None:
    if boundary == "rolling_n":
        return rolling_boundary(prior, lookback)
    if boundary == "prior_session":
        return prior_session_boundary(prior, current)
    if boundary == "overnight":
        return overnight_boundary(prior, current)
    raise ValueError(f"unknown boundary {boundary!r}")


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
    kind: str = "continuation",
    boundary: str = "rolling_n",
) -> FRSCandidate | None:
    """Return a candidate or None.

    ``kind='continuation'`` + ``boundary='rolling_n'`` is the frozen baseline.
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

    prior_short = prior[-short_atr_period:]
    prior_long = prior[-long_atr_period:]
    atr_short = average_true_range(prior_short)
    atr_long = average_true_range(prior_long)
    if atr_short <= 0 or atr_long <= 0:
        return None

    compression = atr_short / atr_long
    e = energy(current)
    levels = _levels(prior, current, boundary=boundary, lookback=boundary_lookback)
    if levels is None:
        return None
    upper, lower = levels

    if kind == "continuation":
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
    elif kind == "reversal":
        spent = compression >= compression_threshold or abs(e) <= (1.0 - energy_threshold)
        direction = 0
        breakout_distance = 0.0
        if current.high > upper and current.close < upper and spent:
            direction = -1
            breakout_distance = current.high - upper
        elif current.low < lower and current.close > lower and spent:
            direction = 1
            breakout_distance = lower - current.low
        if direction == 0 or breakout_distance <= 0:
            return None
        score = (1.0 - abs(e)) * (breakout_distance / atr_long) * max(compression, 0.0)
    else:
        raise ValueError(f"unknown signal kind {kind!r}")

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
        kind=kind,
        boundary=boundary,
        meta={
            "atr_short": atr_short,
            "atr_long": atr_long,
            "range": current.high - current.low,
            "kind": kind,
            "boundary": boundary,
        },
    )
