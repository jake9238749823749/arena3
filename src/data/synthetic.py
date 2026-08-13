"""Deterministic synthetic futures history.

Two data-generating processes ship with the stack:

* ``random_walk`` — correlated GBM. A well-specified FRS should not show
  economically meaningful expectancy here after costs. If it does, the
  engine or the signal is leaking.
* ``planted_frs`` — the same skeleton with occasional compressed ranges
  followed by a high-energy acceptance through the prior boundary and
  several bars of continuation. Used to verify that the *implementation*
  of the frozen baseline can detect the mechanism it claims to encode.

Neither process is a claim about real GC/ES/NQ. Point ``data/raw/`` at
dated contract bars to evaluate the actual market.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from engine.types import Bar
from futures.contracts import InstrumentRegistry, expiry_for, parse_contract_code
from futures.rolls import RollCalendar, front_at
from futures.sessions import SessionCalendar, try_load_cme_holidays

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SynthSpec:
    instrument: str
    start_price: float
    annual_vol: float
    drift: float
    tick_size: float


DEFAULT_SPECS: dict[str, SynthSpec] = {
    "ES": SynthSpec("ES", 3800.0, 0.16, 0.04, 0.25),
    "NQ": SynthSpec("NQ", 12800.0, 0.22, 0.06, 0.25),
    "GC": SynthSpec("GC", 1800.0, 0.14, 0.02, 0.10),
}

MICRO = {"ES": "MES", "NQ": "MNQ", "GC": "MGC"}


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(int(seed))


def _snap(price: float, tick: float) -> float:
    return round(price / tick) * tick


def _make_ohlc(prev_close: float, ret: float, tick: float, rng: np.random.Generator) -> tuple[float, float, float, float]:
    close = max(tick, prev_close * (1.0 + ret))
    open_ = prev_close
    # Intrabar wick: a fraction of |ret| plus a small noise term.
    wick = max(abs(close - open_), prev_close * abs(rng.normal(0, 0.0006)))
    high = max(open_, close) + wick * rng.uniform(0.1, 0.7)
    low = min(open_, close) - wick * rng.uniform(0.1, 0.7)
    low = max(tick, low)
    if high <= low:
        high = low + tick
    o = _snap(open_, tick)
    c = _snap(close, tick)
    h = _snap(high, tick)
    l = _snap(low, tick)
    h = max(h, o, c)
    l = min(l, o, c)
    if h <= l:
        h = l + tick
    return o, h, l, c


def _in_plant_window(ts: datetime) -> bool:
    """Only plant where the frozen baseline is allowed to enter and hold."""
    t = ts.astimezone(NY).time()
    # Entry 09:30–13:00, and enough 30-minute bars to reach a 2-ATR target
    # before the 15:45 daily flat (need ~2 hours of continuation).
    return datetime.strptime("09:30", "%H:%M").time() <= t < datetime.strptime("11:30", "%H:%M").time()


def _plant_breakout(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    opens: np.ndarray,
    tick: float,
    lookback: int,
    rng: np.random.Generator,
    timestamps: list[datetime] | None = None,
) -> None:
    """Mutate arrays in place to plant compressed-then-breakout episodes.

    Plants are restricted to the RTH entry window when timestamps are
    provided. Overnight plants are not a fair test of the executable rule.
    """
    n = len(closes)
    i = lookback + 8
    while i < n - 12:
        if timestamps is not None and not _in_plant_window(timestamps[i]):
            i += 1
            continue
        if rng.random() > 0.12:
            i += 1
            continue
        # Compress the prior lookback window.
        anchor = closes[i - 1]
        for j in range(i - lookback, i):
            width = tick * rng.uniform(2, 6)
            opens[j] = _snap(anchor, tick)
            closes[j] = _snap(anchor + rng.uniform(-width, width), tick)
            highs[j] = _snap(max(opens[j], closes[j]) + width * 0.4, tick)
            lows[j] = _snap(min(opens[j], closes[j]) - width * 0.4, tick)
            if highs[j] <= lows[j]:
                highs[j] = lows[j] + tick
        upper = max(highs[i - lookback : i])
        lower = min(lows[i - lookback : i])
        direction = 1 if rng.random() > 0.5 else -1
        span = max(tick * 16, (upper - lower) * 1.8)
        if direction > 0:
            opens[i] = _snap(upper - tick, tick)
            closes[i] = _snap(upper + span, tick)
            highs[i] = _snap(closes[i] + tick, tick)
            lows[i] = _snap(min(opens[i], upper - 2 * tick), tick)
        else:
            opens[i] = _snap(lower + tick, tick)
            closes[i] = _snap(lower - span, tick)
            lows[i] = _snap(closes[i] - tick, tick)
            highs[i] = _snap(max(opens[i], lower + 2 * tick), tick)
        # Continuation for several bars so a 2-ATR target can complete
        # before the daily flat. Keep the path one-sided.
        step = span * 0.45 * direction
        for k in range(1, 8):
            if i + k >= n:
                break
            opens[i + k] = closes[i + k - 1]
            closes[i + k] = _snap(opens[i + k] + step, tick)
            if direction > 0:
                highs[i + k] = _snap(closes[i + k] + tick * 2, tick)
                lows[i + k] = _snap(opens[i + k] - tick, tick)
            else:
                highs[i + k] = _snap(opens[i + k] + tick, tick)
                lows[i + k] = _snap(closes[i + k] - tick * 2, tick)
        i += lookback + 14


def generate_price_arrays(
    n: int,
    spec: SynthSpec,
    rng: np.random.Generator,
    *,
    scenario: str,
    dt_years: float,
    lookback: int = 12,
) -> dict[str, np.ndarray]:
    shocks = rng.normal(spec.drift * dt_years, spec.annual_vol * np.sqrt(dt_years), size=n)
    opens = np.zeros(n)
    highs = np.zeros(n)
    lows = np.zeros(n)
    closes = np.zeros(n)
    prev = spec.start_price
    for i, r in enumerate(shocks):
        o, h, l, c = _make_ohlc(prev, r, spec.tick_size, rng)
        opens[i], highs[i], lows[i], closes[i] = o, h, l, c
        prev = c
    if scenario == "planted_frs":
        _plant_breakout(closes, highs, lows, opens, spec.tick_size, lookback, rng, None)
    return {"open": opens, "high": highs, "low": lows, "close": closes}


def _backward_ratio_adjust(
    raw_o: np.ndarray,
    raw_h: np.ndarray,
    raw_l: np.ndarray,
    raw_c: np.ndarray,
    contracts: list[str],
    roll_indices: list[int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Backward-ratio: walk from the end so the last price is unadjusted."""
    o = raw_o.copy()
    h = raw_h.copy()
    l = raw_l.copy()
    c = raw_c.copy()
    # At each roll index i, contract[i] != contract[i-1]. Factor = new/old
    # applied to all prices strictly before i.
    for i in reversed(roll_indices):
        if i <= 0 or i >= len(c):
            continue
        old = c[i - 1]
        new = c[i]
        if old == 0:
            continue
        factor = new / old
        o[:i] *= factor
        h[:i] *= factor
        l[:i] *= factor
        c[:i] *= factor
    return o, h, l, c


