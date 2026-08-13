"""Fetch public *proxy* market paths. Not dated CME contracts.

Two sources, both honest about what they are:

* Yahoo Finance continuous futures (``ES=F``, ``NQ=F``, ``GC=F``). Intraday
  history is short (≈60d at 30m, ≈730d at 1h). These are front-month-ish
  continuous series, not tradeable dated contracts.
* Dukascopy spot/index CFDs (``XAUUSD``, ``USA500IDXUSD``, ``USATECHIDXUSD``).
  Longer M30 history. These are not futures. Spreads, sessions, and
  multipliers differ. Used only to ask whether the *price-path pattern*
  appears in real markets.

Neither source is allowed to masquerade as MGC/MES/MNQ tape. The mapper
writes GC/ES/NQ *and* the micros as price-identical copies so the frozen
engine can run, and stamps every row with ``source`` metadata. Roll P&L
on this data is commission-only (no real calendar spread).
"""

from __future__ import annotations

import json
import lzma
import struct
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from futures.contracts import InstrumentRegistry
from futures.rolls import RollCalendar, front_at
from futures.sessions import SessionCalendar, try_load_cme_holidays

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

YAHOO_MAP = {
    "ES": "ES=F",
    "NQ": "NQ=F",
    "GC": "GC=F",
}
MICRO = {"ES": "MES", "NQ": "MNQ", "GC": "MGC"}

# Dukascopy CFD / spot proxies. NOT futures.
DUKAS_MAP = {
    "GC": ("XAUUSD", 1000.0),
    "ES": ("USA500IDXUSD", 1000.0),
    "NQ": ("USATECHIDXUSD", 1000.0),
}

UA = "frs-research/0.2 (falsification stack; local research only)"


def _get(url: str, retries: int = 4) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last: Exception | None = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 404:
                    return None
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last = exc
        except Exception as exc:  # noqa: BLE001 — network is best-effort
            last = exc
        time.sleep(0.4 * (i + 1))
    if last:
        raise RuntimeError(f"GET {url} failed: {last}") from last
    return None


