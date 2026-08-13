"""Backtest driver: freeze config, run the engine, write an immutable run dir."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from data.synthetic import load_bars_parquet, synthesize
from data.validate import hash_parquet_files
from engine.engine import Engine, EngineConfig, EngineResult
from engine.types import Bar
from futures.contracts import InstrumentRegistry
from futures.rolls import RollCalendar
from futures.sessions import SessionCalendar, try_load_cme_holidays
from research.configutil import repo_root
from research.metrics import compute_metrics, segment_trades, trade_frame
from strategy.frs import FRSStrategy

NY = ZoneInfo("America/New_York")
ENGINE_VERSION = "0.2.0"


def git_commit(root: Path) -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def _cfg_hash(cfg: dict[str, Any]) -> str:
    blob = json.dumps(cfg, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def build_engine(cfg: dict[str, Any], registry: InstrumentRegistry) -> Engine:
    acct = cfg["account"]
    exe = cfg["execution"]
    engine_cfg = EngineConfig(
        starting_cash=float(acct["starting_cash"]),
        max_positions=int(acct.get("max_positions", 1)),
        slippage_ticks=int(exe.get("slippage_ticks", 1)),
        signal_to_fill_bars=int(exe.get("signal_to_fill_bars", 1)),
        commission_multiplier=float(cfg.get("commission_multiplier", 1.0)),
        limit_fill_on_touch=bool(exe.get("limit_fill_on_touch", True)),
        stop_gap_policy=str(exe.get("stop_gap_policy", "worse_of_open_and_stop")),
        intrabar_ambiguity=str(exe.get("intrabar_ambiguity", "worst_case")),
        flatten_on_roll=bool(cfg.get("exits", {}).get("flatten_on_roll", True)),
        timezone=str(acct.get("timezone", "America/New_York")),
    )
    strategy = FRSStrategy(cfg)
    return Engine(strategy, registry, engine_cfg, extra_cfg=cfg)


def schedule_sidecars(engine: Engine, bars: list[Bar], cfg: dict[str, Any], registry: InstrumentRegistry) -> None:
    if not bars:
        return
    start, end = bars[0].ts, bars[-1].ts
    holidays = try_load_cme_holidays(start.date(), end.date())
    calendar = SessionCalendar(holidays=holidays)
    flats = calendar.force_flat_times(start, end, cfg["session"]["force_flat"])
    engine.schedule_force_flats(flats)
    roll_days = int(cfg.get("roll_days_before_expiry") or registry.roll_days_before_expiry())
    rolls = []
    for spec in registry.tradeable():
        rolls.extend(RollCalendar(spec, days_before=roll_days).events(start, end))
    engine.schedule_rolls(rolls)


def run_engine_on_bars(cfg: dict[str, Any], bars: list[Bar], registry: InstrumentRegistry) -> EngineResult:
    engine = build_engine(cfg, registry)
    schedule_sidecars(engine, bars, cfg, registry)
    return engine.run(bars)


def slice_holdout(bars: list[Bar], fraction: float, which: str) -> list[Bar]:
    if not bars or fraction <= 0:
        return list(bars)
    stamps = sorted({b.ts for b in bars})
    cut = stamps[max(0, int(len(stamps) * (1.0 - fraction)) - 1)]
    if which == "is":
        return [b for b in bars if b.ts <= cut]
    if which == "oos":
        return [b for b in bars if b.ts > cut]
    raise ValueError(which)


def write_run_dir(
    result: EngineResult,
    cfg: dict[str, Any],
    *,
    scenario_name: str,
    data_hashes: dict[str, str],
    seed: int,
    dataset: str,
    notes: dict[str, Any] | None = None,
    run_root: Path | None = None,
) -> Path:
    root = repo_root()
    run_root = run_root or (root / "runs")
    run_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    commit = git_commit(root)
    dest = run_root / f"{stamp}_{scenario_name}_{_cfg_hash(cfg)}"
    dest.mkdir(parents=True, exist_ok=False)

    (dest / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    meta = {
        "scenario": scenario_name,
        "dataset": dataset,
        "engine_version": ENGINE_VERSION,
        "definition_id": cfg.get("definition_id"),
        "git_commit": commit,
        "seed": seed,
        "data_hashes": data_hashes,
        "created_utc": stamp,
        "notes": notes or {},
        "engine_config": result.config,
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    trades = trade_frame(result.trades)
    _write_table(dest / "trades.parquet", trades)
    orders = []
    for o in result.orders:
        orders.append(
            {
                "order_id": o.order_id,
                "ts": o.submitted_ts,
                "instrument": o.instrument,
                "contract": o.contract,
                "side": o.side,
                "quantity": o.quantity,
                "type": o.order_type.value,
                "status": o.status.value,
                "limit_price": o.limit_price,
                "stop_price": o.stop_price,
                "tag": o.tag,
                "filled_qty": o.filled_qty,
                "avg_fill_price": o.avg_fill_price,
                "oco_group": o.oco_group,
                "min_bars_before_fill": o.min_bars_before_fill,
            }
        )
    _write_table(dest / "orders.parquet", orders)
    fills = []
    for f in result.fills:
        fills.append(
            {
                "fill_id": f.fill_id,
                "order_id": f.order_id,
                "ts": f.ts,
                "instrument": f.instrument,
                "contract": f.contract,
                "side": f.side,
                "quantity": f.quantity,
                "price": f.price,
                "commission": f.commission,
                "slippage_ticks": f.slippage_ticks,
                "slippage_paid": f.slippage_paid,
                "reason": f.reason,
                "ambiguous": f.ambiguous,
            }
        )
    _write_table(dest / "fills.parquet", fills)
    _write_table(dest / "rejected_signals.parquet", result.rejected_signals)
    equity_rows = [
        {
            "ts": p.ts,
            "cash": p.cash,
            "unrealized": p.unrealized,
            "equity": p.equity,
            "exposure_contracts": p.exposure_contracts,
            "open_instruments": p.open_instruments,
        }
        for p in result.equity
    ]
    _write_table(dest / "equity.parquet", equity_rows)

    metrics = compute_metrics(
        result.trades, result.equity, starting_cash=float(cfg["account"]["starting_cash"])
    )
    metrics["segments"] = segment_trades(result.trades)
    metrics["final_snapshot"] = result.final_snapshot
    (dest / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    (dest / "conclusion.md").write_text(_single_run_conclusion(scenario_name, metrics, meta), encoding="utf-8")
    return dest


def _single_run_conclusion(name: str, metrics: dict[str, Any], meta: dict[str, Any]) -> str:
    flags = []
    if metrics["n_trades"] < 30:
        flags.append("small sample")
    if metrics["expectancy"] <= 0:
        flags.append("non-positive expectancy")
    if metrics["concentration_top5_of_wins"] > 0.5:
        flags.append("top 5 wins dominate")
    if metrics["ambiguous_fraction"] > 0.25:
        flags.append("many same-bar stop/target ambiguities")
    if meta.get("dataset") in {"random_walk", "planted_frs"}:
        flags.append("synthetic data only")
    verdict = "suspicious / not a claim" if flags else "needs the rest of the battery"
    lines = [
        f"# Conclusion — {name}",
        "",
        f"Definition: `{meta.get('definition_id')}`  ",
        f"Dataset: `{meta.get('dataset')}`  seed={meta.get('seed')}  commit=`{meta.get('git_commit')}`",
        "",
        f"- trades: {metrics['n_trades']}",
        f"- expectancy: {metrics['expectancy']:.6f}",
        f"- net PnL: {metrics['net_pnl']:.2f}",
        f"- win rate: {metrics['win_rate']:.3f}",
        f"- payoff: {metrics['payoff_ratio']:.3f}",
        f"- Sharpe (bar-annualized): {metrics['sharpe']:.3f}",
        f"- max DD: {metrics['max_drawdown']:.2f} ({metrics['max_drawdown_pct']:.2%})",
        f"- slippage paid: {metrics['slippage_paid']:.2f}",
        f"- commissions paid: {metrics['commissions_paid']:.2f}",
        "",
        f"Interim verdict: **{verdict}**",
        "",
        "Flags: " + (", ".join(flags) if flags else "(none)"),
        "",
        "A single run is not evidence. Cost stress, parameter neighbors,",
        "walk-forward, and a reserved holdout are required before anything",
        "is called economically meaningful.",
        "",
    ]
    return "\n".join(lines)


def _write_table(path: Path, rows: list[dict]) -> None:
    if not rows:
        df = pd.DataFrame()
    else:
        df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def load_or_synth_bars(
    cfg: dict[str, Any],
    *,
    dataset: str,
    start: str,
    end: str,
    seed: int,
    parquet_root: Path | None = None,
) -> tuple[list[Bar], dict[str, str], str]:
    root = repo_root()
    parquet_root = parquet_root or (root / "data" / "parquet" / dataset)
    instruments_yaml = root / "config" / "instruments.yaml"
    registry = InstrumentRegistry.from_yaml(instruments_yaml)
    if dataset in {"random_walk", "planted_frs"}:
        parquet_root.mkdir(parents=True, exist_ok=True)
        existing = list(parquet_root.glob("*.parquet"))
        meta_path = parquet_root / "synthetic_meta.json"
        reuse = False
        if existing and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            reuse = (
                meta.get("seed") == seed
                and meta.get("scenario") == dataset
                and meta.get("start") == start
                and meta.get("end") == end
            )
        if not reuse:
            synthesize(
                start=start,
                end=end,
                seed=seed,
                scenario=dataset,
                out_dir=parquet_root,
                registry=registry,
                roll_days=int(cfg.get("roll_days_before_expiry") or registry.roll_days_before_expiry()),
            )
        bars = load_bars_parquet(parquet_root)
        return bars, hash_parquet_files(parquet_root), str(parquet_root)
    if dataset in {"raw", "vendor", "yahoo_1h", "yahoo_30m", "dukascopy_m30"}:
        if dataset in {"yahoo_1h", "yahoo_30m", "dukascopy_m30"}:
            parquet_root = root / "data" / "parquet" / dataset
            have = list(parquet_root.glob("*.parquet")) if parquet_root.exists() else []
            if not have:
                from data.fetch_market import fetch_and_store

                fetch_and_store(dataset, repo=root, registry=registry, start=start, end=end)
        if not parquet_root.exists():
            raise FileNotFoundError(f"vendor parquet not found at {parquet_root}; run frs ingest or frs fetch")
        return load_bars_parquet(parquet_root), hash_parquet_files(parquet_root), str(parquet_root)
    # Treat dataset as a path.
    path = Path(dataset)
    if path.exists():
        return load_bars_parquet(path), hash_parquet_files(path), str(path)
    raise FileNotFoundError(dataset)


def run_backtest(
    cfg: dict[str, Any],
    *,
    scenario_name: str = "baseline",
    dataset: str = "random_walk",
    start: str = "2021-01-01",
    end: str = "2022-12-31",
    seed: int = 42,
    which: str = "is",
    holdout_fraction: float = 0.20,
    persist: bool = True,
    run_root: Path | None = None,
    bars: list[Bar] | None = None,
) -> tuple[EngineResult, Path | None, dict[str, Any]]:
    root = repo_root()
    registry = InstrumentRegistry.from_yaml(root / "config" / "instruments.yaml")
    if bars is None:
        bars, hashes, origin = load_or_synth_bars(cfg, dataset=dataset, start=start, end=end, seed=seed)
    else:
        hashes, origin = {}, "in-memory"
    if which in {"is", "oos"}:
        bars = slice_holdout(bars, holdout_fraction, which)
    result = run_engine_on_bars(cfg, bars, registry)
    dest = None
    if persist:
        dest = write_run_dir(
            result,
            cfg,
            scenario_name=f"{scenario_name}_{which}",
            data_hashes=hashes,
            seed=seed,
            dataset=dataset,
            notes={"origin": origin, "which": which, "n_bars": len(bars)},
            run_root=run_root,
        )
    metrics = compute_metrics(
        result.trades, result.equity, starting_cash=float(cfg["account"]["starting_cash"])
    )
    metrics["segments"] = segment_trades(result.trades)
    return result, dest, metrics
