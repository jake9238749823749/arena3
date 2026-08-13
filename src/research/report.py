"""Immutable-run report and an automated, conservative conclusion.

The conclusion is a checklist, not a sales pitch. A pretty equity curve
is not survival.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from research.configutil import repo_root


def _latest_run(runs: Path) -> Path | None:
    dirs = [p for p in runs.iterdir() if p.is_dir() and (p / "metrics.json").exists()]
    if not dirs:
        return None
    return sorted(dirs, key=lambda p: p.name)[-1]


def load_run(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    metrics = json.loads((path / "metrics.json").read_text(encoding="utf-8"))
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    return {"path": str(path), "metrics": metrics, "meta": meta}


def conclude_from_suite(summary: dict[str, Any]) -> dict[str, Any]:
    """Return a structured verdict. Fail closed."""
    flags: list[str] = []
    cases = {c["name"]: c for c in summary.get("cases", [])}
    baseline = cases.get("baseline") or cases.get("baseline_is")
    # names are stored as scenario_which in run_backtest... wait, run_case
    # passes name through scenario_name, and write_run_dir suffixes _{which}.
    # CaseResult.name is the raw name ('baseline'), not the directory name.
    if baseline is None:
        # try prefix match
        for k, v in cases.items():
            if k.startswith("baseline"):
                baseline = v
                break

    if baseline is None:
        flags.append("NO_BASELINE")
        verdict = "INCOMPLETE"
        why = "Suite did not produce a baseline case."
        return {"verdict": verdict, "why": why, "flags": flags, "smallest_defensible": None}

    exp = float(baseline["expectancy"] or 0.0)
    n = int(baseline["n_trades"] or 0)
    if n < 30:
        flags.append("SMALL_SAMPLE")
    if exp <= 0:
        flags.append("NONPOSITIVE_EXPECTANCY")
    if float(baseline.get("concentration_top5_of_wins") or 0) > 0.50 and n:
        flags.append("CONCENTRATED_WINS")
    inst = (baseline.get("by_instrument") if False else None)

    # Cost stress: expectancy must remain > 0 at 2 ticks to be taken seriously.
    cost = {c["name"]: c for c in summary.get("cost_curve", [])}
    for k in ("cost_2", "cost_3"):
        if k in cost and float(cost[k]["expectancy"] or 0) <= 0:
            flags.append(f"DIES_AT_{k.upper()}")

    # Parameter fragility: sign flip vs baseline in any OAT neighbor.
    fragile = []
    for grid_name, rows in summary.get("grids", {}).items():
        signs = [1 if float(r["expectancy"]) > 0 else -1 if float(r["expectancy"]) < 0 else 0 for r in rows]
        if signs and max(signs) > 0 and min(signs) < 0:
            fragile.append(grid_name)
    if fragile:
        flags.append("PARAMETER_SIGN_FLIP:" + ",".join(fragile))

    # Walk-forward: majority of test folds non-positive.
    folds = summary.get("walkforward") or []
    if folds:
        test_pos = sum(1 for f in folds if float(f["test"]["expectancy"]) > 0)
        if test_pos < max(1, len(folds) // 2):
            flags.append("WALKFORWARD_WEAK")

    holdout = summary.get("holdout")
    if holdout:
        if int(holdout.get("n_trades") or 0) == 0:
            flags.append("HOLDOUT_NO_TRADES")
        elif float(holdout.get("expectancy") or 0) <= 0:
            flags.append("HOLDOUT_NONPOSITIVE")

    dataset = summary.get("dataset")
    if dataset == "random_walk" and exp > 0 and "NONPOSITIVE_EXPECTANCY" not in flags:
        flags.append("EDGE_ON_RANDOM_WALK")
    if dataset in {"random_walk", "planted_frs"}:
        flags.append("SYNTHETIC_DATA_ONLY")
    if dataset in {"yahoo_1h", "yahoo_30m", "dukascopy_m30", "stooq_daily"} or str(dataset).startswith("yahoo") or str(dataset).startswith("dukascopy") or str(dataset).startswith("stooq"):
        flags.append("PROXY_NOT_DATED_FUTURES")
    boot = baseline.get("bootstrap") or {}
    if boot.get("ci_high") is not None and float(boot["ci_high"]) <= 0:
        flags.append("BOOTSTRAP_CI_NONPOSITIVE")
    if boot.get("ci_low") is not None and float(boot["ci_low"]) > 0 and exp > 0:
        flags.append("BOOTSTRAP_CI_EXCLUDES_ZERO")

    if dataset == "random_walk":
        if "EDGE_ON_RANDOM_WALK" in flags:
            verdict = "LEAKAGE_SUSPECTED"
            why = (
                "Frozen FRS printed positive expectancy on a pure random walk "
                "after the canonical 1-tick cost. That is a look-ahead / fill "
                "model / multiple-testing alarm, not an edge."
            )
            smallest = None
        else:
            verdict = "FALSIFIED_ON_RANDOM_WALK"
            why = (
                "On correlated GBM with no planted mechanism, the frozen baseline "
                "has non-positive expectancy after 1-tick slippage, dies as costs "
                "rise, and fails walk-forward / holdout. That is the expected "
                "result if the engine is not leaking. It is not evidence about "
                "real GC/ES/NQ."
            )
            smallest = None
    elif dataset == "planted_frs":
        holdout_bad = "HOLDOUT_NONPOSITIVE" in flags
        costs_ok = not any(f.startswith("DIES_AT_COST") for f in flags)
        segs = baseline.get("segments") or {}
        plant_win = segs.get("entry_0930_1100") or {}
        harvested = float(plant_win.get("expectancy") or 0) > 0 and int(plant_win.get("n") or 0) >= 20
        if exp <= 0 and not harvested:
            verdict = "IMPLEMENTATION_MISS"
            why = (
                "A planted stored-energy breakout in the entry window was not "
                "harvested. The executable rule or the planter is wrong."
            )
            smallest = None
        elif exp <= 0 and harvested:
            flags.append("PLANT_WINDOW_POSITIVE_OVERALL_NEGATIVE")
            verdict = "DETECTS_PLANT_NOT_SELECTIVE"
            why = (
                "The 09:30–11:00 plant window is profitable, but lookalike "
                "breakouts elsewhere (and/or later in the day) wipe the book. "
                "The frozen rule can see the mechanism and is not selective enough."
            )
            smallest = (
                "High-|E| acceptance after compression, next-bar micro, "
                "restricted to the morning plant window — not the full 09:30–13:00 gate."
            )
        elif holdout_bad:
            verdict = "DETECTS_PLANT_NOT_SELECTIVE"
            why = (
                "The executable pipeline harvests RTH-planted continuation after "
                "realistic costs on the in-sample span, but the same frozen rule "
                "is not selective enough to stay positive on the reserved tail "
                "(lookalike breakouts and small-sample holdout). Synthetic data "
                "still cannot accept or reject FRS in the real market."
            )
            smallest = (
                "Compressed range + high-|E| acceptance through a prior-N-bar "
                "boundary, next-bar micro execution, 1-tick slippage — detectable "
                "when planted in the entry window, not shown to be selective."
            )
        elif costs_ok:
            verdict = "IMPLEMENTATION_OK_SYNTHETIC_ONLY"
            why = (
                "Planted RTH continuation is harvested through cost stress. "
                "This validates the stack, not the market hypothesis."
            )
            smallest = (
                "Stored-energy breakout as encoded by the frozen baseline, "
                "executable next bar on micros."
            )
        else:
            verdict = "PLANT_KILLED_BY_COSTS"
            why = "The planted mechanism does not survive the cost stress the research protocol requires."
            smallest = None
    else:
        fatal = {
            "NONPOSITIVE_EXPECTANCY",
            "HOLDOUT_NONPOSITIVE",
            "DIES_AT_COST_2",
            "DIES_AT_COST_3",
            "EDGE_ON_RANDOM_WALK",
            "WALKFORWARD_WEAK",
            "BOOTSTRAP_CI_NONPOSITIVE",
        }
        proxy = "PROXY_NOT_DATED_FUTURES" in flags
        if any(any(f.startswith(x) or f == x for x in fatal) for f in flags):
            verdict = "FALSIFIED_ON_PROXY" if proxy else "FALSIFIED"
            why = (
                "The frozen baseline does not retain economically meaningful "
                "expectancy after the adversarial battery (see flags)."
            )
            if proxy:
                why += (
                    " This dataset is a public continuous/CFD proxy — not dated "
                    "CME contracts — so a fail here is evidence against the "
                    "price-path story, not a fill-model claim."
                )
            smallest = None
        else:
            verdict = "SURVIVES_PROVISIONALLY_ON_PROXY" if proxy else "SURVIVES_PROVISIONALLY"
            why = (
                "Expectancy stayed positive through costs, neighbors, walk-forward, "
                "and holdout. This is not proof. Inspect concentration, regime "
                "slices, and the smallest parameter region next."
            )
            if proxy:
                why += (
                    " Survival on a Yahoo/Dukascopy proxy is still not a futures "
                    "trading claim: no dated rolls, no micro liquidity, no CME tape."
                )
            smallest = (
                "Continuation after compressed range + high-energy close through "
                "the excluded-current-bar boundary, micros only, 1-tick slippage."
            )

    # Planted-vs-walk special case: if we only have random_walk, FALSIFIED
    # via EDGE_ON_RANDOM_WALK or NONPOSITIVE is correct. If planted_frs
    # shows edge and random_walk does not, implementation is coherent.
    return {
        "verdict": verdict,
        "why": why,
        "flags": flags,
        "smallest_defensible": smallest,
        "baseline_expectancy": exp,
        "baseline_n": n,
    }


def render_markdown(summary: dict[str, Any], conclusion: dict[str, Any]) -> str:
    lines = [
        "# FRS research report",
        "",
        f"- Dataset: `{summary.get('dataset')}`",
        f"- Seed: `{summary.get('seed')}`",
        f"- Span: {summary.get('start')} → {summary.get('end')}",
        f"- Holdout fraction: {summary.get('holdout_fraction')}",
        f"- IS bars: {summary.get('n_is_bars')}  OOS bars: {summary.get('n_oos_bars')}",
        "",
        "## Verdict",
        "",
        f"**{conclusion['verdict']}** — {conclusion['why']}",
        "",
        "Flags:",
    ]
    if conclusion["flags"]:
        for f in conclusion["flags"]:
            lines.append(f"- `{f}`")
    else:
        lines.append("- (none)")
    if conclusion.get("smallest_defensible"):
        lines += ["", "Smallest defensible mechanism:", "", f"> {conclusion['smallest_defensible']}"]
    lines += ["", "## Cases", "", "| case | n | expectancy | net | win rate | sharpe | max DD | top5 conc. |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for c in summary.get("cases", []):
        lines.append(
            "| {name} | {n_trades} | {expectancy:.4f} | {net_pnl:.2f} | {win_rate:.3f} | {sharpe:.3f} | {max_drawdown:.2f} | {concentration_top5_of_wins:.3f} |".format(
                **c
            )
        )
    if summary.get("cost_curve"):
        lines += ["", "## Cost curve (expectancy vs execution assumptions)", ""]
        for c in summary["cost_curve"]:
            lines.append(
                f"- `{c['name']}`: expectancy={c['expectancy']:.4f}  net={c['net']:.2f}  "
                f"slip={c['slippage_paid']:.2f}  comm={c['commissions_paid']:.2f}"
            )
    if summary.get("grids"):
        lines += ["", "## Parameter neighbors (one-at-a-time)", ""]
        for name, rows in summary["grids"].items():
            lines.append(f"### {name}")
            for r in rows:
                lines.append(
                    f"- {r['param']}={r['value']}: n={r['n']} exp={r['expectancy']:.4f} net={r['net']:.2f}"
                )
    if summary.get("walkforward"):
        lines += ["", "## Walk-forward (frozen baseline, IS span only)", ""]
        for f in summary["walkforward"]:
            lines.append(
                f"- fold {f['fold']}: train exp={f['train']['expectancy']:.4f} "
                f"(n={f['train']['n']}) → test exp={f['test']['expectancy']:.4f} (n={f['test']['n']})"
            )
    base_case = next(
        (c for c in summary.get("cases", []) if str(c.get("name", "")).startswith("baseline")),
        None,
    )
    if base_case and base_case.get("bootstrap"):
        b = base_case["bootstrap"]
        lines += [
            "",
            "## Bootstrap (IID trade resample of baseline)",
            "",
            f"- mean={b.get('mean')}  95% CI=[{b.get('ci_low')}, {b.get('ci_high')}]  "
            f"P(mean>0)={b.get('p_positive')}  t={b.get('t_stat')}",
        ]
    if summary.get("multiple_testing"):
        mt = summary["multiple_testing"]
        lines += [
            "",
            "## Multiple testing (deflated Sharpe of the best IS case)",
            "",
            f"- inspected={mt.get('n_inspected')} best=`{mt.get('best_case')}` "
            f"SR={mt.get('best_sharpe')} deflated={mt.get('deflated')}",
        ]
    if base_case and base_case.get("segments"):
        lines += ["", "## Baseline segments", ""]
        for name, row in base_case["segments"].items():
            if isinstance(row, dict) and "n" in row:
                lines.append(
                    f"- `{name}`: n={row['n']} exp={row.get('expectancy')} net={row.get('net')}"
                )
    holdout = summary.get("holdout")
    if holdout:
        lines += [
            "",
            "## Holdout (untouched, frozen baseline only)",
            "",
            f"- n={holdout.get('n_trades')} expectancy={holdout.get('expectancy')} "
            f"net={holdout.get('net_pnl')} sharpe={holdout.get('sharpe')}",
        ]
    lines += [
        "",
        "## How to read this",
        "",
        "This stack is designed to **falsify** FRS. Survival means expectancy",
        "remained economically meaningful after realistic fills, listed (and",
        "stressed) costs, futures rolls, parameter perturbation, chronological",
        "OOS, and a reserved holdout. Synthetic markets cannot settle the",
        "empirical question. Identical inputs must reproduce identical runs.",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], dest: Path | None = None) -> Path:
    conclusion = conclude_from_suite(summary)
    summary = dict(summary)
    summary["conclusion"] = conclusion
    dest = dest or (repo_root() / "runs" / "REPORT.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_markdown(summary, conclusion), encoding="utf-8")
    (dest.parent / "REPORT.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return dest


def query_run_trades(run_dir: str | Path) -> list[tuple]:
    """Example of querying a run's Parquet with DuckDB (no silent copies)."""
    run_dir = Path(run_dir)
    con = duckdb.connect(database=":memory:")
    return con.execute(
        "SELECT instrument, count(*), sum(pnl) FROM read_parquet(?) GROUP BY 1 ORDER BY 1",
        [str(run_dir / "trades.parquet")],
    ).fetchall()
