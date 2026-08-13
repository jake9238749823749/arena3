"""Conservative fill model.

Rules (canonical, deliberately pessimistic):

* A bar is only a valid fill event if ``bar.ts > order.submitted_ts``.
* Market orders fill at the next eligible bar's **open**, plus
  ``slippage_ticks`` of adverse ticks.
* Stop / stop-market: trigger if the bar trades through the stop. A gap
  through the stop fills at the worse of bar open and stop, plus slippage.
* Limit: fill if touched, **at the limit**, with no price improvement.
* If a stop and its OCO target are both touched on the same bar and the
  path is unknown, the default is worst-case: the stop fills, the target
  is canceled, and the fill is marked ``ambiguous``.
* Unknown intrabar sequencing is never resolved in the trader's favor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from engine.orders import Order, OrderType
from engine.types import Bar
from futures.contracts import InstrumentSpec


@dataclass(frozen=True)
class Fill:
    fill_id: int
    order_id: int
    ts: datetime
    instrument: str
    contract: str
    side: int
    quantity: int
    price: float
    commission: float
    slippage_ticks: int
    slippage_paid: float
    liquidity: str
    reason: str
    ambiguous: bool = False
    bar_ts: datetime | None = None


@dataclass(frozen=True)
class FillDecision:
    """Result of matching one order against one bar."""

    fills: bool
    price: float = 0.0
    reason: str = ""
    liquidity: str = "taker"
    slippage_ticks: int = 0
    triggered: bool = False


def adverse_slippage(price: float, side: int, spec: InstrumentSpec, ticks: int) -> float:
    """Buyers pay more, sellers receive less."""
    slipped = price + side * ticks * spec.tick_size
    return spec.snap(slipped)


def market_fill_price(bar: Bar, side: int, spec: InstrumentSpec, slippage_ticks: int) -> float:
    return adverse_slippage(bar.open, side, spec, slippage_ticks)


def stop_triggered(order: Order, bar: Bar) -> bool:
    if order.stop_price is None:
        return False
    if order.side > 0:
        return bar.high >= order.stop_price - 1e-12
    return bar.low <= order.stop_price + 1e-12


def limit_touched(order: Order, bar: Bar) -> bool:
    if order.limit_price is None:
        return False
    if order.side > 0:
        return bar.low <= order.limit_price + 1e-12
    return bar.high >= order.limit_price - 1e-12


def stop_fill_price(
    order: Order,
    bar: Bar,
    spec: InstrumentSpec,
    slippage_ticks: int,
    gap_policy: str = "worse_of_open_and_stop",
) -> float:
    """Conservative stop fill. Gaps must hurt."""
    assert order.stop_price is not None
    stop = order.stop_price
    if gap_policy == "worse_of_open_and_stop":
        if order.side > 0:
            raw = max(bar.open, stop)
        else:
            raw = min(bar.open, stop)
    elif gap_policy == "stop_only":
        raw = stop
    else:
        raise ValueError(f"unknown stop gap policy {gap_policy}")
    return adverse_slippage(raw, order.side, spec, slippage_ticks)


def match_order(
    order: Order,
    bar: Bar,
    spec: InstrumentSpec,
    *,
    slippage_ticks: int,
    limit_fill_on_touch: bool = True,
    stop_gap_policy: str = "worse_of_open_and_stop",
) -> FillDecision:
    """Match a working order against a subsequent bar.

    Returns a no-fill decision if the bar is not strictly after the
    submit timestamp or if the delay bar count has not elapsed.
    """
    if bar.instrument != order.instrument or bar.contract != order.contract:
        return FillDecision(False, reason="wrong_instrument")
    protect_entry_bar = bool(order.meta.get("protect_entry_bar"))
    if bar.ts < order.submitted_ts:
        return FillDecision(False, reason="prior_bar")
    if bar.ts == order.submitted_ts and not protect_entry_bar:
        return FillDecision(False, reason="same_or_prior_bar")
    if bar.ts > order.submitted_ts and order.bars_seen < order.min_bars_before_fill:
        return FillDecision(False, reason="delay")
    if bar.ts == order.submitted_ts and protect_entry_bar:
        # Entry filled at this bar's open; protective orders may use the
        # remainder of the same OHLC. Open is assumed first.
        pass

    otype = order.order_type
    if otype is OrderType.MARKET:
        price = market_fill_price(bar, order.side, spec, slippage_ticks)
        return FillDecision(
            True,
            price=price,
            reason="market_next_open",
            liquidity="taker",
            slippage_ticks=slippage_ticks,
            triggered=True,
        )

    if otype in {OrderType.STOP, OrderType.STOP_MARKET}:
        if not stop_triggered(order, bar):
            return FillDecision(False, reason="stop_not_touched")
        price = stop_fill_price(order, bar, spec, slippage_ticks, stop_gap_policy)
        return FillDecision(
            True,
            price=price,
            reason="stop_triggered",
            liquidity="taker",
            slippage_ticks=slippage_ticks,
            triggered=True,
        )

    if otype is OrderType.LIMIT:
        if not limit_touched(order, bar):
            return FillDecision(False, reason="limit_not_touched")
        if not limit_fill_on_touch:
            # Require trade-through, not just a touch.
            if order.side > 0 and bar.low >= order.limit_price - 1e-12:
                return FillDecision(False, reason="limit_touch_only")
            if order.side < 0 and bar.high <= order.limit_price + 1e-12:
                return FillDecision(False, reason="limit_touch_only")
        assert order.limit_price is not None
        return FillDecision(
            True,
            price=spec.snap(order.limit_price),
            reason="limit_touched",
            liquidity="maker",
            slippage_ticks=0,
            triggered=True,
        )

    if otype is OrderType.STOP_LIMIT:
        if not stop_triggered(order, bar):
            return FillDecision(False, reason="stop_not_touched")
        # After trigger, behave as a limit. If the bar also does not
        # touch the limit, we do not assume a fill (conservative).
        if not limit_touched(order, bar):
            return FillDecision(False, reason="stop_triggered_limit_not_touched")
        assert order.limit_price is not None
        return FillDecision(
            True,
            price=spec.snap(order.limit_price),
            reason="stop_limit",
            liquidity="maker",
            slippage_ticks=0,
            triggered=True,
        )

    return FillDecision(False, reason="unsupported_type")


def both_touched(stop: Order, target: Order, bar: Bar) -> bool:
    return stop_triggered(stop, bar) and limit_touched(target, bar)
