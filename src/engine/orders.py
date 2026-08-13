"""Orders as distinct objects with an explicit lifecycle.

Orders, fills, positions, and trades are not interchangeable. An order
may produce zero or more fills; a position is the net of fills on one
dated contract; a trade is a completed round-trip assembled by the
portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING_NEW = "pending_new"
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    PENDING_CANCEL = "pending_cancel"
    CANCELED = "canceled"
    REJECTED = "rejected"
    REPLACED = "replaced"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


# Legal single-step transitions. Used by tests and the broker.
LEGAL_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_NEW: frozenset(
        {OrderStatus.NEW, OrderStatus.REJECTED, OrderStatus.CANCELED}
    ),
    OrderStatus.NEW: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.CANCELED,
            OrderStatus.REPLACED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PENDING_CANCEL: frozenset(
        {OrderStatus.CANCELED, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.REPLACED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


@dataclass
class Order:
    order_id: int
    instrument: str
    contract: str
    side: int
    quantity: int
    order_type: OrderType
    submitted_ts: datetime
    status: OrderStatus = OrderStatus.PENDING_NEW
    limit_price: float | None = None
    stop_price: float | None = None
    tif: TimeInForce = TimeInForce.GTC
    oco_group: str | None = None
    parent_id: int | None = None
    tag: str = ""
    filled_qty: int = 0
    avg_fill_price: float | None = None
    commission_paid: float = 0.0
    slippage_paid: float = 0.0
    min_bars_before_fill: int = 1
    bars_seen: int = 0
    reject_reason: str = ""
    replaced_by: int | None = None
    replaces: int | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in (-1, 1):
            raise ValueError("side must be +1 (buy) or -1 (sell)")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")

    @property
    def remaining(self) -> int:
        return self.quantity - self.filled_qty

    @property
    def signed_quantity(self) -> int:
        return self.side * self.quantity

    @property
    def is_open(self) -> bool:
        return self.status in {
            OrderStatus.PENDING_NEW,
            OrderStatus.NEW,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PENDING_CANCEL,
        }

    @property
    def is_working(self) -> bool:
        return self.status in {
            OrderStatus.NEW,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PENDING_CANCEL,
        }

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.REPLACED,
            OrderStatus.EXPIRED,
        }

    def transition(self, new_status: OrderStatus) -> None:
        legal = LEGAL_TRANSITIONS[self.status]
        if new_status not in legal:
            raise ValueError(f"illegal order transition {self.status.value} -> {new_status.value}")
        self.status = new_status

    def apply_fill(self, qty: int, price: float, commission: float, slippage: float) -> None:
        if qty <= 0 or qty > self.remaining:
            raise ValueError(f"bad fill qty {qty} remaining {self.remaining}")
        prev = self.filled_qty
        self.filled_qty += qty
        if self.avg_fill_price is None:
            self.avg_fill_price = price
        else:
            self.avg_fill_price = (self.avg_fill_price * prev + price * qty) / self.filled_qty
        self.commission_paid += commission
        self.slippage_paid += slippage
        if self.filled_qty == self.quantity:
            if self.status == OrderStatus.PENDING_NEW:
                self.status = OrderStatus.FILLED
            else:
                if self.status not in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL}:
                    raise ValueError(f"cannot fill order in status {self.status}")
                self.status = OrderStatus.FILLED
        else:
            if self.status == OrderStatus.PENDING_NEW:
                self.status = OrderStatus.PARTIALLY_FILLED
            elif self.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING_CANCEL}:
                self.status = OrderStatus.PARTIALLY_FILLED
            else:
                raise ValueError(f"cannot partially fill order in status {self.status}")
