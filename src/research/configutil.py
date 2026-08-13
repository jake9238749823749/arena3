"""YAML loading and scenario overlay."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a mapping")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def set_path(cfg: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    out = deepcopy(cfg)
    cur: dict[str, Any] = out
    parts = dotted.split(".")
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value
    return out


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_strategy(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else repo_root() / "config" / "strategy.yaml"
    return load_yaml(p)


def load_scenarios(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else repo_root() / "config" / "scenarios.yaml"
    return load_yaml(p)


def materialize_scenario(base: dict[str, Any], scenario_body: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in scenario_body.items() if k not in {"description", "grid", "mode", "inherits"}}
    return deep_merge(base, body)
