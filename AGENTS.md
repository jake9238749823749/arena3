# Agent operating notes — FRS research

You are operating a falsification-first futures research stack. The goal is
not to make FRS look good. The goal is to decide whether a real, robust,
economically exploitable effect remains after adversarial scrutiny.

## Hard rules

1. Do not use LEAN, QuantConnect, Backtrader, Zipline, MetaTrader,
   TradingView, or NautilusTrader as the canonical engine. Study them only
   for architecture. Every fill, roll, and timing assumption must live here
   and have a test.
2. Never silently patch bad data. Record every repair or exclusion. Raw data
   is immutable.
3. Never fill an order using information from the signal bar that created it.
4. Never resolve unknown intrabar path in the trader's favor. Default is
   worst-case (stop before target). Mark the trade ambiguous.
5. Never book P&L from continuous-contract adjustment gaps. Continuous series
   are signal-only. Execution uses dated contracts.
6. Keep GC/MGC, ES/MES, NQ/MNQ as separate instruments even when one signals
   for the other.
7. One shared portfolio. If only one position is allowed, enforce it globally.
8. Optimization must never touch the final holdout.
9. When a modeling choice is ambiguous, take the more conservative reasonable
   interpretation, document it, and add a test or sensitivity scenario.
10. When code fails, inspect, fix, rerun tests, continue. Do not hide
    uncertainty behind an untested assumption.

## Frozen baseline (do not "improve" quietly)

The baseline in `config/strategy.yaml` is a deliberate port of the
QuantConnect prototype. Changing it is a new hypothesis, not a bugfix.

- 30-minute signal bars
- boundary lookback 12, short ATR 6, long ATR 48
- compression threshold 0.75, energy threshold 0.65
- risk 25 bps of equity, stop 1.0 ATR, target 2.0 ATR, max hold 8 bars
- notional cap 1.0x equity
- entries 09:30–13:00 America/New_York
- force flat 15:45 America/New_York
- one position, rank simultaneous candidates by score
- execute micros; signal from full-size continuous (backward-ratio) series
- current breakout bar excluded from boundary and ATR inputs

If you change any of the above, bump `strategy.definition_id` and treat the
run as a different mechanism.

## Engine invariants (must have tests)

- Cash + unrealized = equity; realized + commissions + slippage reconcile.
- Contract multiplier and tick value drive P&L. `tick_value == multiplier * tick_size`.
- No information from `ts > clock.now` is visible to strategy or sizing.
- Identical config + data + seed ⇒ identical trades, fills, equity.
- OCO children cannot both fill when the model forbids it.
- Cancel and rollover leave no phantom positions.
- Session boundaries and the 15:45 flat are correct in America/New_York.
- A completed 30-minute bar cannot place a fill at or before its close.

## Research sequence

Engine correctness → freeze baseline → baseline run → cost/delay stress →
parameter neighbors → alternate rolls → segmentation → walk-forward →
untouched holdout.

Prefer broad stable regions over isolated optima. Report how expectancy
changes as execution assumptions worsen (0, 1, 2, 3+ adverse ticks).

## Commits and runs

- Keep the repo runnable at each stable checkpoint.
- Every backtest writes an immutable directory under `runs/` containing
  config, git commit, data hashes, engine version, seed, trades, orders,
  fills, rejected signals, equity, metrics, and a written conclusion.
- Do not commit raw market data, parquet caches, or run artifacts unless a
  tiny fixture is required for tests (`tests/fixtures/`).

## Ambiguity log (update when you make a conservative call)

| Topic | Choice | Why |
| --- | --- | --- |
| Same-bar stop+target | Worst-case: stop fills, target canceled, trade marked ambiguous | Unknown path must not be optimistic |
| Bar-only execution delay | Fill at next bar open + slippage | Signal bar is not a valid fill event |
| Limit target | Fill if touched, at limit, no price improvement | Liquid futures convention without hidden edge |
| Stop through/gap | Fill at worse of stop and bar open, plus slippage | Gaps should hurt |
| Force-flat at 15:45 on 30m bars | Submit at 15:45, fill on next data event open | Timer is real; price is next observed open |
| Continuous adjustment | Backward-ratio for signals only (QC baseline) | Robustness also runs unadjusted front contract |
| First ATR sample | High-low only (no prior close), matching QC prototype | Frozen baseline fidelity |
| Overnight signal bars | Included in ATR/boundary; entries still RTH-gated | Matches prototype consolidator |
| Simultaneous candidates | Max score, others recorded as rejected | Opportunity cost is research data |
| Reversal / prior-session / overnight | Separate `definition_id`s, never overwrite baseline | New hypotheses, not bugfixes |
| Yahoo / Dukascopy | Proxy price paths only; stamp `PROXY_NOT_DATED_FUTURES` | Not dated CME tape |
