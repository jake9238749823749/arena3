"""Shared portfolio, positions, and round-trip trades.

Futures accounting convention used here:

* Opening a position does not debit notional from cash (margin is not
  simulated as a cash lock). Commission is deducted immediately.
* Equity = cash + unrealized variation.
* Realized P&L is booked to cash on reducing fills.
* Multiplier comes from the instrument spec. Continuous-series gaps
  never enter this object.

One portfolio is shared across all instruments. Exposure caps are
enforced by the engine, not by adding independent backtests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from engine.fills import Fill
from futures.contracts import InstrumentSpec


@dataclass
class Position:
    instrument: str
    contract: str
    quantity: int = 0  # signed
    avg_price: float = 0.0
    realized_pnl: float = 0.0
    commission_paid: float = 0.0
    slippage_paid: float = 0.0
    opened_ts: datetime | None = None
    last_ts: datetime | None = None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def direction(self) -> int:
        if self.quantity > 0:
            return 1
        if self.quantity < 0:
            return -1
        return 0

    def unrealized(self, mark: float, spec: InstrumentSpec) -> float:
        if self.quantity == 0:
            return 0.0
        return self.quantity * (mark - self.avg_price) * spec.multiplier


@dataclass
class Trade:
    """A completed round-trip (or the closed portion of a scale-out)."""

    trade_id: int
    instrument: str
    contract: str
    direction: int
    quantity: int
    entry_ts: datetime
    entry_price: float
    entry_order_id: int
    exit_ts: datetime
    exit_price: float
    exit_order_id: int
    pnl: float
    commission: float
    slippage_paid: float
    mae: float
    mfe: float
    bars_held: int
    exit_reason: str
    ambiguous: bool = False
    signal_meta: dict = field(default_factory=dict)
    rejected_competitors: list = field(default_factory=list)


@dataclass
class OpenTrade:
    """In-progress round-trip used for MAE/MFE and later Trade emission."""

    instrument: str
    contract: str
    direction: int
    quantity: int
    entry_ts: datetime
    entry_price: float
    entry_order_id: int
    commission: float
    slippage_paid: float
    mae: float = 0.0  # most adverse, in dollars (negative or zero)
    mfe: float = 0.0  # most favorable, in dollars (positive or zero)
    bars_held: int = 0
    entry_bar_count: int = 0
    entry_atr: float = 0.0
    signal_meta: dict = field(default_factory=dict)
    rejected_competitors: list = field(default_factory=list)
    ambiguous: bool = False
    mark: float = 0.0


class Portfolio:
    def __init__(self, starting_cash: float) -> None:
        if starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.positions: dict[tuple[str, str], Position] = {}
        self.marks: dict[str, float] = {}
        self.specs: dict[str, InstrumentSpec] = {}
        self.trades: list[Trade] = []
        self.open_trades: dict[str, OpenTrade] = {}  # keyed by instrument
        self._trade_seq = 0
        self.realized_pnl = 0.0
        self.total_commission = 0.0
        self.total_slippage = 0.0

    def register_spec(self, spec: InstrumentSpec) -> None:
        self.specs[spec.symbol] = spec

    def position_for(self, instrument: str, contract: str) -> Position:
        key = (instrument, contract)
        if key not in self.positions:
            self.positions[key] = Position(instrument, contract)
        return self.positions[key]

    def net_quantity(self, instrument: str) -> int:
        return sum(p.quantity for (inst, _), p in self.positions.items() if inst == instrument)

    def open_position_count(self) -> int:
        return sum(1 for p in self.positions.values() if not p.is_flat)

    def total_abs_contracts(self) -> int:
        return sum(abs(p.quantity) for p in self.positions.values())

    def is_flat(self) -> bool:
        return all(p.is_flat for p in self.positions.values())

    def set_mark(self, instrument: str, price: float) -> None:
        self.marks[instrument] = price

    def unrealized_pnl(self) -> float:
        total = 0.0
        for pos in self.positions.values():
            if pos.is_flat:
                continue
            spec = self.specs[pos.instrument]
            mark = self.marks.get(pos.instrument, pos.avg_price)
            total += pos.unrealized(mark, spec)
        return total

    @property
    def equity(self) -> float:
        return self.cash + self.unrealized_pnl()

    def apply_fill(self, fill: Fill) -> Trade | None:
        """Book a fill. Returns a Trade if a round-trip (or slice) closed."""
        spec = self.specs[fill.instrument]
        pos = self.position_for(fill.instrument, fill.contract)
        signed = fill.side * fill.quantity
        closed: Trade | None = None

        self.cash -= fill.commission
        self.total_commission += fill.commission
        self.total_slippage += fill.slippage_paid
        pos.commission_paid += fill.commission
        pos.slippage_paid += fill.slippage_paid
        pos.last_ts = fill.ts

        if pos.quantity == 0:
            pos.quantity = signed
            pos.avg_price = fill.price
            pos.opened_ts = fill.ts
            self._open_trade(fill, spec)
            return None

        same_direction = (pos.quantity > 0 and signed > 0) or (pos.quantity < 0 and signed < 0)
        if same_direction:
            new_qty = pos.quantity + signed
            pos.avg_price = (pos.avg_price * abs(pos.quantity) + fill.price * fill.quantity) / abs(new_qty)
            pos.quantity = new_qty
            ot = self.open_trades.get(fill.instrument)
            if ot is not None:
                ot.quantity = abs(new_qty)
                ot.entry_price = pos.avg_price
                ot.commission += fill.commission
                ot.slippage_paid += fill.slippage_paid
            return None

        # Reducing or reversing.
        close_qty = min(abs(pos.quantity), fill.quantity)
        direction = pos.direction
        pnl = direction * close_qty * (fill.price - pos.avg_price) * spec.multiplier
        self.cash += pnl
        self.realized_pnl += pnl
        pos.realized_pnl += pnl

        ot = self.open_trades.get(fill.instrument)
        if ot is not None:
            closed = self._close_trade_slice(ot, fill, close_qty, pnl)

        remaining_pos = abs(pos.quantity) - close_qty
        leftover_fill = fill.quantity - close_qty
        if remaining_pos == 0 and leftover_fill == 0:
            pos.quantity = 0
            pos.avg_price = 0.0
        elif remaining_pos > 0:
            pos.quantity = direction * remaining_pos
        else:
            # reverse
            pos.quantity = fill.side * leftover_fill
            pos.avg_price = fill.price
            pos.opened_ts = fill.ts
            self._open_trade(fill, spec)
        return closed

    def _open_trade(self, fill: Fill, spec: InstrumentSpec) -> None:
        meta = {}
        competitors: list = []
        atr = 0.0
        entry_bar_count = 0
        # meta is attached later by the engine via attach_signal_meta
        self.open_trades[fill.instrument] = OpenTrade(
            instrument=fill.instrument,
            contract=fill.contract,
            direction=fill.side,
            quantity=fill.quantity,
            entry_ts=fill.ts,
            entry_price=fill.price,
            entry_order_id=fill.order_id,
            commission=fill.commission,
            slippage_paid=fill.slippage_paid,
            mark=fill.price,
            signal_meta=meta,
            rejected_competitors=competitors,
            entry_atr=atr,
            entry_bar_count=entry_bar_count,
        )

    def attach_signal_meta(
        self,
        instrument: str,
        meta: dict,
        competitors: list | None = None,
        entry_atr: float = 0.0,
        entry_bar_count: int = 0,
    ) -> None:
        ot = self.open_trades.get(instrument)
        if ot is None:
            return
        ot.signal_meta = dict(meta)
        ot.rejected_competitors = list(competitors or [])
        ot.entry_atr = entry_atr
        ot.entry_bar_count = entry_bar_count

    def mark_open_trade(self, instrument: str, mark: float, spec: InstrumentSpec, new_bar: bool) -> None:
        ot = self.open_trades.get(instrument)
        if ot is None:
            return
        ot.mark = mark
        excursion = ot.direction * (mark - ot.entry_price) * spec.multiplier * ot.quantity
        ot.mae = min(ot.mae, min(0.0, excursion))
        ot.mfe = max(ot.mfe, max(0.0, excursion))
        if new_bar:
            ot.bars_held += 1

    def mark_ambiguous(self, instrument: str) -> None:
        ot = self.open_trades.get(instrument)
        if ot is not None:
            ot.ambiguous = True

    def _close_trade_slice(self, ot: OpenTrade, fill: Fill, qty: int, pnl: float) -> Trade:
        self._trade_seq += 1
        commission = ot.commission + fill.commission
        slippage = ot.slippage_paid + fill.slippage_paid
        # If this is a partial close, attribute pro-rata entry costs.
        if qty < ot.quantity:
            frac = qty / ot.quantity
            commission = ot.commission * frac + fill.commission
            slippage = ot.slippage_paid * frac + fill.slippage_paid
            ot.commission *= 1.0 - frac
            ot.slippage_paid *= 1.0 - frac
            ot.quantity -= qty
        else:
            self.open_trades.pop(ot.instrument, None)
        reason = str(fill.reason)
        trade = Trade(
            trade_id=self._trade_seq,
            instrument=ot.instrument,
            contract=ot.contract,
            direction=ot.direction,
            quantity=qty,
            entry_ts=ot.entry_ts,
            entry_price=ot.entry_price,
            entry_order_id=ot.entry_order_id,
            exit_ts=fill.ts,
            exit_price=fill.price,
            exit_order_id=fill.order_id,
            pnl=pnl - (fill.commission if qty < ot.quantity + qty else 0.0),
            commission=commission,
            slippage_paid=slippage,
            mae=ot.mae,
            mfe=ot.mfe,
            bars_held=ot.bars_held,
            exit_reason=reason,
            ambiguous=ot.ambiguous or fill.ambiguous,
            signal_meta=dict(ot.signal_meta),
            rejected_competitors=list(ot.rejected_competitors),
        )
        # Net P&L for the trade object includes commissions (cash already
        # deducted). ``pnl`` above is gross variation; store net.
        trade.pnl = pnl - (commission if qty == ot.quantity + qty or ot.instrument not in self.open_trades else fill.commission)
        # Simpler, auditable definition: net = variation - commissions on the slice.
        trade.pnl = pnl - commission
        self.trades.append(trade)
        return trade

    def notional(self, instrument: str, price: float, qty: int) -> float:
        spec = self.specs[instrument]
        return abs(qty) * price * spec.multiplier

    def reconcile_snapshot(self) -> dict:
        """Exact accounting snapshot used by tests and run artifacts."""
        return {
            "starting_cash": self.starting_cash,
            "cash": self.cash,
            "unrealized": self.unrealized_pnl(),
            "equity": self.equity,
            "realized_pnl": self.realized_pnl,
            "total_commission": self.total_commission,
            "total_slippage": self.total_slippage,
            "open_positions": [
                {
                    "instrument": p.instrument,
                    "contract": p.contract,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                }
                for p in self.positions.values()
                if not p.is_flat
            ],
            "identity": abs(
                (self.starting_cash + self.realized_pnl - self.total_commission) - self.cash
            ),
        }

    def assert_cash_identity(self, tol: float = 1e-6) -> None:
        expected = self.starting_cash + self.realized_pnl - self.total_commission
        if abs(expected - self.cash) > tol:
            raise AssertionError(
                f"cash identity failed: cash={self.cash} expected={expected} "
                f"realized={self.realized_pnl} commission={self.total_commission}"
            )