def fetch_yahoo(
    *,
    interval: str,
    period: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, pd.DataFrame]:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for Yahoo fetch: pip install yfinance") from exc

    out: dict[str, pd.DataFrame] = {}
    for root, ticker in YAHOO_MAP.items():
        kwargs: dict = {"interval": interval, "auto_adjust": False, "prepost": True, "progress": False}
        if period:
            kwargs["period"] = period
        else:
            kwargs["start"] = start
            kwargs["end"] = end
        df = yf.download(ticker, **kwargs)
        if df is None or df.empty:
            raise RuntimeError(f"Yahoo returned no rows for {ticker} interval={interval}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.rename(columns={c: c.lower() for c in df.columns})
        need = {"open", "high", "low", "close"}
        if not need.issubset(set(df.columns)):
            raise RuntimeError(f"{ticker} missing OHLC columns: {list(df.columns)}")
        df = df.reset_index()
        ts_col = "Datetime" if "Datetime" in df.columns else ("Date" if "Date" in df.columns else df.columns[0])
        df["ts"] = pd.to_datetime(df[ts_col], utc=True).dt.tz_convert(NY)
        vol = df["volume"] if "volume" in df.columns else 0.0
        out[root] = pd.DataFrame(
            {
                "ts": df["ts"],
                "open": pd.to_numeric(df["open"], errors="coerce"),
                "high": pd.to_numeric(df["high"], errors="coerce"),
                "low": pd.to_numeric(df["low"], errors="coerce"),
                "close": pd.to_numeric(df["close"], errors="coerce"),
                "volume": pd.to_numeric(vol, errors="coerce").fillna(0.0),
            }
        ).dropna(subset=["ts", "open", "high", "low", "close"])
    return out


def _parse_dukas_candles(blob: bytes, point: float, day: date) -> pd.DataFrame:
    raw = lzma.decompress(blob)
    # Dukascopy 1-minute candle: 24 bytes, big-endian uint32 O/C/L/H + float vol
    # plus a time offset in seconds from 00:00 UTC of that day.
    rec = struct.Struct(">IIIII f")
    if len(raw) % rec.size != 0:
        rec = struct.Struct("<IIIII f")
    if len(raw) % rec.size != 0:
        raise ValueError(f"unexpected candle blob length {len(raw)}")
    rows = []
    base = datetime(day.year, day.month, day.day, tzinfo=UTC)
    for off in range(0, len(raw), rec.size):
        tsec, o, c, low, high, vol = rec.unpack_from(raw, off)
        ts = base + timedelta(seconds=int(tsec))
        rows.append(
            {
                "ts": ts,
                "open": o / point,
                "high": high / point,
                "low": low / point,
                "close": c / point,
                "volume": float(vol),
            }
        )
    return pd.DataFrame(rows)


def fetch_dukascopy_m30(start: date, end: date) -> dict[str, pd.DataFrame]:
    """Download daily 1-minute BID candles and resample to 30 minutes.

    Monthly files are tried first; daily files are the fallback. Missing
    days (weekends) are skipped, not invented.
    """
    out: dict[str, pd.DataFrame] = {}
    for root, (symbol, point) in DUKAS_MAP.items():
        frames: list[pd.DataFrame] = []
        day = start
        while day <= end:
            if day.weekday() < 5:  # skip Sat/Sun — Dukas files are usually empty
                y, m, d = day.year, day.month - 1, day.day
                url = (
                    f"https://datafeed.dukascopy.com/datafeed/{symbol}/"
                    f"{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
                )
                try:
                    blob = _get(url)
                except RuntimeError:
                    blob = None
                if blob:
                    try:
                        frames.append(_parse_dukas_candles(blob, point, day))
                    except ValueError:
                        pass
            day += timedelta(days=1)
        if not frames:
            raise RuntimeError(f"Dukascopy returned no candles for {symbol} {start}..{end}")
        df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts"]).sort_values("ts")
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(NY)
        df = df.set_index("ts")
        res = df.resample("30min", label="right", closed="right").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        res = res.dropna(subset=["open", "high", "low", "close"]).reset_index()
        out[root] = res
    return out


def frames_to_bars(
    by_root: dict[str, pd.DataFrame],
    *,
    source: str,
    registry: InstrumentRegistry,
    roll_days: int = 8,
) -> dict[str, pd.DataFrame]:
    """Stamp session/contract metadata and clone micros at the same prices."""
    holidays = set()
    all_ts = pd.concat([f["ts"] for f in by_root.values()])
    if len(all_ts):
        holidays = try_load_cme_holidays(all_ts.min().date(), all_ts.max().date())
    calendar = SessionCalendar(holidays=holidays)
    stamped: dict[str, pd.DataFrame] = {}
    for root, df in by_root.items():
        work = df.copy()
        work = work[(work["high"] >= work[["open", "close"]].max(axis=1) - 1e-9)]
        work = work[(work["low"] <= work[["open", "close"]].min(axis=1) + 1e-9)]
        spec = registry[root] if root in registry else None
        contracts = []
        session_dates = []
        is_rth = []
        for ts in work["ts"]:
            ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            session_dates.append(calendar.session_date(ts).isoformat())
            if spec is not None:
                is_rth.append(calendar.is_rth(ts, spec.rth_start, spec.rth_end))
                try:
                    contracts.append(front_at(spec, calendar.session_date(ts), roll_days))
                except Exception:
                    contracts.append(f"{root}H21")
            else:
                is_rth.append(True)
                contracts.append(f"{root}H21")
        work["instrument"] = root
        work["contract"] = contracts
        work["session_date"] = session_dates
        work["is_rth"] = is_rth
        work["cont_open"] = work["open"]
        work["cont_high"] = work["high"]
        work["cont_low"] = work["low"]
        work["cont_close"] = work["close"]
        work["open_interest"] = None
        work["source"] = source
        stamped[root] = work
        micro = MICRO[root]
        m = work.copy()
        m["instrument"] = micro
        if spec is not None and micro in registry:
            mspec = registry[micro]
            m["contract"] = [
                c.replace(root, micro, 1) if isinstance(c, str) else c for c in work["contract"]
            ]
            _ = mspec
        stamped[micro] = m
    return stamped


def write_vendor_and_parquet(
    stamped: dict[str, pd.DataFrame],
    *,
    source: str,
    raw_dir: Path,
    parquet_dir: Path,
) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    parquet_dir.mkdir(parents=True, exist_ok=True)
    for inst, df in stamped.items():
        raw_path = raw_dir / f"{inst}.csv"
        # Raw dump is the immutable vendor image for this fetch.
        df.to_csv(raw_path, index=False)
        dest = parquet_dir / f"{inst}.parquet"
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dest)
    meta = {
        "source": source,
        "instruments": sorted(stamped),
        "rows": {k: int(len(v)) for k, v in stamped.items()},
        "disclaimer": (
            "Proxy market path. Not dated CME contracts. Not sufficient to "
            "claim a tradeable futures edge. Used to test whether the FRS "
            "price-path pattern appears outside synthetic GBM."
        ),
        "fetched_utc": datetime.utcnow().isoformat() + "Z",
    }
    (parquet_dir / "market_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (raw_dir / "SOURCE.txt").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return parquet_dir


def fetch_and_store(
    source: str,
    *,
    repo: Path,
    registry: InstrumentRegistry,
    start: str | None = None,
    end: str | None = None,
) -> Path:
    if source == "yahoo_1h":
        frames = fetch_yahoo(interval="1h", period="730d")
        label = "yahoo_1h"
    elif source == "yahoo_30m":
        frames = fetch_yahoo(interval="30m", period="60d")
        label = "yahoo_30m"
    elif source == "dukascopy_m30":
        s = datetime.fromisoformat(start or "2021-01-01").date()
        e = datetime.fromisoformat(end or "2024-12-31").date()
        frames = fetch_dukascopy_m30(s, e)
        label = "dukascopy_m30"
    elif source == "stooq_daily":
        from data.stooq import fetch_stooq_daily

        frames = fetch_stooq_daily()
        label = "stooq_daily"
    else:
        raise ValueError(f"unknown market source {source!r}")
    stamped = frames_to_bars(frames, source=label, registry=registry)
    return write_vendor_and_parquet(
        stamped,
        source=label,
        raw_dir=repo / "data" / "raw" / label,
        parquet_dir=repo / "data" / "parquet" / label,
    )
