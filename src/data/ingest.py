"""Ingest vendor files into normalized Parquet.

Raw files under data/raw/ are immutable. The ingest step writes a new
tree under data/parquet/ and a validation sidecar. Nothing is patched
in place.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from data.normalize import REQUIRED_COLUMNS, apply_aliases, ensure_ny_timestamp
from data.validate import validate_frame, ValidationReport
from futures.sessions import SessionCalendar


def _read_any(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".json"}:
        return pd.read_json(path)
    raise ValueError(f"unsupported raw format: {path}")


def ingest_raw(
    raw_root: str | Path,
    out_root: str | Path,
    calendar: SessionCalendar | None = None,
) -> ValidationReport:
    raw_root = Path(raw_root)
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    files = [p for p in raw_root.rglob("*") if p.suffix.lower() in {".csv", ".txt", ".parquet", ".pq", ".json"}]
    reports = []
    notes: list[str] = []
    if not files:
        notes.append(f"no ingestible files under {raw_root}")

    for path in sorted(files):
        df = _read_any(path)
        df.columns = apply_aliases(list(df.columns))
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            notes.append(f"{path}: missing {missing}")
            continue
        df["ts"] = ensure_ny_timestamp(df["ts"])
        df["instrument"] = df["instrument"].astype(str)
        df["contract"] = df["contract"].astype(str)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # Drop rows that cannot be interpreted; record the exclusion.
        before = len(df)
        df = df.dropna(subset=["ts", "instrument", "contract", "open", "high", "low", "close"])
        dropped = before - len(df)
        inst = str(df["instrument"].iloc[0]) if len(df) else path.stem
        report = validate_frame(df, inst, calendar)
        if dropped:
            report.exclusions.append(
                {
                    "kind": "unparseable_row",
                    "count": int(dropped),
                    "action": "excluded_from_parquet",
                    "source": str(path),
                }
            )
        dest = out_root / f"{inst}.parquet"
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, dest)
        reports.append(report)

    ok = bool(reports) and all(
        (not r.missing_required and r.impossible_ohlc == 0) for r in reports
    )
    from datetime import datetime

    summary = ValidationReport(
        ok=ok,
        generated_at=datetime.utcnow().isoformat() + "Z",
        path=str(out_root),
        instruments=reports,
        notes=notes,
    )
    (out_root / "validation.json").write_text(
        json.dumps(summary.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    (out_root / "ingest_log.json").write_text(
        json.dumps({"sources": [str(p) for p in files], "notes": notes}, indent=2),
        encoding="utf-8",
    )
    return summary
