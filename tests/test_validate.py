from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from data.validate import validate_frame

NY = ZoneInfo("America/New_York")


def test_impossible_ohlc_is_recorded_not_patched():
    df = pd.DataFrame(
        {
            "ts": [datetime(2021, 3, 1, 10, 0, tzinfo=NY)],
            "instrument": ["ES"],
            "contract": ["ESH21"],
            "open": [10.0],
            "high": [9.0],  # impossible
            "low": [11.0],
            "close": [10.0],
            "volume": [1.0],
        }
    )
    report = validate_frame(df, "ES")
    assert report.impossible_ohlc == 1
    assert any(e["kind"] == "impossible_ohlc" for e in report.exclusions)
    assert any(e["action"] == "recorded_not_patched" for e in report.exclusions)
    # The frame itself is unchanged.
    assert df.loc[0, "high"] == 9.0


def test_duplicates_recorded():
    ts = datetime(2021, 3, 1, 10, 0, tzinfo=NY)
    df = pd.DataFrame(
        {
            "ts": [ts, ts],
            "instrument": ["ES", "ES"],
            "contract": ["ESH21", "ESH21"],
            "open": [10.0, 10.0],
            "high": [11.0, 11.0],
            "low": [9.0, 9.0],
            "close": [10.5, 10.5],
            "volume": [1.0, 1.0],
        }
    )
    report = validate_frame(df, "ES")
    assert report.duplicates == 2
