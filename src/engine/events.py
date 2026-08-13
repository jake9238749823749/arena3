"""Event types for the deterministic single-threaded loop.

Events at the same timestamp are processed in ``EventType`` order, then
by insertion sequence. Market data is applied before strategy decisions
at that timestamp; orders submitted at T cannot fill on a bar with ts=T.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any


class EventType(IntEnum):
    SESSION_OPEN = 10
    SESSION_BREAK = 11
    SESSION_CLOSE = 12
    ROLL = 20
    TIMER = 30
    FORCE_FLAT = 35
    BAR = 40
    SIGNAL = 50
    ORDER_SUBMIT = 60
    ORDER_CANCEL = 61
    ORDER_REPLACE = 62
    FILL = 70
    MARK = 80


@dataclass(order=True)
class Event:
    ts: datetime
    etype: EventType
    seq: int
    payload: Any = field(compare=False, default=None)
    note: str = field(compare=False, default="")
