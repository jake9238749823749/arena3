"""Research metrics. Implemented here so Empyrical is optional, not canonical."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

import numpy as np

from engine.portfolio import Trade
from engine.engine import EquityPoint


def _safe_div(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return a / b


def trade_frame(trades: Iterable[Trade]) -> list[dict[str, Any]]:
    rows = []
    for t in trades:
        rows.append(
            {
                "trade_id": t.trade_id,
                "instrument": t.instrument,
                "contract": t.contract,
                "direction": t.direction,
                "quantity": t.quantity,
                "entry_ts": t.entry_ts,
                "exit_ts": t.exit_ts,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "commission": t.commission,
                "slippage_paid": t.slippage_paid,
                "mae": t.mae,
                "mfe": t.mfe,
                "bars_held": t.bars_held,
                "exit_reason": t.exit_reason,
                "ambiguous": t.ambiguous,
                "score": t.signal_meta.get("score"),
                "energy": t.signal_meta.get("energy"),
                "compression": t.signal_meta.get("compression"),
                "name": t.signal_meta.get("name"),
            }
        )
    return rows


def compute_metrics(
    trades: list[Trade],
    equity: list[EquityPoint],
    *,
    starting_cash: float,
    periods_per_year: float = 252.0,
) -> dict[str, Any]:
    pnls = [t.pnl for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flats = [p for p in pnls if p == 0]
    gross_win = float(sum(wins))
    gross_loss = float(sum(losses))
    net = float(sum(pnls))
    win_rate = _safe_div(len(wins), n)
    avg_win = _safe_div(gross_win, len(wins))
    avg_loss = _safe_div(gross_loss, len(losses))  # negative
    payoff = _safe_div(avg_win, abs(avg_loss)) if avg_loss != 0 else 0.0
    expectancy = _safe_div(net, n)
    profit_factor = _safe_div(gross_win, abs(gross_loss)) if gross_loss != 0 else float("inf") if gross_win > 0 else 0.0

    eq = np.array([p.equity for p in equity], dtype=float) if equity else np.array([starting_cash])
    if len(eq) >= 2:
        rets = np.diff(eq) / eq[:-1]
        rets = rets[np.isfinite(rets)]
    else:
        rets = np.array([])
    mu = float(np.mean(rets)) if len(rets) else 0.0
    sd = float(np.std(rets, ddof=1)) if len(rets) > 1 else 0.0
    downside = rets[rets < 0] if len(rets) else np.array([])
    dsd = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0.0
    # Equity points are bar-level. Annualize using the number of equity
    # points per calendar year estimated from timestamps when possible.
    ppy = periods_per_year
    if len(equity) >= 3:
        span_days = (equity[-1].ts - equity[0].ts).total_seconds() / 86400.0
        if span_days > 1:
            ppy = (len(equity) - 1) / span_days * 365.25
    sharpe = _safe_div(mu, sd) * math.sqrt(ppy) if sd > 0 else 0.0
    sortino = _safe_div(mu, dsd) * math.sqrt(ppy) if dsd > 0 else 0.0

    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    max_dd_pct = float((dd / peak).min()) if len(peak) and peak.min() > 0 else 0.0

    # Concentration: share of total profit coming from the best N trades.
    positives = sorted(wins, reverse=True)
    top5 = float(sum(positives[:5]))
    concentration_top5 = _safe_div(top5, gross_win) if gross_win > 0 else 0.0

    by_year: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    by_inst: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    by_dir: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    by_reason: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    ambiguous = 0
    for t in trades:
        year = str(t.exit_ts.year)
        by_year[year]["pnl"] += t.pnl
        by_year[year]["trades"] += 1
        by_inst[t.instrument]["pnl"] += t.pnl
        by_inst[t.instrument]["trades"] += 1
        if t.pnl > 0:
            by_inst[t.instrument]["wins"] += 1
        key = "long" if t.direction > 0 else "short"
        by_dir[key]["pnl"] += t.pnl
        by_dir[key]["trades"] += 1
        by_reason[t.exit_reason]["pnl"] += t.pnl
        by_reason[t.exit_reason]["trades"] += 1
        if t.ambiguous:
            ambiguous += 1

    turnover = float(sum(t.quantity for t in trades))
    comm = float(sum(t.commission for t in trades))
    slip = float(sum(t.slippage_paid for t in trades))
    final_eq = float(eq[-1]) if len(eq) else starting_cash
    boot = bootstrap_expectancy(pnls)
    block = block_bootstrap_expectancy(pnls)
    perm = permutation_expectancy(pnls)
    edge = excursion_stats(trades)

    return {
        "n_trades": n,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "n_flats": len(flats),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "expectancy": expectancy,
        "profit_factor": profit_factor if math.isfinite(profit_factor) else None,
        "net_pnl": net,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "final_equity": final_eq,
        "return_on_start": _safe_div(final_eq - starting_cash, starting_cash),
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "turnover_contracts": turnover,
        "commissions_paid": comm,
        "slippage_paid": slip,
        "ambiguous_trades": ambiguous,
        "ambiguous_fraction": _safe_div(ambiguous, n),
        "concentration_top5_of_wins": concentration_top5,
        "by_year": {k: dict(v) for k, v in sorted(by_year.items())},
        "by_instrument": {k: dict(v) for k, v in sorted(by_inst.items())},
        "by_direction": {k: dict(v) for k, v in by_dir.items()},
        "by_exit_reason": {k: dict(v) for k, v in by_reason.items()},
        "bootstrap": boot,
        "block_bootstrap": block,
        "permutation": perm,
        "excursions": edge,
    }


def bootstrap_expectancy(pnls: list[float], n: int = 2000, seed: int = 42) -> dict[str, Any]:
    """IID trade-resample CI. Does not fix serial dependence; lower bound on uncertainty."""
    arr = np.asarray(pnls, dtype=float)
    if len(arr) < 5:
        return {
            "n_boot": 0,
            "mean": float(arr.mean()) if len(arr) else 0.0,
            "ci_low": None,
            "ci_high": None,
            "p_positive": None,
            "t_stat": None,
        }
    rng = np.random.default_rng(seed)
    samples = rng.choice(arr, size=(n, len(arr)), replace=True).mean(axis=1)
    mean = float(arr.mean())
    sd = float(arr.std(ddof=1))
    t_stat = mean / (sd / math.sqrt(len(arr))) if sd > 0 else 0.0
    return {
        "n_boot": n,
        "mean": mean,
        "ci_low": float(np.percentile(samples, 2.5)),
        "ci_high": float(np.percentile(samples, 97.5)),
        "p_positive": float(np.mean(samples > 0)),
        "t_stat": t_stat,
    }


def block_bootstrap_expectancy(
    pnls: list[float],
    *,
    block: int = 5,
    n: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """Moving-block bootstrap of trade expectancy. Respects short serial runs."""
    arr = np.asarray(pnls, dtype=float)
    if len(arr) < max(8, block * 2):
        return {"n_boot": 0, "block": block, "mean": float(arr.mean()) if len(arr) else 0.0, "ci_low": None, "ci_high": None, "p_positive": None}
    rng = np.random.default_rng(seed)
    length = len(arr)
    starts = np.arange(0, length - block + 1)
    samples = []
    n_blocks = int(math.ceil(length / block))
    for _ in range(n):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        concat = np.concatenate([arr[s : s + block] for s in chosen])[:length]
        samples.append(float(concat.mean()))
    samples_a = np.asarray(samples)
    return {
        "n_boot": n,
        "block": block,
        "mean": float(arr.mean()),
        "ci_low": float(np.percentile(samples_a, 2.5)),
        "ci_high": float(np.percentile(samples_a, 97.5)),
        "p_positive": float(np.mean(samples_a > 0)),
    }


def permutation_expectancy(pnls: list[float], n: int = 2000, seed: int = 42) -> dict[str, Any]:
    """Two-sided p-value under random sign flips (no edge null)."""
    arr = np.asarray(pnls, dtype=float)
    if len(arr) < 5:
        return {"n_perm": 0, "observed": float(arr.mean()) if len(arr) else 0.0, "p_value": None}
    rng = np.random.default_rng(seed)
    obs = float(arr.mean())
    signs = rng.choice(np.array([-1.0, 1.0]), size=(n, len(arr)))
    null = (arr * signs).mean(axis=1)
    p = float(np.mean(np.abs(null) >= abs(obs)))
    return {"n_perm": n, "observed": obs, "p_value": p}


def excursion_stats(trades: list[Trade]) -> dict[str, Any]:
    """MAE / MFE / edge ratio. Edge = median(MFE / |MAE|) on trades with MAE < 0."""
    if not trades:
        return {"n": 0, "mean_mae": 0.0, "mean_mfe": 0.0, "median_edge_ratio": None, "median_capture": None}
    maes = np.array([t.mae for t in trades], dtype=float)
    mfes = np.array([t.mfe for t in trades], dtype=float)
    pnls = np.array([t.pnl for t in trades], dtype=float)
    ratios = []
    captures = []
    for mae, mfe, pnl in zip(maes, mfes, pnls):
        if mae < 0:
            ratios.append(mfe / abs(mae))
        if mfe > 0:
            captures.append(pnl / mfe)
    return {
        "n": len(trades),
        "mean_mae": float(maes.mean()),
        "mean_mfe": float(mfes.mean()),
        "median_edge_ratio": float(np.median(ratios)) if ratios else None,
        "median_capture": float(np.median(captures)) if captures else None,
    }


def deflated_sharpe(
    sharpe: float,
    n_obs: int,
    n_trials: int,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> dict[str, Any]:
    """Bailey & López de Prado deflated Sharpe (normal CDF of haircut SR).

    ``n_trials`` is the number of configurations inspected. This is a
    conservative multiple-testing haircut, not a license to pick the max.
    """
    if n_obs < 3 or n_trials < 1 or not math.isfinite(sharpe):
        return {"sharpe": sharpe, "n_trials": n_trials, "expected_max_sr": None, "deflated_sharpe": None, "prob_skill": None}
    # Expected max SR under N independent null trials (approx).
    emc = 0.5772156649
    expected_max = ((1 - emc) * _norm_ppf(1 - 1.0 / n_trials) + emc * _norm_ppf(1 - 1.0 / (n_trials * math.e))) / math.sqrt(n_obs - 1) if n_trials > 1 else 0.0
    denom = math.sqrt(
        max(1e-12, 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe) / (n_obs - 1)
    )
    z = (sharpe - expected_max) / denom
    return {
        "sharpe": sharpe,
        "n_trials": n_trials,
        "n_obs": n_obs,
        "expected_max_sr": float(expected_max),
        "deflated_sharpe": float(z),
        "prob_skill": float(_norm_cdf(z)),
    }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam approximation of the standard normal quantile."""
    if p <= 0.0:
        return -10.0
    if p >= 1.0:
        return 10.0
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    )


