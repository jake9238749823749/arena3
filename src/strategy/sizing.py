"""Position sizing — kept out of signal generation on purpose.

Supported methods:

* ``fixed_contracts``
* ``fixed_dollar_risk``
* ``percent_equity_risk`` (frozen baseline)
* ``vol_normalized``

All methods respect ``notional_cap`` as a hard ceiling on
``qty * price * multiplier / equity``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from futures.contracts import InstrumentSpec


@dataclass(frozen=True)
class SizeDecision:
    quantity: int
    method: str
    stop_distance: float
    risk_dollars: float
    notional: float
    reason: str


def size_position(
    *,
    method: str,
    equity: float,
    price: float,
    stop_distance: float,
    spec: InstrumentSpec,
    risk_fraction: float = 0.0025,
    notional_cap: float = 1.0,
    fixed_contracts: int = 1,
    fixed_dollar_risk: float = 250.0,
    vol_target_annual: float = 0.10,
    atr: float | None = None,
    bars_per_year: float = 46 * 252,
) -> SizeDecision:
    if equity <= 0 or price <= 0 or spec.multiplier <= 0:
        return SizeDecision(0, method, stop_distance, 0.0, 0.0, "invalid_inputs")

    if method == "fixed_contracts":
        qty = max(0, int(fixed_contracts))
        risk = qty * stop_distance * spec.multiplier
    elif method == "fixed_dollar_risk":
        if stop_distance <= 0:
            return SizeDecision(0, method, stop_distance, 0.0, 0.0, "zero_stop")
        qty = math.floor(fixed_dollar_risk / (stop_distance * spec.multiplier))
        risk = qty * stop_distance * spec.multiplier
    elif method == "percent_equity_risk":
        if stop_distance <= 0:
            return SizeDecision(0, method, stop_distance, 0.0, 0.0, "zero_stop")
        budget = equity * risk_fraction
        qty = math.floor(budget / (stop_distance * spec.multiplier))
        risk = qty * stop_distance * spec.multiplier
    elif method == "vol_normalized":
        vol = atr if atr is not None else stop_distance
        if vol <= 0:
            return SizeDecision(0, method, stop_distance, 0.0, 0.0, "zero_vol")
        # Target a fraction of equity as expected 1-ATR move.
        dollar_per_contract_atr = vol * spec.multiplier
        target_dollar = equity * vol_target_annual / math.sqrt(bars_per_year)
        qty = math.floor(target_dollar / dollar_per_contract_atr) if dollar_per_contract_atr > 0 else 0
        risk = qty * stop_distance * spec.multiplier
    else:
        raise ValueError(f"unknown sizing method {method!r}")

    notional = qty * price * spec.multiplier
    cap_qty = math.floor(equity * notional_cap / (price * spec.multiplier))
    if qty > cap_qty:
        qty = max(0, cap_qty)
        notional = qty * price * spec.multiplier
        risk = qty * stop_distance * spec.multiplier
        return SizeDecision(qty, method, stop_distance, risk, notional, "notional_capped")
    if qty < 1:
        return SizeDecision(0, method, stop_distance, 0.0, 0.0, "size_zero")
    return SizeDecision(qty, method, stop_distance, risk, notional, "ok")
