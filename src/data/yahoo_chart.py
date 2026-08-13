"""Parse Yahoo v8 chart JSON (the public chart endpoint).

Yahoo's CSV download requires a cookie. The chart endpoint returns
timestamp + quote arrays. This parser is for files saved from that
endpoint (or a future TLS-capable fetch). It does not invent bars.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")


def parse_chart_json(payload: dict | str | Path) -> pd.DataFrame:
    if isinstance(payload, (str, Path)) and Path(payload).exists():
        payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    elif isinstance(payload, str):
        payload = json.loads(payload)
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        err = (payload.get("chart") or {}).get("error") or payload.get("finance")
        raise ValueError(f"Yahoo chart payload has no result: {err}")
    block = result[0]
    ts = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or [0] * len(ts)
    rows = []
    for i, epoch in enumerate(ts):
        o, h, l, c = _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)
        if None in (o, h, l, c):
            continue
        stamp = datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(NY)
        rows.append(
            {
                "ts": stamp,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(_at(vols, i) or 0.0),
            }
        )
    if not rows:
        raise ValueError("Yahoo chart contained no usable OHLC rows")
    return pd.DataFrame(rows)


def _at(seq, i):
    return seq[i] if i < len(seq) else None
