# FRS research conclusion (phase 2)

**Date:** 2026-08-12
**Engine:** `0.2.0`
**Frozen definition:** `frs_baseline_v1` (unchanged)
**New candidate definitions (separate IDs, never silently swapped in):**
`frs_reversal_v1`, `frs_prior_session_v1`, `frs_overnight_v1`

This phase pushed the falsification program harder: four years of GBM,
two years of RTH-planted paths, bootstrap CIs, regime/session slices,
and three neighboring *mechanisms* (not just neighboring thresholds).
Public Yahoo/Dukascopy fetchers are in the tree; this sandbox's outbound
TLS is blocked, so they were not able to land tape here.

## Verdict

| Dataset | Span | Verdict |
| --- | --- | --- |
| `random_walk` seed 42 | 2020–2023 (223 536 IS bar-rows) | **FALSIFIED_ON_RANDOM_WALK** |
| `planted_frs` seed 7 | 2021–2022 | **DETECTS_PLANT_NOT_SELECTIVE** |
| `frs_reversal_v1` on GBM | 2020–2023 | **Dead.** 1 013 trades, −$10.74 expectancy |
| `frs_prior_session_v1` on GBM | 2020–2023 | **Noise.** +$5.47, bootstrap CI crosses 0 |
| `frs_overnight_v1` on GBM | 2020–2023 | **Dead.** −$12.54 expectancy |
| Dated CME GC/ES/NQ + micros | — | **NOT RUN** |

There is still **no economically defensible futures claim**. There is a
sharper statement about the code path.

## What phase 2 added

- `frs fetch --source yahoo_1h|yahoo_30m|dukascopy_m30` writes an immutable
  raw dump and a parquet tree stamped `PROXY_NOT_DATED_FUTURES`.
- Signal kinds `continuation` / `reversal` and boundaries `rolling_n` /
  `prior_session` / `overnight`. Changing any of these bumps `definition_id`.
- IID bootstrap on trade expectancy (2.5/97.5 CI, P(mean>0), t-stat).
- Segments: long/short, energy, compression terciles, 09:30–11:00 vs 11:00–13:00.
- Suite `hard`: costs + delay + the three new definitions + energy OAT +
  walk-forward + holdout.

## 4-year random walk (leakage test, n larger)

Frozen baseline, 1-tick slippage, 82 trades in ~3.2 IS years.

| case | n | exp $ | net $ | P(mean>0) |
| --- | ---: | ---: | ---: | ---: |
| baseline 1 tick | 82 | −1.75 | −144 | 0.47 |
| cost_0 | 82 | +3.66 | +300 | 0.55 |
| cost_2 | 82 | −6.29 | −515 | 0.40 |
| cost_3 | 82 | −24.35 | −1 997 | 0.15 |
| delay +1 bar | 82 | +5.24 | +430 | 0.56 |
| holdout (untouched) | 23 | +32.77 | +754 | 0.77 |

Bootstrap 95% CI on the baseline is **[−52, +46]**. The point estimate is
a coin flip with a fee. A lucky holdout on GBM is not a holdout win —
the IS book is negative and 2-tick costs make it worse. Energy OAT still
flips sign (0.55/0.60/0.75 print pluses; 0.65/0.70 do not). 0.75 has
**nine** trades. That is multiple testing, which is why those cells are
not promoted and never touch the holdout.

Walk-forward: 10 test folds, most of them small-n coin flips with mixed
sign. Flagged `WALKFORWARD_WEAK`.

**By instrument (baseline):** MGC +$410 / MES −$238 / MNQ −$315.
**By side:** longs +$320, shorts −$464.

No leakage money machine after one tick. The engine is still usable.

## Neighboring mechanisms on the same 4-year GBM

These are **new hypotheses**, not patches to `frs_baseline_v1`.

| definition | n | exp $ | net $ | 95% CI | P(mean>0) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `frs_reversal_v1` fade a spent-energy rejection | 1 013 | −10.74 | −10 883 | [−23.5, +2.7] | 0.05 |
| `frs_prior_session_v1` prior-session H/L | 94 | +5.47 | +514 | [−40, +53] | 0.59 |
| `frs_overnight_v1` overnight extremes | 56 | −12.54 | −702 | [−69, +46] | 0.32 |

