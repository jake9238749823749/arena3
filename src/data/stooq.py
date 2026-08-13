"""Stooq daily continuous futures as a last-resort public proxy.

Daily bars cannot test the 30-minute FRS definition. They *can* ask
whether compressed-range + high-energy breakouts continue on a daily
continuous series. That is `frs_daily_proxy_v1`, not the frozen baseline.
"""

from __future__ import annotations

import io
from urllib.request import Request, urlopen

import pandas as pd

STOOQ = {
    "ES": "es.f",
    "NQ": "nq.f",
    "GC": "gc.f",
}
UA = "frs-research/0.3 (local falsification; daily proxy only)"


def fetch_stooq_daily() -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for root, symbol in STOOQ.items():
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=30) as resp:
            raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        if "Date" not in text.splitlines()[0]:
            raise RuntimeError(f"Stooq did not return a CSV for {symbol}: {text[:120]!r}")
        df = pd.read_csv(io.StringIO(text))
        df.columns = [c.strip().lower() for c in df.columns]
        df["ts"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("America/New_York")
        df["ts"] = df["ts"] + pd.Timedelta(hours=16)  # stamp at RTH-ish close
        out[root] = df[["ts", "open", "high", "low", "close", "volume"]].dropna()
    return out