def synthesize(
    *,
    start: str | datetime,
    end: str | datetime,
    seed: int,
    scenario: str = "random_walk",
    bar_minutes: int = 30,
    out_dir: str | Path | None = None,
    registry: InstrumentRegistry | None = None,
    roll_days: int = 8,
    include_micros: bool = True,
    roll_basis_ticks: int = 0,
) -> dict[str, list[Bar]]:
    if isinstance(start, str):
        start = datetime.fromisoformat(start).replace(tzinfo=NY)
    if isinstance(end, str):
        end = datetime.fromisoformat(end).replace(tzinfo=NY)
    if start.tzinfo is None:
        start = start.replace(tzinfo=NY)
    if end.tzinfo is None:
        end = end.replace(tzinfo=NY)

    holidays = try_load_cme_holidays(start.date(), end.date())
    calendar = SessionCalendar(holidays=holidays)
    closes_ts = calendar.iter_bar_closes(start, end, bar_minutes)
    if not closes_ts:
        raise ValueError("no bars in requested window")

    rng = _rng(seed)
    # Shared shock for ES/NQ correlation.
    n = len(closes_ts)
    dt_years = bar_minutes / (60 * 24 * 365.25)

    if registry is None:
        # Lightweight specs sufficient for contract codes / rolls.
        from pathlib import Path as _P

        cfg = _P(__file__).resolve().parents[2] / "config" / "instruments.yaml"
        if cfg.exists():
            registry = InstrumentRegistry.from_yaml(cfg)

    by_inst: dict[str, list[Bar]] = {}
    # Independent residual + common factor.
    common = rng.normal(0.0, 1.0, size=n)

    for full, sspec in DEFAULT_SPECS.items():
        residual = rng.normal(0.0, 1.0, size=n)
        if full == "NQ":
            shock = 0.85 * common + 0.53 * residual
        elif full == "ES":
            shock = 0.85 * common + 0.53 * residual
        else:
            shock = 0.15 * common + 0.99 * residual
        shock = sspec.drift * dt_years + sspec.annual_vol * np.sqrt(dt_years) * shock
        opens = np.zeros(n)
        highs = np.zeros(n)
        lows = np.zeros(n)
        closes = np.zeros(n)
        prev = sspec.start_price
        for i, r in enumerate(shock):
            o, h, l, c = _make_ohlc(prev, float(r), sspec.tick_size, rng)
            opens[i], highs[i], lows[i], closes[i] = o, h, l, c
            prev = c
        if scenario == "planted_frs":
            _plant_breakout(closes, highs, lows, opens, sspec.tick_size, 12, rng, closes_ts)

        # Map each timestamp to a dated front contract.
        if registry is not None and full in registry:
            spec = registry[full]
            cal = RollCalendar(spec, days_before=roll_days)
            intervals = cal.map_through_time(closes_ts[0], closes_ts[-1] + timedelta(minutes=1))
        else:
            intervals = []

        contracts: list[str] = []
        for ts in closes_ts:
            if intervals:
                found = None
                for a, b, code in intervals:
                    if a <= ts < b:
                        found = code
                        break
                if found is None:
                    found = intervals[-1][2]
                contracts.append(found)
            else:
                contracts.append(f"{full}H20")

        # Optional roll basis: jump the raw price at contract change so
        # continuous adjustment is not the identity map.
        if roll_basis_ticks:
            for i in range(1, n):
                if contracts[i] != contracts[i - 1]:
                    jump = roll_basis_ticks * sspec.tick_size
                    opens[i:] += jump
                    highs[i:] += jump
                    lows[i:] += jump
                    closes[i:] += jump

        roll_idx = [i for i in range(1, n) if contracts[i] != contracts[i - 1]]
        cont_o, cont_h, cont_l, cont_c = _backward_ratio_adjust(
            opens, highs, lows, closes, contracts, roll_idx
        )

        def _bars(root: str, tick: float) -> list[Bar]:
            out: list[Bar] = []
            # Micro uses the same price levels; contract root changes.
            for i, ts in enumerate(closes_ts):
                raw_root, year, month = parse_contract_code(contracts[i])
                code = contracts[i] if root == full else f"{root}{contracts[i][len(full):]}"
                sess = calendar.session_date(ts)
                rth_start, rth_end = ("09:30", "16:00") if root in {"ES", "MES", "NQ", "MNQ"} else ("08:20", "13:30")
                out.append(
                    Bar(
                        ts=ts,
                        instrument=root,
                        contract=code,
                        open=_snap(float(opens[i]), tick),
                        high=_snap(float(highs[i]), tick),
                        low=_snap(float(lows[i]), tick),
                        close=_snap(float(closes[i]), tick),
                        volume=float(max(0, rng.integers(100, 5000))),
                        session_date=sess,
                        is_rth=calendar.is_rth(ts, rth_start, rth_end),
                        open_interest=float(max(0, rng.integers(1000, 20000))),
                        cont_open=_snap(float(cont_o[i]), tick),
                        cont_high=_snap(float(cont_h[i]), tick),
                        cont_low=_snap(float(cont_l[i]), tick),
                        cont_close=_snap(float(cont_c[i]), tick),
                    )
                )
            return out

        by_inst[full] = _bars(full, sspec.tick_size)
        if include_micros:
            micro = MICRO[full]
            by_inst[micro] = _bars(micro, sspec.tick_size)

    if out_dir is not None:
        write_bars_parquet(by_inst, out_dir, scenario=scenario, seed=seed)
    return by_inst


