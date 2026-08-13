# FRS research conclusion (phase 3)

**Date:** 2026-08-12
**Engine:** `0.3.0`
**Frozen definition:** `frs_baseline_v1` (still unchanged)
**New IDs this phase:** `frs_morning_highE_v1` (pre-registered after phase-2 slices)

Ten improvements landed. None of them turns FRS into a tradeable futures
claim. Several of them make the existing conclusion sharper.

## The ten steps

1. **Trade-first event order restored** — dated trade bars fill and mark before same-timestamp signal bars.
2. **`target_first` OCO path** — optimistic same-bar resolution as a sensitivity, never the default.
3. **Pre-registered `frs_morning_highE_v1`** — E ≥ 0.80 and entries 09:30–11:00 only. Planted IS is contaminated by peeking; GBM IS and the *existence* of plants in the holdout are disclosed below.
4. **Block bootstrap** of trade expectancy (block length 5).
5. **Sign-flip permutation test** against a no-edge null.
6. **Deflated Sharpe** haircut on the best IS case given how many cases we inspected.
7. **Opportunity-cost log** — rejected candidates by reason (outranked / blocked / outside window).
8. **MAE / MFE / edge ratio / capture**.
9. **1-minute → 30-minute consolidator** with close-time visibility (tested; mixed-resolution fill is next-bar of whatever you feed).
10. **`frs compose` / `frs analyze`**, Stooq daily + Yahoo chart JSON parsers, `stooq_daily` fetch hook.

Public TLS from this sandbox is still blocked. Fetchers are in the tree.

## Verdict

| Test | Result |
| --- | --- |
| Frozen baseline on 4y GBM | **Still a coin flip.** n=82, exp −$1.75, perm p=0.94 |
| Frozen baseline on 2y planted | **Detects plant, not selective.** n=487, exp −$18.69, perm p=0.25 |
| `morning_highE` on 4y GBM | **Zero trades.** No leakage, no sample. |
| `morning_highE` on planted IS | +$191 exp, +$57k, perm p=0.00 — **harvests the planter** |
| `morning_highE` on planted holdout | +$199 exp, +$14k, perm p=0.00 — **same DGP continues into the tail** |
| `target_first` vs worst-case | Planted almost unchanged (−$17.9 vs −$18.7). GBM +$3 vs −$1.75, both CIs include 0. |
| Dated CME tape | **Still not run** |

The planted holdout of `morning_highE` is **not** an out-of-sample market test.
The planter injects the same morning continuation into the reserved tail.
It is a selectivity check: tighten the gate and you keep more of the
injected mechanism and less of the lookalikes. Promoting that gate to
the frozen baseline would be fitting the planter.

## Phase-3 numbers

### Planted 2021–2022 (`suite phase3`)

| case | n | exp $ | net $ | win rate | perm p | block P(mean>0) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 487 | −18.69 | −9 102 | 0.614 | 0.245 | 0.076 |
| morning_highE | 298 | +191.39 | +57 035 | 0.842 | 0.000 | 1.00 |
| target_first | 487 | −17.87 | −8 704 | 0.616 | 0.260 | 0.086 |
| holdout baseline | 121 | −9.02 | −1 091 | 0.645 | 0.817 | 0.33 |
| holdout morning_highE | 72 | +199.04 | +14 331 | 0.889 | 0.000 | 1.00 |

Opportunity cost on the frozen planted book: **3 651 rejected**
(2 231 blocked by the one-position rule, 1 267 outside the entry window,
153 outranked). The one-position constraint is itself a large filter.

Excursions: planted baseline median edge ratio 2.11 and capture 1.07.
GBM baseline edge ratio 0.90 and capture 0.56. The plant writes a
favorable MFE path; GBM does not.

### 4-year GBM (`suite phase3`)

| case | n | exp $ | net $ | perm p |
| --- | ---: | ---: | ---: | ---: |
| baseline | 82 | −1.75 | −144 | 0.94 |
| morning_highE | 0 | — | — | — |
| target_first | 82 | +2.99 | +245 | 0.90 |
| holdout baseline | 23 | +32.77 | +754 | 0.46 |
| holdout morning_highE | 0 | — | — | — |

A lucky 23-trade GBM holdout still has permutation p=0.46. It is noise.
`target_first` can only help when stop and target print in the same bar;
on this GBM that is rare enough that the book barely moves and remains
indistinguishable from zero.

Deflated Sharpe on the *best* IS case of the GBM suite latches onto that
lucky holdout (SR 0.87, 3 trials). That is exactly why we do not promote
holdout winners and why the frozen verdict stays **FALSIFIED_ON_RANDOM_WALK**.

## What this does to the smallest defensible statement

Unchanged, and now better measured:

> The executable pipeline can harvest a **high-energy morning continuation
> after compression** when that pattern is actually in the tape (the
> planter). The frozen QC gate (E ≥ 0.65, entries until 13:00) is too
> loose: lookalikes have worse expectancy than a coin flip and dominate
> the book. Tightening to E ≥ 0.80 and 09:30–11:00 is a **new**
> definition. On GBM it does not trade. On a planter that keeps injecting
> the same pattern into the holdout, it looks spectacular. That is not a
> market result.
>
> Same-bar stop/target ambiguity is not the story: flipping to
> target-first does not create an edge on GBM and does not repair the
> frozen planted book.
>
> Dated CME GC/MGC/ES/MES/NQ/MNQ bars remain the only measurement that
> could accept or reject the price-path story in the real market.

## Commands

```bash
pytest
frs robustness --suite phase3 --dataset planted_frs --start 2021-01-01 --end 2022-12-31 --seed 7
frs robustness --suite phase3 --dataset random_walk --start 2020-01-01 --end 2023-12-31 --seed 42
frs compose runs/suite_*.json --out runs/COMPOSE.md
frs analyze --run latest
frs fetch --source stooq_daily   # daily continuous proxy; needs TLS
```
