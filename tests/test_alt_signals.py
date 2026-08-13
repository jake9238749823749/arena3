from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.types import SignalBar
from research.metrics import bootstrap_expectancy
from research.report import conclude_from_suite
from strategy.signals import compute_frs_candidate

NY = ZoneInfo("America/New_York")


def _sb(i: int, o, h, l, c, *, rth=True, session=None) -> SignalBar:
    ts = datetime(2021, 3, 1, 9, 30, tzinfo=NY) + timedelta(minutes=i)
    return SignalBar(
        ts=ts,
        instrument="ES",
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1,
        session_date=session or ts.date(),
        is_rth=rth,
        raw_close=c,
    )


def _history():
    bars = []
    for i in range(48):
        if i < 42:
            bars.append(_sb(i, 100, 102, 98, 100))
        else:
            bars.append(_sb(i, 100, 100.5, 99.5, 100))
    return bars


def test_reversal_fades_a_rejected_probe():
    prior = _history()
    # Probe above 102 and close back inside with a doji (spent energy).
    current = _sb(48, 101.5, 104.0, 101.0, 101.6)
    cand = compute_frs_candidate(
        "ES",
        "ES",
        "MES",
        prior + [current],
        boundary_lookback=12,
        short_atr_period=6,
        long_atr_period=48,
        compression_threshold=0.75,
        energy_threshold=0.65,
        bar_count=49,
        kind="reversal",
        boundary="rolling_n",
    )
    assert cand is not None
    assert cand.direction == -1
    assert cand.kind == "reversal"


def test_reversal_does_not_fire_on_acceptance():
    prior = _history()
    current = _sb(48, 102, 120, 101.5, 118)
    cand = compute_frs_candidate(
        "ES",
        "ES",
        "MES",
        prior + [current],
        boundary_lookback=12,
        short_atr_period=6,
        long_atr_period=48,
        compression_threshold=0.75,
        energy_threshold=0.65,
        bar_count=49,
        kind="reversal",
        boundary="rolling_n",
    )
    assert cand is None


def test_prior_session_uses_yesterday_not_rolling():
    from datetime import date

    bars = []
    d0 = date(2021, 3, 1)
    d1 = date(2021, 3, 2)
    for i in range(48):
        if i < 42:
            bars.append(_sb(i, 100, 102, 98, 100, session=d0))
        else:
            bars.append(_sb(i, 100, 100.5, 99.5, 100, session=d0))
    # Today: rolling upper would be 102, but yesterday's high is 102.
    # Give today a high-energy close through 102.
    bars.append(_sb(48, 102, 106, 101.5, 105.5, session=d1, rth=True))
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
        kind="continuation",
        boundary="prior_session",
    )
    assert cand is not None
    assert cand.upper == 102
    assert cand.boundary == "prior_session"


def test_bootstrap_ci_covers_mean_of_known_sample():
    pnls = [1.0] * 20 + [-1.0] * 20
    boot = bootstrap_expectancy(pnls, n=500, seed=1)
    assert boot["ci_low"] <= 0.0 <= boot["ci_high"]
    assert 0.2 < boot["p_positive"] < 0.8


def test_proxy_negative_is_falsified_on_proxy():
    summary = {
        "dataset": "yahoo_1h",
        "cases": [
            {
                "name": "baseline",
                "expectancy": -5.0,
                "n_trades": 40,
                "concentration_top5_of_wins": 0.2,
                "bootstrap": {"ci_low": -10.0, "ci_high": -1.0},
            }
        ],
        "cost_curve": [{"name": "cost_2", "expectancy": -8.0}],
        "grids": {},
        "walkforward": [],
        "holdout": {"n_trades": 10, "expectancy": -3.0},
    }
    c = conclude_from_suite(summary)
    assert c["verdict"] == "FALSIFIED_ON_PROXY"
    assert "PROXY_NOT_DATED_FUTURES" in c["flags"]