def write_bars_parquet(
    by_inst: dict[str, list[Bar]],
    out_dir: str | Path,
    *,
    scenario: str,
    seed: int,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for inst, bars in by_inst.items():
        rows = []
        for b in bars:
            rows.append(
                {
                    "ts": b.ts,
                    "instrument": b.instrument,
                    "contract": b.contract,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "open_interest": b.open_interest,
                    "cont_open": b.cont_open,
                    "cont_high": b.cont_high,
                    "cont_low": b.cont_low,
                    "cont_close": b.cont_close,
                    "session_date": b.session_date.isoformat() if b.session_date else None,
                    "is_rth": b.is_rth,
                }
            )
        df = pd.DataFrame(rows)
        dest = out_dir / f"{inst}.parquet"
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False), dest)
        frames.append(df)
    meta = {
        "scenario": scenario,
        "seed": seed,
        "instruments": sorted(by_inst),
        "rows": {k: len(v) for k, v in by_inst.items()},
        "generator": "src/data/synthetic.py",
    }
    (out_dir / "synthetic_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out_dir


def load_bars_parquet(root: str | Path, instruments: Iterable[str] | None = None) -> list[Bar]:
    import duckdb

    root = Path(root)
    files = sorted(root.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no parquet under {root}")
    con = duckdb.connect(database=":memory:")
    paths = [str(f) for f in files]
    df = con.execute(
        "SELECT * FROM read_parquet(?) ORDER BY ts, instrument, contract",
        [paths],
    ).fetchdf()
    if instruments is not None:
        keep = set(instruments)
        df = df[df["instrument"].isin(keep)]
    bars: list[Bar] = []
    for rec in df.itertuples(index=False):
        ts = rec.ts
        if getattr(ts, "tzinfo", None) is None:
            ts = pd.Timestamp(ts).tz_localize(NY).to_pydatetime()
        else:
            ts = pd.Timestamp(ts).tz_convert(NY).to_pydatetime()
        sess = rec.session_date
        if isinstance(sess, str):
            from datetime import date as _date

            sess = _date.fromisoformat(sess)
        bars.append(
            Bar(
                ts=ts,
                instrument=str(rec.instrument),
                contract=str(rec.contract),
                open=float(rec.open),
                high=float(rec.high),
                low=float(rec.low),
                close=float(rec.close),
                volume=float(rec.volume),
                session_date=sess,
                is_rth=bool(rec.is_rth),
                open_interest=None if rec.open_interest is None else float(rec.open_interest),
                cont_open=None if rec.cont_open is None else float(rec.cont_open),
                cont_high=None if rec.cont_high is None else float(rec.cont_high),
                cont_low=None if rec.cont_low is None else float(rec.cont_low),
                cont_close=None if rec.cont_close is None else float(rec.cont_close),
            )
        )
    return bars


def tiny_fixture(n_bars: int = 80, seed: int = 1, planted: bool = False) -> list[Bar]:
    """Short, fully in-memory fixture for unit tests (single RTH-like stream)."""
    rng = _rng(seed)
    start = datetime(2021, 3, 1, 9, 30, tzinfo=NY)
    ts = [start + timedelta(minutes=30 * i) for i in range(n_bars)]
    # Skip the 17:00 break / overnight by staying inside a long weekday
    # sequence that we treat as valid for tests that do not check sessions.
    spec = DEFAULT_SPECS["ES"]
    arrays = generate_price_arrays(
        n_bars,
        spec,
        rng,
        scenario="planted_frs" if planted else "random_walk",
        dt_years=30 / (60 * 24 * 365.25),
    )
    bars: list[Bar] = []
    for i, stamp in enumerate(ts):
        o, h, l, c = arrays["open"][i], arrays["high"][i], arrays["low"][i], arrays["close"][i]
        for inst, tick, contract in (
            ("ES", 0.25, "ESH21"),
            ("MES", 0.25, "MESH21"),
        ):
            bars.append(
                Bar(
                    ts=stamp,
                    instrument=inst,
                    contract=contract,
                    open=_snap(float(o), tick),
                    high=_snap(float(h), tick),
                    low=_snap(float(l), tick),
                    close=_snap(float(c), tick),
                    volume=1000,
                    session_date=stamp.date(),
                    is_rth=True,
                    cont_open=_snap(float(o), tick),
                    cont_high=_snap(float(h), tick),
                    cont_low=_snap(float(l), tick),
                    cont_close=_snap(float(c), tick),
                )
            )
    return bars
