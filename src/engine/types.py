"""Shared market-data and error types for the engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class LookAheadError(RuntimeError):
    """Raised when code attempts to observe or act on a future timestamp."""


class EngineError(RuntimeError):
    """Unrecoverable engine invariant violation."""


@dataclass(frozen=True)
class Bar:
    """A completed OHLCV bar.

    ``ts`` is the bar's *close* timestamp and is the first moment the bar
    is visible to the strategy. It is not a valid fill time for orders
    submitted at ``ts``.
    """

    ts: datetime
    instrument: str
    contract: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    session_date: date | None = None
    is_rth: bool = True
    open_interest: float | None = None
    cont_open: float | None = None
    cont_high: float | None = None
    cont_low: float | None = None
    cont_close: float | None = None

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"high < low at {self.ts} {self.instrument}")
        if self.high < max(self.open, self.close) - 1e-12:
            raise ValueError(f"high below open/close at {self.ts} {self.instrument}")
        if self.low > min(self.open, self.close) + 1e-12:
            raise ValueError(f"low above open/close at {self.ts} {self.instrument}")

    def signal_ohlc(self, series: str) -> tuple[float, float, float, float]:
        """Prices the strategy is allowed to use for this bar."""
        if series == "continuous_backward_ratio":
            o = self.cont_open if self.cont_open is not None else self.open
            h = self.cont_high if self.cont_high is not None else self.high
            l = self.cont_low if self.cont_low is not None else self.low
            c = self.cont_close if self.cont_close is not None else self.close
            return o, h, l, c
        if series == "front_contract":
            return self.open, self.high, self.low, self.close
        raise ValueError(f"unknown signal series {series!r}")

    def trade_ohlc(self) -> tuple[float, float, float, float]:
        """Raw dated-contract prices used for fills. Never continuous."""
        return self.open, self.high, self.low, self.close


@dataclass(frozen=True)
class SignalBar:
    """Strategy-facing bar in the chosen signal price space."""

    ts: datetime
    instrument: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    session_date: date | None
    is_rth: bool
    raw_close: float
