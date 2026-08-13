"""Adversarial research battery.

Order of operations (holdout is last and never used for selection):

1. Baseline on the in-sample span
2. Cost and delay stress
3. One-at-a-time parameter neighbors
4. Alternate roll assumptions
5. Chronological walk-forward on the in-sample span
6. Reserved holdout evaluation of the *frozen* baseline only
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from engine.types import Bar
from research.backtest import run_backtest, slice_holdout
from research.configutil import load_scenarios, load_strategy, materialize_scenario, set_path
from research.metrics import compute_metrics


@dataclass
class CaseResult:
    name: str
    metrics: dict[str, Any]
    run_dir: str | None
    extra: dict[str, Any]


def _expectancy(metrics: dict[str, Any]) -> float:
    return float(metrics.get("expectancy") or 0.0)


def run_case(
    name: str,
    cfg: dict[str, Any],
    *,
    dataset: str,
    start: str,
    end: str,
    seed: int,
    which: str,
    holdout_fraction: float,
    persist: bool,
    bars: list[Bar] | None = None,
) -> CaseResult:
    result, dest, metrics = run_backtest(
        cfg,
        scenario_name=name,
        dataset=dataset,
        start=start,
        end=end,
        seed=seed,
        which=which,
        holdout_fraction=holdout_fraction,
        persist=persist,
        bars=bars,
    )
    return CaseResult(name=name, metrics=metrics, run_dir=str(dest) if dest else None, extra={"n_trades": metrics["n_trades"]})


def walkforward(
    cfg: dict[str, Any],
    bars: list[Bar],
    *,
    train_days: int,
    test_days: int,
    step_days: int,
    persist: bool,
    dataset: str,
    seed: int,
) -> list[dict[str, Any]]:
    if not bars:
        return []
    stamps = sorted({b.ts for b in bars})
    start = stamps[0]
    end = stamps[-1]
    folds = []
    cursor = start
    fold = 0
    while True:
        train_end = cursor + timedelta(days=train_days)
        test_end = train_end + timedelta(days=test_days)
        if test_end > end:
            break
        train_bars = [b for b in bars if cursor <= b.ts < train_end]
        test_bars = [b for b in bars if train_end <= b.ts < test_end]
        # Frozen baseline — no fitting. We still run both windows so the
        # report can compare IS/OOS of the *same* definition.
        _, _, m_train = run_backtest(
            cfg,
            scenario_name=f"wf{fold:02d}_train",
            dataset=dataset,
            seed=seed,
            which="is",
            holdout_fraction=0.0,
            persist=persist,
            bars=train_bars,
        )
        _, dest, m_test = run_backtest(
            cfg,
            scenario_name=f"wf{fold:02d}_test",
            dataset=dataset,
            seed=seed,
            which="is",
            holdout_fraction=0.0,
            persist=persist,
            bars=test_bars,
        )
        folds.append(
            {
                "fold": fold,
                "train_start": cursor.isoformat(),
                "train_end": train_end.isoformat(),
                "test_end": test_end.isoformat(),
                "train": {"n": m_train["n_trades"], "expectancy": m_train["expectancy"], "net": m_train["net_pnl"]},
                "test": {"n": m_test["n_trades"], "expectancy": m_test["expectancy"], "net": m_test["net_pnl"]},
                "run_dir": str(dest) if dest else None,
            }
        )
        cursor = cursor + timedelta(days=step_days)
        fold += 1
    return folds


def run_suite(
    suite: str = "standard",
    *,
    dataset: str | None = None,
    persist: bool = True,
    start: str | None = None,
    end: str | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    base = load_strategy()
    sc = load_scenarios()
    names = sc["suites"][suite]
    ds_cfg = sc["dataset"]
    dataset = dataset or ds_cfg["default"]
    start = start or ds_cfg["start"]
    end = end or ds_cfg["end"]
    seed = int(seed if seed is not None else base.get("research", {}).get("seed", 42))
    holdout_fraction = float(ds_cfg.get("holdout_fraction", 0.20))

    # Load bars once (synthesis happens inside the first call).
    from research.backtest import load_or_synth_bars

    bars, hashes, origin = load_or_synth_bars(base, dataset=dataset, start=start, end=end, seed=seed)
    is_bars = slice_holdout(bars, holdout_fraction, "is")
    oos_bars = slice_holdout(bars, holdout_fraction, "oos")

    cases: list[CaseResult] = []
    grids: dict[str, list[dict[str, Any]]] = {}
    folds: list[dict[str, Any]] = []
    holdout_metrics: dict[str, Any] | None = None

    for name in names:
        body = sc["scenarios"][name]
        mode = body.get("mode")
        if mode == "walkforward":
            wf = base.get("research", {}).get("walkforward", {})
            folds = walkforward(
                base,
                is_bars,
                train_days=int(wf.get("train_days", 180)),
                test_days=int(wf.get("test_days", 90)),
                step_days=int(wf.get("step_days", 90)),
                persist=persist,
                dataset=dataset,
                seed=seed,
            )
            continue
        if mode == "holdout":
            cr = run_case(
                "holdout_baseline",
                base,
                dataset=dataset,
                start=start,
                end=end,
                seed=seed,
                which="oos",
                holdout_fraction=holdout_fraction,
                persist=persist,
                bars=bars,  # full series; run_backtest slices the reserved tail
            )
            holdout_metrics = cr.metrics
            cases.append(cr)
            continue
        grid = body.get("grid")
        cfg = materialize_scenario(base, body)
        if grid:
            rows = []
            for dotted, values in grid.items():
                for value in values:
                    labeled = f"{name}:{dotted}={value}"
                    variant = set_path(base, dotted, value)
                    cr = run_case(
                        labeled,
                        variant,
                        dataset=dataset,
                        start=start,
                        end=end,
                        seed=seed,
                        which="is",
                        holdout_fraction=0.0,  # already the IS slice
                        persist=persist,
                        bars=is_bars,
                    )
                    rows.append(
                        {
                            "param": dotted,
                            "value": value,
                            "expectancy": cr.metrics["expectancy"],
                            "n": cr.metrics["n_trades"],
                            "net": cr.metrics["net_pnl"],
                            "sharpe": cr.metrics["sharpe"],
                            "run_dir": cr.run_dir,
                        }
                    )
                    cases.append(cr)
            grids[name] = rows
            continue
        cr = run_case(
            name,
            cfg,
            dataset=dataset,
            start=start,
            end=end,
            seed=seed,
            which="is",
            holdout_fraction=0.0,  # already the IS slice
            persist=persist,
            bars=is_bars,
        )
        cases.append(cr)

    cost_curve = [
        {
            "name": c.name,
            "expectancy": c.metrics["expectancy"],
            "net": c.metrics["net_pnl"],
            "n": c.metrics["n_trades"],
            "slippage_paid": c.metrics["slippage_paid"],
            "commissions_paid": c.metrics["commissions_paid"],
        }
        for c in cases
        if c.name.startswith("cost_") or c.name == "baseline"
    ]

    summary = {
        "dataset": dataset,
        "origin": origin,
        "data_hashes": hashes,
        "start": start,
        "end": end,
        "seed": seed,
        "holdout_fraction": holdout_fraction,
        "n_is_bars": len(is_bars),
        "n_oos_bars": len(oos_bars),
        "cases": [
            {
                "name": c.name,
                "run_dir": c.run_dir,
                "n_trades": c.metrics["n_trades"],
                "expectancy": c.metrics["expectancy"],
                "net_pnl": c.metrics["net_pnl"],
                "win_rate": c.metrics["win_rate"],
                "sharpe": c.metrics["sharpe"],
                "max_drawdown": c.metrics["max_drawdown"],
                "concentration_top5_of_wins": c.metrics["concentration_top5_of_wins"],
                "ambiguous_fraction": c.metrics["ambiguous_fraction"],
            }
            for c in cases
        ],
        "cost_curve": cost_curve,
        "grids": grids,
        "walkforward": folds,
        "holdout": holdout_metrics,
    }
    return summary


def write_suite_summary(summary: dict[str, Any], path: Path | None = None) -> Path:
    from research.configutil import repo_root
    from datetime import datetime

    root = repo_root() / "runs"
    root.mkdir(parents=True, exist_ok=True)
    path = path or root / f"suite_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return path
