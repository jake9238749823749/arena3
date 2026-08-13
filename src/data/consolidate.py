"""Bar consolidation. Higher-timeframe bars complete only when the period elapses."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from engine.types import Bar

NY = ZoneInfo("America/New_York")


def _period_end(ts: datetime, minutes: int) -> datetime:
    local = ts.astimezone(NY)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((local - midnight).total_seconds() // 60)
    # Close timestamp is the end of the bucket containing ts, or ts if exact.
    bucket = (elapsed - 1) // minutes if elapsed % minutes == 0 and elapsed > 0 else elapsed // minutes
    if elapsed % minutes == 0 and elapsed > 0:
        return midnight + timedelta(minutes=elapsed)
    return midnight + timedelta(minutes=(bucket + 1) * minutes)


def consolidate(bars: list[Bar], minutes: int) -> list[Bar]:
    """Build ``minutes``-wide OHLCV bars from finer bars of the same instrument.

    The output timestamp is the period *close*. A 30-minute bar ending
    10:00 is not visible before 10:00.
    """
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    groups: dict[tuple, list[Bar]] = defaultdict(list)
    for b in sorted(bars, key=lambda x: (x.ts, x.instrument, x.contract)):
        close_ts = _period_end(b.ts, minutes)
        groups[(b.instrument, b.contract, close_ts)].append(b)
    out: list[Bar] = []
    for (inst, contract, close_ts), chunk in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        first, last = chunk[0], chunk[-1]
        high = max(b.high for b in chunk)
        low = min(b.low for b in chunk)
        ch = max((b.cont_high for b in chunk if b.cont_high is not None), default=None)
        cl = min((b.cont_low for b in chunk if b.cont_low is not None), default=None)
        out.append(
            Bar(
                ts=close_ts if close_ts.tzinfo else close_ts.replace(tzinfo=NY),
                instrument=inst,
                contract=contract,
                open=first.open,
                high=high,
                low=low,
                close=last.close,
                volume=sum(b.volume for b in chunk),
                session_date=last.session_date,
                is_rth=any(b.is_rth for b in chunk),
                open_interest=last.open_interest,
                cont_open=first.cont_open,
                cont_high=ch,
                cont_low=cl,
                cont_close=last.cont_close,
            )
        )
    return out
