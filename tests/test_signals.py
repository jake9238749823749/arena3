from datetime import datetime
from zoneinfo import ZoneInfo

from engine.types import SignalBar
from strategy.signals import average_true_range, compute_frs_candidate, energy

NY = ZoneInfo("America/New_York")


def _sb(i: int, o, h, l, c) -> SignalBar:
    return SignalBar(
        ts=datetime(2021, 3, 1, 9, 30, tzinfo=NY).replace(minute=min(59, 30), second=i % 60),
        instrument="ES",
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1,
        session_date=None,
        is_rth=True,
        raw_close=c,
    )


def test_energy_formula():
    b = _sb(0, 10, 12, 8, 11)
    assert abs(energy(b) - (11 - 10) / (12 - 8)) < 1e-12


def test_energy_zero_range():
    b = _sb(0, 10, 10, 10, 10)
    assert energy(b) == 0.0


def test_atr_first_bar_is_high_low_only():
    a = _sb(0, 10, 12, 8, 11)
    b = _sb(1, 11, 15, 10, 14)
    # first TR = 4, second = max(5, |15-11|, |10-11|) = 5
    assert average_true_range([a, b]) == (4 + 5) / 2


def test_current_bar_excluded_from_boundary():
    """If the current bar's high were in the boundary, it could not break itself."""
    prior = []
    for i in range(48):
        if i < 42:
            prior.append(_sb(i, 100, 102, 98, 100))
        else:
            prior.append(_sb(i, 100, 100.5, 99.5, 100))
    # Current bar trades far above the prior max high of 102.
    current = _sb(48, 102, 120, 101.5, 118)
    bars = prior + [current]
    cand = compute_frs_candidate(
        "ES",
        "ES",
        "MES",
        bars,
        boundary_lookback=12,
        short_atr_period=6,
        long_atr_period=48,
        compression_threshold=0.75,
        energy_threshold=0.65,
        bar_count=49,
        exclude_current_bar=True,
    )
    assert cand is not None
    assert cand.direction == 1
    assert cand.upper == 102  # not 120
    # With the current bar included, upper becomes 120 and close 118 is not a break.
    leaked = compute_frs_candidate(
        "ES",
        "ES",
        "MES",
        bars,
        boundary_lookback=12,
        short_atr_period=6,
        long_atr_period=48,
        compression_threshold=0.75,
        energy_threshold=0.65,
        bar_count=49,
        exclude_current_bar=False,
    )
    assert leaked is None
