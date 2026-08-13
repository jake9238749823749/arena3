"""Explicit futures roll schedules.

Continuous-series adjustment gaps are never applied to execution. A roll
is a pair of real orders: close the front dated contract, open the next
dated contract. If the two prices differ, that calendar P&L is real.
If they are equal, the only cost is commission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from futures.contracts import (
    InstrumentSpec,
    contract_code,
    expiry_for,
    MONTH_CODES,
    parse_contract_code,
)

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class RollEvent:
    ts: datetime
    instrument: str
    old_contract: str
    new_contract: str
    expiry: date
    policy: str


def month_cycle(spec: InstrumentSpec) -> list[int]:
    return [next(k for k, v in MONTH_CODES.items() if v == code) for code in spec.contract_months]


def iter_contract_months(spec: InstrumentSpec, start: date, end: date) -> list[tuple[int, int]]:
    months = month_cycle(spec)
    out: list[tuple[int, int]] = []
    year = start.year - 1
    while year <= end.year + 1:
        for m in months:
            out.append((year, m))
        year += 1
    # Keep those whose expiry is in a useful window around [start, end].
    useful = []
    for y, m in out:
        exp = expiry_for(spec, y, m)
        if exp < start - timedelta(days=90):
            continue
        if exp > end + timedelta(days=120):
            continue
        useful.append((y, m))
    return useful


def front_at(spec: InstrumentSpec, on: date, days_before: int) -> str:
    """Dated contract that is front on ``on`` under a scheduled roll policy."""
    candidates = []
    for y, m in iter_contract_months(spec, on - timedelta(days=400), on + timedelta(days=400)):
        exp = expiry_for(spec, y, m)
        roll = exp - timedelta(days=days_before)
        if on <= roll:
            candidates.append((roll, y, m))
    if not candidates:
        raise RuntimeError(f"no front contract for {spec.symbol} on {on}")
    candidates.sort()
    _, y, m = candidates[0]
    return contract_code(spec.symbol, y, m)


class RollCalendar:
    def __init__(self, spec: InstrumentSpec, days_before: int = 8, policy: str = "scheduled") -> None:
        self.spec = spec
        self.days_before = days_before
        self.policy = policy

    def events(self, start: datetime, end: datetime) -> list[RollEvent]:
        start_d = start.astimezone(NY).date()
        end_d = end.astimezone(NY).date()
        evs: list[RollEvent] = []
        months = iter_contract_months(self.spec, start_d, end_d)
        months_sorted = sorted(months)
        for i, (y, m) in enumerate(months_sorted[:-1]):
            exp = expiry_for(self.spec, y, m)
            roll_day = exp - timedelta(days=self.days_before)
            if roll_day < start_d or roll_day > end_d:
                continue
            # Roll at 16:00 ET so the daily 15:45 flat has usually already fired.
            ts = datetime(roll_day.year, roll_day.month, roll_day.day, 16, 0, tzinfo=NY)
            ny, nm = months_sorted[i + 1]
            evs.append(
                RollEvent(
                    ts=ts,
                    instrument=self.spec.symbol,
                    old_contract=contract_code(self.spec.symbol, y, m),
                    new_contract=contract_code(self.spec.symbol, ny, nm),
                    expiry=exp,
                    policy=self.policy,
                )
            )
        return evs

    def map_through_time(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime, str]]:
        """Half-open intervals [a, b) of front contract identity."""
        evs = self.events(start, end)
        if not evs:
            c = front_at(self.spec, start.astimezone(NY).date(), self.days_before)
            return [(start, end, c)]
        intervals: list[tuple[datetime, datetime, str]] = []
        cursor = start
        for ev in evs:
            if ev.ts <= cursor:
                cursor = ev.ts
                continue
            intervals.append((cursor, ev.ts, ev.old_contract))
            cursor = ev.ts
        last = evs[-1].new_contract
        intervals.append((cursor, end, last))
        return intervals


def volume_oi_roll(
    spec: InstrumentSpec,
    bars_by_contract: dict[str, list],
    days_before_floor: int = 2,
) -> list[RollEvent]:
    """Roll when the next contract's volume+OI exceeds the front.

    Requires dated per-contract bars with volume and open_interest. If the
    required fields are missing, the caller must fall back to scheduled.
    """
    # Implemented as a pure function over already-aligned daily aggregates
    # so the engine does not invent liquidity it did not observe.
    raise NotImplementedError(
        "volume/OI rolls require dated per-contract volume and open interest; "
        "pass those series or use the scheduled policy"
    )


def assert_not_continuous(contract: str) -> None:
    if contract.upper() in {"CONTINUOUS", "CONT", "BACK_ADJ", "@"} or contract.endswith("1!"):
        raise ValueError(f"continuous identifier {contract!r} is not tradeable")
    parse_contract_code(contract)  # validates dated form
