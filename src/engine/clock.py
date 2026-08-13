"""Monotonic research clock. Time only moves forward via events."""

from __future__ import annotations

from datetime import datetime

from engine.types import LookAheadError


class Clock:
    """Single-threaded clock. ``advance`` refuses to move backwards."""

    def __init__(self) -> None:
        self._ts: datetime | None = None
        self._seq: int = 0

    @property
    def now(self) -> datetime:
        if self._ts is None:
            raise RuntimeError("clock has not been started")
        return self._ts

    @property
    def started(self) -> bool:
        return self._ts is not None

    def start(self, ts: datetime) -> None:
        if self._ts is not None:
            raise RuntimeError("clock already started")
        self._ts = ts

    def advance(self, ts: datetime) -> None:
        if self._ts is None:
            self._ts = ts
            return
        if ts < self._ts:
            raise LookAheadError(f"refusing to move clock from {self._ts.isoformat()} to {ts.isoformat()}")
        self._ts = ts

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def ensure_visible(self, ts: datetime, what: str) -> None:
        """Raise if ``ts`` is strictly after now — the object is from the future."""
        if self._ts is None:
            raise LookAheadError(f"{what} observed before clock start")
        if ts > self._ts:
            raise LookAheadError(f"{what} at {ts.isoformat()} is after now {self._ts.isoformat()}")
