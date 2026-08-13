"""Event-driven backtest engine.

Processing order at each timestamp T:

1. Advance the clock to T (refuses to go backwards).
2. Fire timers scheduled at T (force-flat).
3. Apply scheduled rolls at T.
4. For each completed bar with close T, in instrument-name order:
   a. Match working orders (fills only if submitted_ts < T).
   b. Mark-to-market and update MAE/MFE.
   c. Inform the strategy of the completed bar.
5. Time-stop / force-flat exits may submit market orders (fill later).
6. Rank any simultaneous FRS candidates and submit at most one entry.
   Those orders cannot fill until a later bar.

A completed 30-minute bar therefore cannot produce a fill at T.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from engine.broker import Broker, BrokerConfig
from engine.clock import Clock
from engine.events import Event, EventType
from engine.fills import Fill
from engine.orders import Order, OrderType
from engine.portfolio import Portfolio, Trade
from engine.types import Bar, LookAheadError
from futures.contracts import InstrumentRegistry
from futures.rolls import RollEvent, assert_not_continuous
from strategy.frs import FRSStrategy


@dataclass
class EquityPoint:
    ts: datetime
    cash: float
    unrealized: float
    equity: float
    exposure_contracts: int
    open_instruments: int


@dataclass
class EngineConfig:
    starting_cash: float = 100_000.0
    max_positions: int = 1
    slippage_ticks: int = 1
    signal_to_fill_bars: int = 1
    commission_multiplier: float = 1.0
    limit_fill_on_touch: bool = True
    stop_gap_policy: str = "worse_of_open_and_stop"
    intrabar_ambiguity: str = "worst_case"
    flatten_on_roll: bool = True
    timezone: str = "America/New_York"


@dataclass
class EngineResult:
    trades: list[Trade]
    orders: list[Order]
    fills: list[Fill]
    equity: list[EquityPoint]
    rejected_signals: list[dict]
    events: list[Event]
    final_snapshot: dict
    config: dict


class Engine:
    def __init__(
        self,
        strategy: FRSStrategy,
        registry: InstrumentRegistry,
        engine_cfg: EngineConfig | None = None,
        extra_cfg: dict[str, Any] | None = None,
    ) -> None:
        self.strategy = strategy
        self.registry = registry
        self.cfg = engine_cfg or EngineConfig()
        self.extra_cfg = extra_cfg or {}
        self.clock = Clock()
        self.portfolio = Portfolio(self.cfg.starting_cash)
        for spec in registry.specs.values():
            self.portfolio.register_spec(spec)
        self.broker = Broker(
            self.portfolio,
            registry.specs,
            BrokerConfig(
                slippage_ticks=self.cfg.slippage_ticks,
                limit_fill_on_touch=self.cfg.limit_fill_on_touch,
                stop_gap_policy=self.cfg.stop_gap_policy,
                intrabar_ambiguity=self.cfg.intrabar_ambiguity,
                commission_multiplier=self.cfg.commission_multiplier,
            ),
        )
        self.broker.on_fill(self._on_fill)
        self.equity_curve: list[EquityPoint] = []
        self.event_log: list[Event] = []
        self.pending_entry: Order | None = None
        self.pending_exit: Order | None = None
        self.pending_roll_in: Order | None = None
        self.active_instrument: str | None = None
        self.active_contract: str | None = None
        self._roll_queue: list[RollEvent] = []
        self._force_flats: set[datetime] = set()
        self._last_bar_ts: dict[str, datetime] = {}
        self._seen_bar_keys: set[tuple] = set()
        self._exited_this_ts = False

    def _log(self, etype: EventType, payload: Any = None, note: str = "") -> None:
        ev = Event(self.clock.now, etype, self.clock.next_seq(), payload, note)
        self.event_log.append(ev)

    def schedule_force_flats(self, stamps: Iterable[datetime]) -> None:
        self._force_flats = set(stamps)

    def schedule_rolls(self, events: Iterable[RollEvent]) -> None:
        self._roll_queue = sorted(events, key=lambda e: (e.ts, e.instrument))

    def _busy(self) -> bool:
        if not self.portfolio.is_flat():
            return True
        if self.pending_entry is not None and self.pending_entry.is_open:
            return True
        if self.pending_exit is not None and self.pending_exit.is_open:
            return True
        return False

    def _on_fill(self, fill: Fill, order: Order) -> None:
        role = order.meta.get("role")
        if role == "entry" or (self.pending_entry and order.order_id == self.pending_entry.order_id):
            self.pending_entry = None
            if self.portfolio.net_quantity(fill.instrument) == 0:
                self._clear_active()
                return
            self.active_instrument = fill.instrument
            self.active_contract = fill.contract
            qty = abs(self.portfolio.net_quantity(fill.instrument))
            direction = 1 if self.portfolio.net_quantity(fill.instrument) > 0 else -1
            spec = self.registry[fill.instrument]
            group = f"bracket-{fill.order_id}"
            stop, target = self.strategy.make_bracket(
                fill.instrument,
                fill.contract,
                qty,
                direction,
                fill.price,
                fill.ts,
                spec,
                group,
            )
            self.broker.submit(stop)
            self.broker.submit(target)
            self.portfolio.attach_signal_meta(
                fill.instrument,
                self.strategy.last_entry_meta,
                self.strategy.last_competitors,
                entry_atr=self.strategy.entry_atr or 0.0,
                entry_bar_count=self.strategy.entry_bar_count or 0,
            )
            self._log(EventType.FILL, fill, "entry")
            return

        if role == "roll_in" or (self.pending_roll_in and order.order_id == self.pending_roll_in.order_id):
            self.pending_roll_in = None
            qty = abs(self.portfolio.net_quantity(fill.instrument))
            if qty == 0:
                self._clear_active()
                return
            self.active_instrument = fill.instrument
            self.active_contract = fill.contract
            direction = 1 if self.portfolio.net_quantity(fill.instrument) > 0 else -1
            spec = self.registry[fill.instrument]
            group = f"bracket-roll-{fill.order_id}"
            stop, target = self.strategy.make_bracket(
                fill.instrument,
                fill.contract,
                qty,
                direction,
                fill.price,
                fill.ts,
                spec,
                group,
            )
            self.broker.submit(stop)
            self.broker.submit(target)
            self._log(EventType.FILL, fill, "roll_in")
            return

        if role in {"stop", "target", "exit", "force_flat", "time_stop", "roll_out", "daily_flat"}:
            if order.oco_group:
                self.broker.cancel_oco_group(order.oco_group, order.order_id, "OCO sibling canceled")
            if self.portfolio.net_quantity(fill.instrument) == 0:
                self._exited_this_ts = True
                self._clear_active()
            self.pending_exit = None
            self._log(EventType.FILL, fill, role or fill.reason)
            return

        self._log(EventType.FILL, fill, fill.reason)

    def _clear_active(self) -> None:
        self.active_instrument = None
        self.active_contract = None
        self.pending_entry = None
        self.pending_exit = None
        self.pending_roll_in = None
        self.strategy.clear_position_state()

    def request_exit(self, reason: str, ts: datetime) -> None:
        if self.active_instrument is None:
            return
        if self.pending_exit is not None and self.pending_exit.is_open:
            return
        inst = self.active_instrument
        contract = self.active_contract
        if contract is None:
            return
        self.broker.cancel_instrument(inst, reason)
        qty = self.portfolio.net_quantity(inst)
        if qty == 0:
            self._clear_active()
            return
        side = -1 if qty > 0 else 1
        order = Order(
            order_id=0,
            instrument=inst,
            contract=contract,
            side=side,
            quantity=abs(qty),
            order_type=OrderType.MARKET,
            submitted_ts=ts,
            tag=reason,
            min_bars_before_fill=1,
            meta={"role": "exit", "reason": reason},
        )
        self.pending_exit = self.broker.submit(order)
        self._log(EventType.ORDER_SUBMIT, order, reason)

    def _maybe_force_flat(self, ts: datetime) -> None:
        if ts in self._force_flats:
            self.strategy.pending = {}
            self.strategy.pending_ts = None
            self.request_exit("15:45 ET daily flat", ts)
            self._log(EventType.FORCE_FLAT, None, "force_flat")
            self._force_flats.discard(ts)

    def _maybe_rolls(self, ts: datetime) -> None:
        due = [r for r in self._roll_queue if r.ts == ts]
        if not due:
            return
        for ev in due:
            self._apply_roll(ev)
        self._roll_queue = [r for r in self._roll_queue if r.ts != ts]

    def _apply_roll(self, ev: RollEvent) -> None:
        assert_not_continuous(ev.old_contract)
        assert_not_continuous(ev.new_contract)
        self._log(EventType.ROLL, ev, f"{ev.old_contract}->{ev.new_contract}")
        if ev.instrument != self.active_instrument:
            return
        if self.active_contract != ev.old_contract:
            # Strategy mapped contract may already have moved with the data.
            if self.active_contract == ev.new_contract:
                return
            return
        qty = self.portfolio.net_quantity(ev.instrument)
        if qty == 0:
            self.active_contract = ev.new_contract
            return
        self.broker.cancel_contract(ev.instrument, ev.old_contract, "continuous future rollover")
        side_out = -1 if qty > 0 else 1
        out = Order(
            order_id=0,
            instrument=ev.instrument,
            contract=ev.old_contract,
            side=side_out,
            quantity=abs(qty),
            order_type=OrderType.MARKET,
            submitted_ts=ev.ts,
            tag=f"FRS roll out: {ev.old_contract}",
            min_bars_before_fill=1,
            meta={"role": "roll_out"},
        )
        inn = Order(
            order_id=0,
            instrument=ev.instrument,
            contract=ev.new_contract,
            side=-side_out,
            quantity=abs(qty),
            order_type=OrderType.MARKET,
            submitted_ts=ev.ts,
            tag=f"FRS roll in: {ev.new_contract}",
            min_bars_before_fill=1,
            meta={"role": "roll_in"},
        )
        self.broker.submit(out)
        self.pending_roll_in = self.broker.submit(inn)
        self.active_contract = ev.new_contract

    def _record_equity(self, ts: datetime) -> None:
        snap_ok = True
        try:
            self.portfolio.assert_cash_identity()
        except AssertionError as exc:
            snap_ok = False
            identity_error = exc
        else:
            identity_error = None
        self.equity_curve.append(
            EquityPoint(
                ts=ts,
                cash=self.portfolio.cash,
                unrealized=self.portfolio.unrealized_pnl(),
                equity=self.portfolio.equity,
                exposure_contracts=self.portfolio.total_abs_contracts(),
                open_instruments=self.portfolio.open_position_count(),
            )
        )
        if not snap_ok:
            raise RuntimeError(f"cash identity failed: {identity_error}")

    def _process_bar(self, bar: Bar) -> None:
        key = (bar.ts, bar.instrument, bar.contract)
        if key in self._seen_bar_keys:
            raise ValueError(f"duplicate bar {key}")
        self._seen_bar_keys.add(key)
        self.clock.ensure_visible(bar.ts, f"bar {bar.instrument}")

        # Fills first, using only this bar's trade (dated) OHLC.
        if bar.instrument in {p["trade"] for p in self.strategy.pairs} or bar.instrument in self.registry.specs:
            if bar.instrument in self.registry.specs and self.registry[bar.instrument].role == "trade":
                self.broker.on_bar(bar)
                self.portfolio.set_mark(bar.instrument, bar.close)
                spec = self.registry[bar.instrument]
                new_bar = self._last_bar_ts.get(bar.instrument) != bar.ts
                self.portfolio.mark_open_trade(bar.instrument, bar.close, spec, new_bar=new_bar)
                self.strategy.on_trade_bar(bar)

        if bar.instrument in self.strategy.state_by_signal:
            self.strategy.on_signal_bar(bar)

        self._last_bar_ts[bar.instrument] = bar.ts

    def _try_enter(self, ts: datetime) -> None:
        if self.strategy.past_force_flat(ts):
            self.strategy.pending = {}
            self.strategy.pending_ts = None
            return
        if self._busy():
            # Still record blocked candidates for opportunity-cost research.
            self.strategy.choose_entry(ts, in_position=True)
            return
        if not self.strategy.in_entry_window(ts):
            self.strategy.pending = {}
            self.strategy.pending_ts = None
            return
        if self.portfolio.open_position_count() >= self.cfg.max_positions:
            self.strategy.choose_entry(ts, in_position=True)
            return
        cand = self.strategy.choose_entry(ts, in_position=False)
        if cand is None:
            return
        st = self.strategy.states[cand.name]
        contract = st.mapped_contract
        trade_bar = st.last_trade_bar
        if contract is None or trade_bar is None:
            self.strategy.rejected_log.append(
                {"ts": ts.isoformat(), "name": cand.name, "reason": "no_mapped_contract", "score": cand.score}
            )
            return
        assert_not_continuous(contract)
        price = float(trade_bar.close)
        spec = self.registry[cand.trade_instrument]
        qty = self.strategy.size_for(cand, self.portfolio.equity, price, spec)
        if qty < 1:
            return
        self.strategy.note_entry(cand)
        order = self.strategy.make_entry_order(
            cand, contract, qty, ts, self.cfg.signal_to_fill_bars
        )
        self.pending_entry = self.broker.submit(order)
        self.active_instrument = cand.trade_instrument
        self.active_contract = contract
        self._log(EventType.ORDER_SUBMIT, order, "entry")

    def run(self, bars: Iterable[Bar]) -> EngineResult:
        """Replay bars in chronological order. Deterministic given the input."""
        # Materialize and sort. Ties broken by instrument name then contract.
        seq = sorted(bars, key=lambda b: (b.ts, b.instrument, b.contract))
        if not seq:
            return self._result()

        # Group by timestamp.
        grouped: dict[datetime, list[Bar]] = defaultdict(list)
        for b in seq:
            grouped[b.ts].append(b)
        timestamps = sorted(grouped)

        # Merge timer/roll timestamps into the walk.
        extra = set(self._force_flats) | {r.ts for r in self._roll_queue}
        all_ts = sorted(set(timestamps) | extra)

        self.clock.start(all_ts[0])
        for ts in all_ts:
            self.clock.advance(ts)
            self._maybe_force_flat(ts)
            self._maybe_rolls(ts)
            if ts in grouped:
                for bar in grouped[ts]:
                    self._process_bar(bar)
                self._log(EventType.BAR, {"n": len(grouped[ts])}, ts.isoformat())
            reason = self.strategy.time_stop_due()
            if reason:
                self.request_exit(reason, ts)
            self._try_enter(ts)
            self._record_equity(ts)

        # End of data: flatten anything left so the run is a closed book.
        if not self.portfolio.is_flat() and timestamps:
            last = timestamps[-1]
            self.clock.advance(last)
            self.request_exit("end_of_data", last)
            # Cannot fill without another bar; leave the position marked.
        self.portfolio.assert_cash_identity()
        return self._result()

    def _result(self) -> EngineResult:
        return EngineResult(
            trades=list(self.portfolio.trades),
            orders=list(self.broker.orders.values()),
            fills=list(self.broker.fills),
            equity=list(self.equity_curve),
            rejected_signals=list(self.strategy.rejected_log),
            events=list(self.event_log),
            final_snapshot=self.portfolio.reconcile_snapshot(),
            config={
                "starting_cash": self.cfg.starting_cash,
                "slippage_ticks": self.cfg.slippage_ticks,
                "signal_to_fill_bars": self.cfg.signal_to_fill_bars,
                "commission_multiplier": self.cfg.commission_multiplier,
                "intrabar_ambiguity": self.cfg.intrabar_ambiguity,
                "max_positions": self.cfg.max_positions,
            },
        )
