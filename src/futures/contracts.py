"""Dated futures contract specifications.

GC and MGC, ES and MES, NQ and MNQ are first-class separate instruments.
A continuous series is not a contract and must never be submitted to the
broker as an execution target.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


MONTH_CODES: dict[int, str] = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}
CODE_TO_MONTH: dict[str, int] = {v: k for k, v in MONTH_CODES.items()}


def contract_code(root: str, year: int, month: int) -> str:
    yy = year % 100
    return f"{root}{MONTH_CODES[month]}{yy:02d}"


def parse_contract_code(code: str) -> tuple[str, int, int]:
    """Parse e.g. ESH21 → ('ES', 2021, 3). Roots may be 2 or 3 letters."""
    if len(code) < 5:
        raise ValueError(f"bad contract code {code!r}")
    yy = int(code[-2:])
    month_code = code[-3]
    root = code[:-3]
    if month_code not in CODE_TO_MONTH:
        raise ValueError(f"bad month code in {code!r}")
    year = 2000 + yy if yy < 80 else 1900 + yy
    return root, year, CODE_TO_MONTH[month_code]


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    name: str
    exchange: str
    asset_class: str
    role: str
    currency: str
    multiplier: float
    tick_size: float
    tick_value: float
    contract_months: tuple[str, ...]
    cycle: str
    settlement: str
    rth_start: str
    rth_end: str
    commission_per_side: float
    related_micro: str | None = None
    related_full: str | None = None

    def __post_init__(self) -> None:
        expected = self.multiplier * self.tick_size
        if abs(expected - self.tick_value) > 1e-9:
            raise ValueError(
                f"{self.symbol}: tick_value {self.tick_value} != "
                f"multiplier {self.multiplier} * tick_size {self.tick_size} (= {expected})"
            )
        if self.tick_size <= 0 or self.multiplier <= 0:
            raise ValueError(f"{self.symbol}: tick_size and multiplier must be positive")

    def snap(self, price: float) -> float:
        ticks = round(price / self.tick_size)
        return ticks * self.tick_size

    def ticks_between(self, a: float, b: float) -> int:
        return int(round((b - a) / self.tick_size))

    def dollar_per_point(self) -> float:
        return self.multiplier

    def pnl(self, qty: int, entry: float, exit: float, direction: int) -> float:
        return direction * qty * (exit - entry) * self.multiplier


def spec_from_mapping(symbol: str, raw: dict[str, Any]) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=symbol,
        name=raw["name"],
        exchange=raw["exchange"],
        asset_class=raw["asset_class"],
        role=raw["role"],
        currency=raw.get("currency", "USD"),
        multiplier=float(raw["multiplier"]),
        tick_size=float(raw["tick_size"]),
        tick_value=float(raw["tick_value"]),
        contract_months=tuple(raw["contract_months"]),
        cycle=raw.get("cycle", "quarterly"),
        settlement=raw.get("settlement", "financial"),
        rth_start=raw["rth_start"],
        rth_end=raw["rth_end"],
        commission_per_side=float(raw["commission_per_side"]),
        related_micro=raw.get("related_micro"),
        related_full=raw.get("related_full"),
    )


class InstrumentRegistry:
    def __init__(self, specs: dict[str, InstrumentSpec], raw_config: dict[str, Any] | None = None) -> None:
        self.specs = specs
        self.raw_config = raw_config or {}

    def __getitem__(self, symbol: str) -> InstrumentSpec:
        return self.specs[symbol]

    def get(self, symbol: str) -> InstrumentSpec:
        return self.specs[symbol]

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.specs

    def symbols(self) -> list[str]:
        return sorted(self.specs)

    def tradeable(self) -> list[InstrumentSpec]:
        return [s for s in self.specs.values() if s.role == "trade"]

    def signals(self) -> list[InstrumentSpec]:
        return [s for s in self.specs.values() if s.role == "signal"]

    @classmethod
    def from_yaml(cls, path: str | Path) -> InstrumentRegistry:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        specs = {sym: spec_from_mapping(sym, body) for sym, body in raw["instruments"].items()}
        return cls(specs, raw)

    def roll_days_before_expiry(self, override: int | None = None) -> int:
        if override is not None:
            return int(override)
        return int(self.raw_config.get("rolls", {}).get("scheduled_days_before_expiry", 8))


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday(): Mon=0 ... Fri=4
    offset = (4 - d.weekday()) % 7
    first_friday = d.replace(day=1 + offset)
    return first_friday.replace(day=first_friday.day + 14)


def nth_last_business_day(year: int, month: int, n: int = 3) -> date:
    """Nth last weekday of the month (gold last-trade approximation)."""
    if month == 12:
        d = date(year + 1, 1, 1)
    else:
        d = date(year, month + 1, 1)
    from datetime import timedelta

    d = d - timedelta(days=1)
    found = 0
    while True:
        if d.weekday() < 5:
            found += 1
            if found == n:
                return d
        d = d - timedelta(days=1)


def expiry_for(spec: InstrumentSpec, year: int, month: int) -> date:
    if spec.asset_class == "index":
        return third_friday(year, month)
    return nth_last_business_day(year, month, 3)
