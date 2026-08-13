"""Compose several suite JSON files into one research memo."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.report import conclude_from_suite


def load_suite(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compose(paths: list[str | Path]) -> str:
    lines = [
        "# FRS composed research memo",
        "",
        "Each block is one suite. Suites are independent. Holdouts listed",
        "here were not used to pick parameters inside that suite.",
        "",
    ]
    for path in paths:
        summary = load_suite(path)
        if "conclusion" not in summary:
            summary["conclusion"] = conclude_from_suite(summary)
        c = summary["conclusion"]
        lines += [
            f"## `{summary.get('dataset')}`  {summary.get('start')} → {summary.get('end')}",
            "",
            f"- file: `{path}`",
            f"- verdict: **{c.get('verdict')}**",
            f"- flags: {', '.join(c.get('flags') or []) or '(none)'}",
            f"- baseline n={c.get('baseline_n')} exp={c.get('baseline_expectancy')}",
            "",
        ]
        for case in summary.get("cases", []):
            if case["name"] in {"baseline", "holdout_baseline", "morning_highE", "target_first"} or case["name"].startswith("cost_"):
                lines.append(
                    f"- `{case['name']}`: n={case['n_trades']} exp={case['expectancy']:.3f} net={case['net_pnl']:.1f}"
                )
        lines.append("")
        if c.get("why"):
            lines += [c["why"], ""]
    return "\n".join(lines) + "\n"


def write_compose(paths: list[str | Path], dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(compose(paths), encoding="utf-8")
    return dest
