"""Frozen baseline Frontier Resolution Switch.

This is a port of the QuantConnect prototype. Changing thresholds or
the entry rule is a new hypothesis — bump ``definition_id``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from engine.orders import Order, OrderType
from engine.types import Bar, SignalBar
from strategy.signals import FRSCandidate, compute_frs_candidate
from strategy.sizing import size_position


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


@dataclass
class MarketState:
    name: str
    signal_instrument: str
    trade_instrument: str
    bars: deque[SignalBar]
    bar_count: int = 0
    last_ts: datetime | None = None
    mapped_contract: str | None = None
    last_trade_bar: Bar | None = None


class FRSStrategy:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.definition_id = cfg.get("definition_id", "frs_baseline_v1")
        signal = cfg["signal"]
        session = cfg["session"]
        self.bar_minutes = int(signal["bar_minutes"])
        self.boundary_lookback = int(signal["boundary_lookback"])
        self.short_atr_period = int(signal["short_atr_period"])
        self.long_atr_period = int(signal["long_atr_period"])
        self.compression_threshold = float(signal["compression_threshold"])
        self.energy_threshold = float(signal["energy_threshold"])
        self.exclude_current = bool(signal.get("exclude_current_bar", True))
        self.series = signal.get("series", "continuous_backward_ratio")
        self.entry_start = _parse_time(session["entry_start"])
        self.entry_cutoff = _parse_time(session["entry_cutoff"])
        self.force_flat_time = _parse_time(session["force_flat"])
        self.stop_atr = float(cfg["exits"]["stop_atr"])
        self.target_atr = float(cfg["exits"]["target_atr"])
        self.max_hold_bars = int(cfg["exits"]["max_hold_bars"])
        self.sizing_cfg = cfg["sizing"]
        self.pairs = list(cfg["execution"]["pairs"])

        maxlen = self.long_atr_period + 2
        self.states: dict[str, MarketState] = {}
        self.state_by_signal: dict[str, MarketState] = {}
        self.state_by_trade: dict[str, MarketState] = {}
        for pair in self.pairs:
            st = MarketState(
                name=pair["name"],
                signal_instrument=pair["signal"],
                trade_instrument=pair["trade"],
                bars=deque(maxlen=maxlen),
            )
            self.states[st.name] = st
            self.state_by_signal[st.signal_instrument] = st
            self.state_by_trade[st.trade_instrument] = st

        self.pending: dict[str, FRSCandidate | None] = {}
        self.pending_ts: datetime | None = None
        self.rejected_log: list[dict] = []
        self.active_name: str | None = None
        self.entry_bar_count: int | None = None
        self.entry_atr: float | None = None
        self.last_entry_meta: dict = {}
        self.last_competitors: list = []

    def in_entry_window(self, ts: datetime) -> bool:
        t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
        return self.entry_start <= t < self.entry_cutoff

    def past_force_flat(self, ts: datetime) -> bool:
        t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
        return t >= self.force_flat_time

    def on_trade_bar(self, bar: Bar) -> None:
        st = self.state_by_trade.get(bar.instrument)
        if st is None:
            return
        st.mapped_contract = bar.contract
        st.last_trade_bar = bar

    def on_signal_bar(self, bar: Bar) -> FRSCandidate | None:
        st = self.state_by_signal.get(bar.instrument)
        if st is None:
            return None
        o, h, l, c = bar.signal_ohlc(self.series)
        sb = SignalBar(
            ts=bar.ts,
            instrument=bar.instrument,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=bar.volume,
            session_date=bar.session_date,
            is_rth=bar.is_rth,
            raw_close=bar.close,
        )
        st.bar_count += 1
        st.bars.append(sb)
        st.last_ts = bar.ts

        cand = compute_frs_candidate(
            st.name,
            st.signal_instrument,
            st.trade_instrument,
            list(st.bars),
            boundary_lookback=self.boundary_lookback,
            short_atr_period=self.short_atr_period,
            long_atr_period=self.long_atr_period,
            compression_threshold=self.compression_threshold,
            energy_threshold=self.energy_threshold,
            bar_count=st.bar_count,
            exclude_current_bar=self.exclude_current,
        )
        if cand is not None and not self.in_entry_window(bar.ts):
            self.rejected_log.append(
                {
                    "ts": bar.ts.isoformat(),
                    "name": st.name,
                    "reason": "outside_entry_window",
                    "score": cand.score,
                    "direction": cand.direction,
                    "energy": cand.energy,
                    "compression": cand.compression,
                }
            )
            cand = None
        if self.pending_ts is None or bar.ts > self.pending_ts:
            self.pending_ts = bar.ts
            self.pending = {name: None for name in self.states}
        self.pending[st.name] = cand
        return cand

    def time_stop_due(self) -> str | None:
        if self.active_name is None or self.entry_bar_count is None:
            return None
        st = self.states[self.active_name]
        if st.bar_count - self.entry_bar_count >= self.max_hold_bars:
            return "time stop"
        return None

    def choose_entry(self, now: datetime, in_position: bool) -> FRSCandidate | None:
        """Select at most one candidate using only information at ``now``."""
        if in_position:
            for name, cand in self.pending.items():
                if cand is not None:
                    self.rejected_log.append(
                        {
                            "ts": now.isoformat(),
                            "name": name,
                            "reason": "blocked_by_position",
                            "score": cand.score,
                            "direction": cand.direction,
                        }
                    )
            self.pending = {}
            self.pending_ts = None
            return None
        if self.pending_ts is None:
            return None
        # Candidates were produced on a completed bar. They may be used
        # at this timestamp (order still cannot fill until a later bar).
        live = [(n, c) for n, c in self.pending.items() if c is not None]
        self.pending = {}
        self.pending_ts = None
        if not live:
            return None
        live.sort(key=lambda kv: (-kv[1].score, kv[0]))
        winner_name, winner = live[0]
        competitors = []
        for name, cand in live[1:]:
            rec = {
                "ts": now.isoformat(),
                "name": name,
                "reason": "outranked",
                "score": cand.score,
                "winner": winner_name,
                "direction": cand.direction,
                "energy": cand.energy,
                "compression": cand.compression,
            }
            self.rejected_log.append(rec)
            competitors.append(rec)
        self.last_competitors = competitors
        return winner

    def size_for(self, candidate: FRSCandidate, equity: float, trade_price: float, spec) -> int:
        # Convert adjusted ATR into raw mapped-contract points.
        signal_price = float(candidate.signal_price)
        if trade_price <= 0 or signal_price <= 0:
            return 0
        raw_atr = candidate.atr * trade_price / signal_price
        stop_distance = raw_atr * self.stop_atr
        self.entry_atr = raw_atr
        decision = size_position(
            method=self.sizing_cfg["method"],
            equity=equity,
            price=trade_price,
            stop_distance=stop_distance,
            spec=spec,
            risk_fraction=float(self.sizing_cfg.get("risk_fraction", 0.0025)),
            notional_cap=float(self.sizing_cfg.get("notional_cap", 1.0)),
            fixed_contracts=int(self.sizing_cfg.get("fixed_contracts", 1)),
            fixed_dollar_risk=float(self.sizing_cfg.get("fixed_dollar_risk", 250.0)),
            vol_target_annual=float(self.sizing_cfg.get("vol_target_annual", 0.10)),
            atr=raw_atr,
        )
        if decision.quantity < 1:
            self.rejected_log.append(
                {
                    "ts": candidate.ts.isoformat(),
                    "name": candidate.name,
                    "reason": decision.reason,
                    "score": candidate.score,
                }
            )
        return decision.quantity

    def note_entry(self, candidate: FRSCandidate) -> None:
        self.active_name = candidate.name
        self.entry_bar_count = candidate.bar_count
        self.last_entry_meta = {
            "name": candidate.name,
            "energy": candidate.energy,
            "compression": candidate.compression,
            "score": candidate.score,
            "atr": candidate.atr,
            "direction": candidate.direction,
            "signal_ts": candidate.ts.isoformat(),
            "definition_id": self.definition_id,
        }

    def clear_position_state(self) -> None:
        self.active_name = None
        self.entry_bar_count = None
        self.entry_atr = None
        self.last_entry_meta = {}
        self.last_competitors = []

    def bracket_prices(self, fill_price: float, direction: int, spec) -> tuple[float, float]:
        assert self.entry_atr is not None
        stop = spec.snap(fill_price - direction * self.entry_atr * self.stop_atr)
        target = spec.snap(fill_price + direction * self.entry_atr * self.target_atr)
        return stop, target

    def make_entry_order(
        self,
        candidate: FRSCandidate,
        contract: str,
        quantity: int,
        submitted_ts: datetime,
        delay_bars: int,
    ) -> Order:
        tag = (
            f"FRS {candidate.name} E={candidate.energy:.3f} "
            f"compression={candidate.compression:.3f} score={candidate.score:.4f}"
        )
        return Order(
            order_id=0,
            instrument=candidate.trade_instrument,
            contract=contract,
            side=candidate.direction,
            quantity=quantity,
            order_type=OrderType.MARKET,
            submitted_ts=submitted_ts,
            tag=tag,
            min_bars_before_fill=delay_bars,
            meta={"role": "entry", "name": candidate.name},
        )

    def make_bracket(
        self,
        instrument: str,
        contract: str,
        quantity: int,
        direction: int,
        fill_price: float,
        submitted_ts: datetime,
        spec,
        oco_group: str,
    ) -> tuple[Order, Order]:
        stop_px, target_px = self.bracket_prices(fill_price, direction, spec)
        exit_side = -direction
        stop = Order(
            order_id=0,
            instrument=instrument,
            contract=contract,
            side=exit_side,
            quantity=quantity,
            order_type=OrderType.STOP_MARKET,
            submitted_ts=submitted_ts,
            stop_price=stop_px,
            tag="FRS protective stop",
            oco_group=oco_group,
            min_bars_before_fill=1,
            meta={"role": "stop", "protect_entry_bar": True},
        )
        target = Order(
            order_id=0,
            instrument=instrument,
            contract=contract,
            side=exit_side,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            submitted_ts=submitted_ts,
            limit_price=target_px,
            tag="FRS profit target",
            oco_group=oco_group,
            min_bars_before_fill=1,
            meta={"role": "target", "protect_entry_bar": True},
        )
        return stop, target