Reversal is the other half of the original story. On GBM it over-trades
and loses. Prior-session is a small plus whose CI includes large losses.
Overnight does not pay. None of these is a candidate for promotion.

## 2-year planted RTH paths (implementation + selectivity)

Plants fire only 09:30–11:30 ET and then run one-sided for several bars
so a 2-ATR target can complete before the 15:45 flat.

**Headline book is negative:** 487 trades, −$18.69 expectancy, −$9 102,
61% win rate. Holdout −$9.02 (n=121). Costs do not save it.

That is **not** an implementation miss. The slices say where the money is:

| slice | n | exp $ | net $ | win rate |
| --- | ---: | ---: | ---: | ---: |
| entry 09:30–11:00 (plant window) | 236 | **+136.5** | **+$32 215** | 0.822 |
| entry 11:00–13:00 (lookalikes) | 189 | −73.4 | −$13 874 | 0.534 |
| \|E\| ≥ 0.80 | 329 | **+141.2** | **+$46 455** | 0.839 |
| 0.65 ≤ \|E\| < 0.80 | 158 | −351.6 | −$55 556 | 0.146 |
| compression tercile mid | 165 | +223.3 | +$36 847 | 0.976 |
| compression tercile high | 161 | −345.3 | −$55 594 | 0.168 |

The frozen 09:30–13:00 gate is too wide. The frozen energy floor of 0.65
is too low. The rule *does* harvest a stored-energy continuation when
that is what the bar actually is. It also takes a second population of
lookalikes that have worse expectancy than a coin flip, and those
dominate the book once the sample is long enough for them to accumulate.

`delay_2bars` on this planted series prints +$59.5 expectancy / +$24 522.
That is a **planter artifact** (the injected continuation lasts ~7 bars,
so waiting one extra bar still catches it and skips some junk). It is
not a reason to change the frozen delay. We do not promote it.

`frs_reversal_v1` on planted is ~flat (−$0.32). It is not the planted
mechanism. Prior-session and overnight do not harvest the plant either.

## Smallest defensible statement (still not a market fact)

> If a compressed range is followed by a **high-energy** close through
> the prior-N-bar boundary **in the morning session**, and price then
> actually continues for the next several 30-minute bars, the executable
> pipeline (next-bar micro, 1 adverse tick, ATR bracket, 15:45 flat)
> can capture some of that move.
>
> The frozen QC gate (E ≥ 0.65, entries through 13:00, no further
> quality filter) is **not selective enough** to keep that population
> from being drowned by lookalikes. On GBM the whole book is a coin
> flip after one tick. On a two-year planted series the morning plant
> window is hugely positive and the rest of the day sinks the account.
>
> Dated CME tape has not been measured. Reversal-after-spent-energy,
> prior-session levels, and overnight extremes are separate hypotheses
> and do not help on GBM.

Tightening energy to 0.80 or cutting the entry window to 09:30–11:00
would be a **new** `definition_id`. It would also be fitting the planter.
That is not allowed to touch the holdout and is not a result.

## Real tape

`frs fetch` is the next measurement, on a machine that can open TLS to
Yahoo or Dukascopy (or, better, a vendor of *dated* GC/MGC/ES/MES/NQ/MNQ
bars dropped into `data/raw/`).

```bash
frs fetch --source yahoo_1h          # continuous futures, ~2y hourly
frs fetch --source yahoo_30m         # continuous futures, ~60d 30-minute
frs fetch --source dukascopy_m30     # spot/index CFD, longer M30
frs robustness --suite hard --dataset yahoo_1h
```

Those sources are **proxies**. Survival on them would still not be a
micro-futures trading claim. Failure on them would be evidence against
the price-path story itself.

Until dated contract bars exist in `data/parquet/`, FRS remains an
untested market story sitting on a tested engine, with a tested
observation that the frozen gate is too loose even when the mechanism
is injected by hand.