def segment_trades(trades: list[Trade]) -> dict[str, Any]:
    """Extra slices used by the research report."""
    def _agg(subset: list[Trade]) -> dict[str, float]:
        pnls = [t.pnl for t in subset]
        return {
            "n": len(subset),
            "net": float(sum(pnls)),
            "expectancy": float(sum(pnls) / len(pnls)) if subset else 0.0,
            "win_rate": float(sum(1 for p in pnls if p > 0) / len(pnls)) if subset else 0.0,
        }

    out: dict[str, Any] = {"all": _agg(trades)}
    for name, pred in (
        ("rth_exit", lambda t: True),
        ("long", lambda t: t.direction > 0),
        ("short", lambda t: t.direction < 0),
        ("ambiguous", lambda t: t.ambiguous),
        ("clean", lambda t: not t.ambiguous),
    ):
        out[name] = _agg([t for t in trades if pred(t)])
    # Volatility regime via |energy| stored on the trade.
    high_e = [t for t in trades if abs(float(t.signal_meta.get("energy") or 0)) >= 0.8]
    mid_e = [t for t in trades if 0.65 <= abs(float(t.signal_meta.get("energy") or 0)) < 0.8]
    out["energy_high"] = _agg(high_e)
    out["energy_mid"] = _agg(mid_e)
    comps = [float(t.signal_meta.get("compression") or 0) for t in trades]
    if comps:
        q1, q2 = np.quantile(comps, [0.33, 0.67])
        out["compression_low"] = _agg([t for t, c in zip(trades, comps) if c <= q1])
        out["compression_mid"] = _agg([t for t, c in zip(trades, comps) if q1 < c <= q2])
        out["compression_high"] = _agg([t for t, c in zip(trades, comps) if c > q2])
    # Session slice by entry hour in America/New_York.
    def _hour(t: Trade) -> int:
        ts = t.entry_ts
        if getattr(ts, "tzinfo", None) is not None:
            from zoneinfo import ZoneInfo

            ts = ts.astimezone(ZoneInfo("America/New_York"))
        return int(ts.hour)

    out["entry_0930_1100"] = _agg([t for t in trades if 9 <= _hour(t) < 11])
    out["entry_1100_1300"] = _agg([t for t in trades if 11 <= _hour(t) < 13])
    return out
