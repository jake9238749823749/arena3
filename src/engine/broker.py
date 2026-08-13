"""Broker: order book, OCO, cancel/replace, conservative matching.

The broker never looks at future bars. It is given one bar at a time and
the current clock. Fills are applied to the shared portfolio immediately
and emitted to listeners.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from engine.fills import Fill, both_touched, match_order
from engine.orders import Order, OrderStatus, OrderType, TimeInForce
from engine.portfolio import Portfolio
from engine.types import Bar
from futures.contracts import InstrumentSpec


FillListener = Callable[[Fill, Order], None]


@dataclass
class BrokerConfig:
    slippage_ticks: int = 1
    limit_fill_on_touch: bool = True
    stop_gap_policy: str = "worse_of_open_and_stop"
    intrabar_ambiguity: str = "worst_case"
    commission_multiplier: float = 1.0


class Broker:
    def __init__(
        self,
        portfolio: Portfolio,
        specs: dict[str, InstrumentSpec],
        config: BrokerConfig | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.specs = specs
        self.config = config or BrokerConfig()
        self.orders: dict[int, Order] = {}
        self._order_seq = 0
        self._fill_seq = 0
        self.fills: list[Fill] = []
        self.listeners: list[FillListener] = []
        self.rejected: list[Order] = []

    def on_fill(self, fn: FillListener) -> None:
        self.listeners.append(fn)

    def next_order_id(self) -> int:
        self._order_seq += 1
        return self._order_seq

    def submit(self, order: Order) -> Order:
        if order.order_id == 0:
            order.order_id = self.next_order_id()
        spec = self.specs.get(order.instrument)
        if spec is None:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "unknown_instrument"
            self.rejected.append(order)
            return order
        if order.contract == "":
            order.status = OrderStatus.REJECTED
            order.reject_reason = "missing_contract"
            self.rejected.append(order)
            return order
        if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and order.limit_price is None:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "missing_limit"
            self.rejected.append(order)
            return order
        if order.order_type in {OrderType.STOP, OrderType.STOP_MARKET, OrderType.STOP_LIMIT} and order.stop_price is None:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "missing_stop"
            self.rejected.append(order)
            return order
        order.status = OrderStatus.NEW
        self.orders[order.order_id] = order
        return order

    def cancel(self, order_id: int, reason: str = "cancel") -> Order | None:
        order = self.orders.get(order_id)
        if order is None or order.is_terminal:
            return order
        if order.status == OrderStatus.PENDING_NEW:
            order.transition(OrderStatus.CANCELED)
        elif order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}:
            order.transition(OrderStatus.CANCELED)
        elif order.status == OrderStatus.PENDING_CANCEL:
            order.transition(OrderStatus.CANCELED)
        order.meta["cancel_reason"] = reason
        return order

    def cancel_instrument(self, instrument: str, reason: str) -> list[Order]:
        out = []
        for order in list(self.orders.values()):
            if order.instrument == instrument and order.is_open:
                self.cancel(order.order_id, reason)
                out.append(order)
        return out

    def cancel_contract(self, instrument: str, contract: str, reason: str) -> list[Order]:
        out = []
        for order in list(self.orders.values()):
            if order.instrument == instrument and order.contract == contract and order.is_open:
                self.cancel(order.order_id, reason)
                out.append(order)
        return out

    def cancel_oco_group(self, group: str, except_id: int | None, reason: str) -> None:
        for order in list(self.orders.values()):
            if order.oco_group == group and order.order_id != except_id and order.is_open:
                self.cancel(order.order_id, reason)

    def replace(
        self,
        order_id: int,
        *,
        quantity: int | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        ts=None,
        tag: str = "replace",
    ) -> Order:
        old = self.orders[order_id]
        if old.is_terminal:
            raise ValueError("cannot replace a terminal order")
        new = Order(
            order_id=self.next_order_id(),
            instrument=old.instrument,
            contract=old.contract,
            side=old.side,
            quantity=quantity if quantity is not None else old.remaining,
            order_type=old.order_type,
            submitted_ts=ts if ts is not None else old.submitted_ts,
            limit_price=old.limit_price if limit_price is None else limit_price,
            stop_price=old.stop_price if stop_price is None else stop_price,
            tif=old.tif,
            oco_group=old.oco_group,
            parent_id=old.order_id,
            tag=tag,
            min_bars_before_fill=1,
            replaces=old.order_id,
            meta=dict(old.meta),
        )
        old.replaced_by = new.order_id
        old.transition(OrderStatus.REPLACED)
        return self.submit(new)

    def working(self) -> list[Order]:
        return [o for o in self.orders.values() if o.is_working]

    def working_for(self, instrument: str, contract: str | None = None) -> list[Order]:
        out = []
        for o in self.orders.values():
            if not o.is_working:
                continue
            if o.instrument != instrument:
                continue
            if contract is not None and o.contract != contract:
                continue
            out.append(o)
        return out

    def _commission(self, spec: InstrumentSpec, qty: int) -> float:
        return spec.commission_per_side * self.config.commission_multiplier * qty

    def _emit(self, fill: Fill, order: Order) -> None:
        self.fills.append(fill)
        self.portfolio.apply_fill(fill)
        for fn in self.listeners:
            fn(fill, order)

    def _make_fill(self, order: Order, bar: Bar, price: float, reason: str, slippage_ticks: int, liquidity: str, ambiguous: bool) -> Fill:
        spec = self.specs[order.instrument]
        qty = order.remaining
        commission = self._commission(spec, qty)
        slippage_paid = slippage_ticks * spec.tick_value * qty
        self._fill_seq += 1
        fill = Fill(
            fill_id=self._fill_seq,
            order_id=order.order_id,
            ts=bar.ts,
            instrument=order.instrument,
            contract=order.contract,
            side=order.side,
            quantity=qty,
            price=price,
            commission=commission,
            slippage_ticks=slippage_ticks,
            slippage_paid=slippage_paid,
            liquidity=liquidity,
            reason=reason,
            ambiguous=ambiguous,
            bar_ts=bar.ts,
        )
        order.apply_fill(qty, price, commission, slippage_paid)
        return fill

    def on_bar(self, bar: Bar) -> list[Fill]:
        """Increment delay counters and match working orders for this contract.

        Matching order on one bar:
        1. Market orders (at open).
        2. OCO groups: if both children would fill, apply worst-case.
        3. Remaining stops and limits.
        """
        produced: list[Fill] = []
        relevant = [
            o
            for o in self.orders.values()
            if o.is_working and o.instrument == bar.instrument and o.contract == bar.contract
        ]
        for order in relevant:
            if bar.ts > order.submitted_ts:
                order.bars_seen += 1

        markets = [o for o in relevant if o.order_type is OrderType.MARKET and o.is_working]
        # Stable order: older first.
        markets.sort(key=lambda o: (o.submitted_ts, o.order_id))
        for order in markets:
            if order.tif is TimeInForce.IOC or True:
                dec = match_order(
                    order,
                    bar,
                    self.specs[order.instrument],
                    slippage_ticks=self.config.slippage_ticks,
                    limit_fill_on_touch=self.config.limit_fill_on_touch,
                    stop_gap_policy=self.config.stop_gap_policy,
                )
                if dec.fills:
                    fill = self._make_fill(
                        order, bar, dec.price, dec.reason, dec.slippage_ticks, dec.liquidity, False
                    )
                    produced.append(fill)
                    self._emit(fill, order)

        # Refresh relevant after market fills (brackets may have been added
        # by listeners — those are submitted at bar.ts and must NOT fill
        # on this same bar because submitted_ts == bar.ts).
        relevant = [
            o
            for o in self.orders.values()
            if o.is_working and o.instrument == bar.instrument and o.contract == bar.contract
        ]

        handled: set[int] = set()
        groups: dict[str, list[Order]] = {}
        for o in relevant:
            if o.oco_group:
                groups.setdefault(o.oco_group, []).append(o)

        for group, members in sorted(groups.items()):
            stops = [o for o in members if o.order_type in {OrderType.STOP, OrderType.STOP_MARKET, OrderType.STOP_LIMIT}]
            limits = [o for o in members if o.order_type is OrderType.LIMIT]
            if len(stops) == 1 and len(limits) == 1:
                stop, target = stops[0], limits[0]
                if both_touched(stop, target, bar):
                    policy = self.config.intrabar_ambiguity
                    # worst_case / stop_first / both_paths / mark_ambiguous → stop
                    # target_first → optimistic path, used only as a sensitivity
                    winner = target if policy == "target_first" else stop
                    same_bar_ok = bool(winner.meta.get("protect_entry_bar")) and bar.ts == winner.submitted_ts
                    delay_ok = winner.bars_seen >= winner.min_bars_before_fill and bar.ts > winner.submitted_ts
                    if delay_ok or same_bar_ok:
                        dec = match_order(
                            winner,
                            bar,
                            self.specs[winner.instrument],
                            slippage_ticks=self.config.slippage_ticks,
                            limit_fill_on_touch=self.config.limit_fill_on_touch,
                            stop_gap_policy=self.config.stop_gap_policy,
                        )
                        if dec.fills:
                            reason = (
                                "oco_target_first"
                                if winner is target
                                else "oco_worst_case_stop"
                            )
                            fill = self._make_fill(
                                winner,
                                bar,
                                dec.price,
                                reason,
                                dec.slippage_ticks,
                                dec.liquidity,
                                True,
                            )
                            produced.append(fill)
                            self.portfolio.mark_ambiguous(winner.instrument)
                            self._emit(fill, winner)
                            self.cancel_oco_group(group, winner.order_id, "OCO sibling canceled")
                            handled.add(stop.order_id)
                            handled.add(target.order_id)
                            continue

        remaining = [o for o in relevant if o.order_id not in handled and o.is_working]
        remaining.sort(key=lambda o: (o.submitted_ts, o.order_id))
        for order in remaining:
            dec = match_order(
                order,
                bar,
                self.specs[order.instrument],
                slippage_ticks=self.config.slippage_ticks,
                limit_fill_on_touch=self.config.limit_fill_on_touch,
                stop_gap_policy=self.config.stop_gap_policy,
            )
            if not dec.fills:
                continue
            fill = self._make_fill(
                order, bar, dec.price, dec.reason, dec.slippage_ticks, dec.liquidity, False
            )
            produced.append(fill)
            self._emit(fill, order)
            if order.oco_group:
                self.cancel_oco_group(order.oco_group, order.order_id, "OCO sibling canceled")
        return produced

    def expire_day_orders(self, instrument: str, session_date) -> None:
        for order in list(self.orders.values()):
            if (
                order.is_working
                and order.instrument == instrument
                and order.tif is TimeInForce.DAY
                and order.submitted_ts.date() != session_date
            ):
                order.transition(OrderStatus.EXPIRED)
