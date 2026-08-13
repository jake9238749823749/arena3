# FRS research conclusion

**Date:** 2026-08-12
**Definition:** `frs_baseline_v1` (QuantConnect prototype, frozen)
**Engine:** `0.1.0` (this repository — not LEAN / QC / Backtrader / Nautilus)
**Data:** synthetic only. Real dated GC/ES/NQ and MGC/MES/MNQ bars were not supplied.

This document is the scientific record. Pretty equity curves are not the criterion.
The criterion is whether FRS keeps economically meaningful expectancy after
realistic execution, costs, rolls, perturbation, segmentation, walk-forward,
and a reserved holdout.

## Verdict

| Dataset | What it is for | Verdict |
| --- | --- | --- |
| `random_walk` (seed 42, 2021–2022) | Leakage / free-lunch test | **FALSIFIED_ON_RANDOM_WALK** |
| `planted_frs` (seed 7, 2021-01–2022-06, RTH plants) | Implementation check | **DETECTS_PLANT_NOT_SELECTIVE** |
| Real CME tape | The actual hypothesis | **NOT RUN** — drop dated contract bars into `data/raw/` |

There is **no evidence** in this repository that FRS is an economically
exploitable effect in gold or equity-index futures. There **is** evidence that:

1. The engine is not printing a free lunch on GBM after the canonical 1-tick cost.
2. The frozen rule *can* harvest a stored-energy breakout when that pattern is
   planted inside the 09:30–11:30 ET window and given room to run before 15:45.
3. The same frozen rule also takes lookalike breakouts. A reserved holdout on
   the planted series goes negative. The executable definition is not selective
   enough to be called a mechanism, even in a world where we cheated for it.

## What was frozen

Port of the supplied QuantConnect algorithm, with every missing assumption written down:

- \(E = (close-open)/(high-low)\)
- Compression = short ATR(6) / long ATR(48)
- Boundary = max high / min low of the prior 12 bars; **current bar excluded**
- Long if close > upper and \(E \ge 0.65\) after compression < 0.75; short symmetric
- Simultaneous candidates ranked by \( |E| \cdot (\text{distance}/\text{ATR}) \cdot (1-\text{compression}) \)
- One shared portfolio, one position
- Signals from GC/ES/NQ; fills on MGC/MES/MNQ
- Risk 25 bps of equity, notional cap 1×, stop 1.0 ATR, target 2.0 ATR, time stop 8 bars
- Entries 09:30–13:00 America/New_York, flatten 15:45
- Next completed bar’s **open** + 1 adverse tick; same-bar stop+target → stop wins
- Continuous series is signal-only. Execution uses dated contracts. Roll gaps are real.

With only 30-minute bars, next-bar open is more conservative than the original
next-minute QC fill. Feed 1-minute bars if you want QC-like delay.

## Engine correctness

`pytest` (54 tests, including Hypothesis) covers:

- No fill on the signal bar; every non-protective fill has `fill.ts > submitted_ts`
- A future monster bar does not create earlier entries
- Same-bar OCO: stop fills, target canceled, trade marked ambiguous
- Cash identity: `cash = start + realized − commissions`
- MES P&L is `qty × points × $5` (tick value = multiplier × tick size)
- Cancel / replace leave no phantom position
- Scheduled rolls close the old dated contract and open the new one
- Identical inputs replay to identical fills and equity
- More slippage never improves an entry fill
- Holdout is the final time-fraction and does not overlap IS
- Sizing is not imported by the signal module

Those tests are the reason the random-walk result is interpretable. If the
baseline had been profitable on GBM after 1 tick, we would have treated it as
a leak, not as alpha.

## Random walk battery (primary falsification)

Span 2021-01-01 → 2022-12-31, seed 42, 111 768 IS bar-rows / 27 948 holdout.
~23k 30-minute Globex bars per instrument. Frozen baseline, no fitting.

