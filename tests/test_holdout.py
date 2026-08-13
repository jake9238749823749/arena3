from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research.backtest import slice_holdout
from tests.helpers import bar

NY = ZoneInfo("America/New_York")


def test_holdout_is_the_final_fraction_and_does_not_overlap():
    start = datetime(2021, 1, 1, 10, 0, tzinfo=NY)
    bars = [bar(start + timedelta(days=i), "ES", 10, 11, 9, 10) for i in range(100)]
    is_ = slice_holdout(bars, 0.20, "is")
    oos = slice_holdout(bars, 0.20, "oos")
    assert is_
    assert oos
    assert max(b.ts for b in is_) < min(b.ts for b in oos)
    # 20% of 100 unique stamps → about 20 OOS bars
    assert 15 <= len(oos) <= 25
    assert len(is_) + len(oos) == 100
