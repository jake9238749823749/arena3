"""Command-line entry point: ``frs``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research.configutil import load_strategy, repo_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="frs", description="FRS falsification-first research CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_syn = sub.add_parser("synthesize", help="Write deterministic synthetic parquet")
    p_syn.add_argument("--scenario", default="random_walk", choices=["random_walk", "planted_frs"])
    p_syn.add_argument("--seed", type=int, default=42)
    p_syn.add_argument("--start", default="2021-01-01")
    p_syn.add_argument("--end", default="2022-12-31")
    p_syn.add_argument("--out", default=None)

    p_ft = sub.add_parser("fetch", help="Download public proxy market paths (Yahoo / Dukascopy)")
    p_ft.add_argument("--source", required=True, choices=["yahoo_1h", "yahoo_30m", "dukascopy_m30", "stooq_daily"])
    p_ft.add_argument("--start", default="2021-01-01")
    p_ft.add_argument("--end", default="2024-12-31")

    p_ing = sub.add_parser("ingest", help="Normalize data/raw into data/parquet (raw is immutable)")
    p_ing.add_argument("--raw", default=str(repo_root() / "data" / "raw"))
    p_ing.add_argument("--out", default=str(repo_root() / "data" / "parquet" / "vendor"))

    p_val = sub.add_parser("validate", help="Validate a parquet tree; never patches")
    p_val.add_argument("--root", default=str(repo_root() / "data" / "parquet"))

    p_bt = sub.add_parser("backtest", help="Run one scenario")
    p_bt.add_argument("--scenario", default="baseline")
    p_bt.add_argument("--dataset", default="random_walk")
    p_bt.add_argument("--start", default="2021-01-01")
    p_bt.add_argument("--end", default="2022-12-31")
    p_bt.add_argument("--seed", type=int, default=42)
    p_bt.add_argument("--which", default="is", choices=["is", "oos"])
    p_bt.add_argument("--no-persist", action="store_true")

    p_rb = sub.add_parser("robustness", help="Run a research suite")
    p_rb.add_argument("--suite", default="standard")
    p_rb.add_argument("--dataset", default=None)
    p_rb.add_argument("--start", default=None)
    p_rb.add_argument("--end", default=None)
    p_rb.add_argument("--seed", type=int, default=None)
    p_rb.add_argument("--no-persist", action="store_true")

    p_rep = sub.add_parser("report", help="Render REPORT.md from a suite JSON or the latest suite")
    p_rep.add_argument("--suite-json", default=None)
    p_rep.add_argument("--run", default=None, help="latest | path to a run dir")

    p_co = sub.add_parser("compose", help="Merge several suite JSON files into one memo")
    p_co.add_argument("suites", nargs="+")
    p_co.add_argument("--out", default=None)

    p_an = sub.add_parser("analyze", help="Opportunity-cost summary for a run directory")
    p_an.add_argument("--run", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "fetch":
        from data.fetch_market import fetch_and_store
        from futures.contracts import InstrumentRegistry

        registry = InstrumentRegistry.from_yaml(repo_root() / "config" / "instruments.yaml")
        dest = fetch_and_store(
            args.source,
            repo=repo_root(),
            registry=registry,
            start=args.start,
            end=args.end,
        )
        print(json.dumps({"out": str(dest), "source": args.source}, indent=2))
        return 0

    if args.cmd == "synthesize":
        from data.synthetic import synthesize
        from futures.contracts import InstrumentRegistry

        out = Path(args.out) if args.out else repo_root() / "data" / "parquet" / args.scenario
        registry = InstrumentRegistry.from_yaml(repo_root() / "config" / "instruments.yaml")
        by = synthesize(
            start=args.start,
            end=args.end,
            seed=args.seed,
            scenario=args.scenario,
            out_dir=out,
            registry=registry,
        )
        print(json.dumps({"out": str(out), "rows": {k: len(v) for k, v in by.items()}}, indent=2))
        return 0

    if args.cmd == "ingest":
        from data.ingest import ingest_raw

        report = ingest_raw(args.raw, args.out)
        print(json.dumps(report.to_dict(), indent=2, default=str))
        return 0 if report.ok else 2

    if args.cmd == "validate":
        from data.validate import validate_parquet

        report = validate_parquet(args.root)
        print(json.dumps(report.to_dict(), indent=2, default=str))
        return 0 if report.ok else 2

    if args.cmd == "backtest":
        from research.backtest import run_backtest
        from research.configutil import load_scenarios, materialize_scenario

        base = load_strategy()
        sc = load_scenarios()
        body = sc["scenarios"].get(args.scenario, {"description": args.scenario})
        cfg = materialize_scenario(base, body)
        result, dest, metrics = run_backtest(
            cfg,
            scenario_name=args.scenario,
            dataset=args.dataset,
            start=args.start,
            end=args.end,
            seed=args.seed,
            which=args.which,
            persist=not args.no_persist,
        )
        print(
            json.dumps(
                {
                    "run": str(dest) if dest else None,
                    "n_trades": metrics["n_trades"],
                    "expectancy": metrics["expectancy"],
                    "net_pnl": metrics["net_pnl"],
                    "final_equity": metrics["final_equity"],
                    "sharpe": metrics["sharpe"],
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "robustness":
        from research.report import write_report
        from research.robustness import run_suite, write_suite_summary

        summary = run_suite(
            args.suite,
            dataset=args.dataset,
            persist=not args.no_persist,
            start=args.start,
            end=args.end,
            seed=args.seed,
        )
        suite_path = write_suite_summary(summary)
        report_path = write_report(summary)
        print(json.dumps({"suite": str(suite_path), "report": str(report_path), "verdict": summary.get("conclusion", {})}, indent=2, default=str))
        # write_report attaches conclusion to a copy; attach here too for the print.
        from research.report import conclude_from_suite

        print("VERDICT", conclude_from_suite(summary)["verdict"])
        return 0

    if args.cmd == "report":
        from research.report import conclude_from_suite, load_run, render_markdown, write_report, _latest_run

        if args.suite_json:
            summary = json.loads(Path(args.suite_json).read_text(encoding="utf-8"))
            path = write_report(summary)
            print(path)
            return 0
        if args.run:
            runs = repo_root() / "runs"
            run_dir = _latest_run(runs) if args.run == "latest" else Path(args.run)
            if run_dir is None:
                print("no runs found", file=sys.stderr)
                return 2
            payload = load_run(run_dir)
            # Single-run mini report.
            mini = {
                "dataset": payload["meta"].get("dataset"),
                "seed": payload["meta"].get("seed"),
                "start": None,
                "end": None,
                "holdout_fraction": None,
                "n_is_bars": None,
                "n_oos_bars": None,
                "cases": [
                    {
                        "name": payload["meta"].get("scenario"),
                        "n_trades": payload["metrics"]["n_trades"],
                        "expectancy": payload["metrics"]["expectancy"],
                        "net_pnl": payload["metrics"]["net_pnl"],
                        "win_rate": payload["metrics"]["win_rate"],
                        "sharpe": payload["metrics"]["sharpe"],
                        "max_drawdown": payload["metrics"]["max_drawdown"],
                        "concentration_top5_of_wins": payload["metrics"]["concentration_top5_of_wins"],
                    }
                ],
            }
            conclusion = conclude_from_suite(mini)
            text = render_markdown(mini, conclusion)
            out = run_dir / "conclusion.md"
            out.write_text(text, encoding="utf-8")
            print(out)
            return 0
        print("provide --suite-json or --run", file=sys.stderr)
        return 2

    if args.cmd == "compose":
        from research.compose import write_compose

        dest = Path(args.out) if args.out else repo_root() / "runs" / "COMPOSE.md"
        path = write_compose(args.suites, dest)
        print(path)
        return 0

    if args.cmd == "analyze":
        import pandas as pd
        from research.opportunity import analyze_rejected

        run = Path(args.run)
        if args.run == "latest":
            from research.report import _latest_run

            found = _latest_run(repo_root() / "runs")
            if found is None:
                print("no runs", file=sys.stderr)
                return 2
            run = found
        rejected_path = run / "rejected_signals.parquet"
        trades_path = run / "trades.parquet"
        rejected = pd.read_parquet(rejected_path).to_dict("records") if rejected_path.exists() else []
        print(json.dumps(analyze_rejected(rejected), indent=2, default=str))
        if trades_path.exists():
            t = pd.read_parquet(trades_path)
            print("trades", len(t), "net", float(t["pnl"].sum()) if len(t) and "pnl" in t.columns else None)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