| case | n | expectancy $ | net $ | notes |
| --- | ---: | ---: | ---: | --- |
| baseline (1 tick) | 32 | −13.46 | −431 | canonical model |
| cost_0 | 32 | +8.08 | +259 | optimistic bound; noise, not a claim |
| cost_2 | 32 | −16.56 | −530 | |
| cost_3 | 32 | −32.79 | −1 049 | |
| cost_5 | 32 | −39.36 | −1 260 | |
| 3× commission | 32 | −23.67 | −757 | |
| delay 2 / 3 bars | 32 | +0.51 / +4.94 | small + | small-sample noise; not an edge |
| roll 5d / 10d | 32 | −13.46 | −431 | daily flat ⇒ rolls almost never while in a trade |
| front-contract signals | 32 | −13.41 | −429 | same conclusion |
| holdout (untouched) | 8 | −128.61 | −1 029 | reserved 20% tail |

One-at-a-time neighbors **flip sign** (energy 0.70, compression 0.80/0.90,
stop 0.75/1.5). That is the opposite of a broad stable region. Several of
those cells have n < 20. Walk-forward test folds are 7, 4, 4, 2 trades.

**Why it fails:** after a realistic taker tick, 32 GBM lookalikes are a coin
flip with a fee. Zero slippage can print a small plus by chance. Raising
energy or loosening compression sometimes lucks into a plus — that is
multiple testing, which is why those cells are not promoted and why they
never touch the holdout.

**Small sample** is itself a finding: the frozen gate is rare. A rare rule
on two years of 30-minute bars cannot support an economic claim even if
the point estimate had been positive.

## Planted mechanism (implementation check)

After an earlier planter that fired overnight (and was correctly flattened
or rejected) produced a false “implementation miss,” plants were restricted
to 09:30–11:30 ET with several bars of one-sided continuation before 15:45.

IS (through 2022-03, 20% holdout reserved):

| case | n | expectancy $ | net $ | win rate |
| --- | ---: | ---: | ---: | ---: |
| baseline 1 tick | 356 | +28.15 | +10 023 | 0.660 |
| cost_0 | 365 | +31.51 | +11 502 | 0.660 |
| cost_2 | 351 | +21.40 | +7 512 | 0.655 |
| cost_3 | 347 | +17.64 | +6 121 | 0.654 |
| holdout | 89 | −57.83 | −5 147 | 0.596 |

So: the stack *does* what the frozen definition says, and costs do not
erase a *true* stored-energy continuation that occurs in the entry window.
The reserved tail still loses, because the rule also takes unplanted
lookalikes and is not a filter for “this bar was planted.”

A hand-built 49-bar fixture in `tests/test_frs.py` independently shows the
first fill is the planted breakout, on the micro, on the next bar, never
on the signal bar.

## What would have counted as survival

Not a pretty IS curve. Survival on **real** tape would have required:

- Expectancy > 0 at 2+ adverse ticks and listed (then stressed) commissions
- A broad, same-sign neighborhood of thresholds — not isolated cells
- Walk-forward test folds not dominated by a handful of trades
- Untouched holdout still positive
- P&L not concentrated in 5 winners or one instrument
- Rolls and continuous-vs-front signal choice not driving the result

None of that is available here for GC/ES/NQ. The random-walk battery fails
those tests in the direction a non-leaking engine should. The planted
battery passes cost stress and fails selectivity / holdout.

## Smallest defensible statement

> If price compresses, then prints a high-energy close through the prior
> N-bar boundary *and that move actually continues for the next several
> 30-minute bars inside the New York day*, the frozen executable rule can
> capture some of it on the micro after one adverse tick.
>
> That is a description of the code path. It is not a market fact.
> Lookalikes without continuation are taken too. On GBM they lose after
> costs. On real futures we do not know.

Reversal-after-spent-energy was **not** implemented. The QC prototype only
encodes continuation-after-stored-energy. Treating reversal as part of
“FRS” would be a new `definition_id`.

## Next measurement (the only one that matters)

1. Ingest dated contract bars (not continuous Yahoo) for GC, MGC, ES, MES,
   NQ, MNQ into `data/raw/` and run `frs ingest`.
2. Do not touch `config/strategy.yaml` except to bump `definition_id` if
   you intentionally change the hypothesis.
3. `frs robustness --suite standard --dataset raw`
4. Read `runs/REPORT.md`. If it dies at 2 ticks, stop. If a neighbor is
   the only profitable cell, stop. If holdout is the first time you look
   at the tail, you are doing it right.

Until that file exists, FRS is an untested market story sitting on a
tested engine.
