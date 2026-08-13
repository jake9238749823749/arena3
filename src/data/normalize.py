"""Column names, dtypes, and timezone normalization.

Raw files are never modified. Normalization writes a new Parquet dataset
and a sidecar JSON describing every exclusion.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")

REQUIRED_COLUMNS = (
    "ts",
    "instrument",
    "contract",
    "open",
    "high",
    "low",
    "close",
    "volume",
)

OPTIONAL_COLUMNS = (
    "open_interest",
    "cont_open",
    "cont_high",
    "cont_low",
    "cont_close",
    "session_date",
    "is_rth",
)

COLUMN_ALIASES = {
    "timestamp": "ts",
    "time": "ts",
    "datetime": "ts",
    "date": "ts",
    "symbol": "instrument",
    "root": "instrument",
    "ticker": "instrument",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "vol": "volume",
    "oi": "open_interest",
}


def apply_aliases(columns: list[str]) -> list[str]:
    out = []
    for c in columns:
        key = c.strip().lower()
        out.append(COLUMN_ALIASES.get(key, key))
    return out


def ensure_ny_timestamp(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, utc=False)
    if ts.dt.tz is None:
        # Ambiguous vendor dumps are treated as America/New_York and flagged
        # by the validator — we do not silently assume UTC.
        ts = ts.dt.tz_localize(NY, ambiguous="infer", nonexistent="shift_forward")
    else:
        ts = ts.dt.tz_convert(NY)
    return ts


def to_aware_iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=NY)
    return ts.isoformat()
