# Frontier Resolution Switch (FRS) Research

A fully local, reproducible research stack for determining whether the
**Frontier Resolution Switch** contains a real, robust, economically
exploitable futures-market effect.

This repository is designed to **falsify FRS before optimizing it**.
Favorable historical results are treated as suspicious until they survive
look-ahead tests, realistic execution, futures roll mechanics, cost stress,
parameter perturbation, regime segmentation, and chronological out-of-sample
evaluation.

The canonical engine is **ours**. LEAN, QuantConnect, Backtrader, Zipline,
MetaTrader, TradingView, and NautilusTrader are not used as the execution
engine. NautilusTrader was studied only as an architecture reference for
deterministic event-driven design.

## Hypothesis (candidate, not truth)

Important boundaries may resolve differently depending on whether price
arrives with energy still stored or already spent:

- **Stored energy + acceptance into genuinely new territory** may favor continuation.
- **Spent energy + little structural progress + rapid rejection** may favor reversal.

The frozen baseline implementation (ported from a QuantConnect prototype)
operationalizes one explicit version of this idea:

- Energy \(E = (close - open) / (high - low)\)
- Compression = short ATR / long ATR
- Boundary = max high / min low of prior N completed bars (current bar excluded)
- Continuation entry when a high-|E| close accepts beyond that boundary
  after compression
- Execute in the micro contract (MGC / MES / MNQ) while signals come from
  GC / ES / NQ
- One shared portfolio, one position at a time, ATR brackets, time stop,
  15:45 ET daily flat

Those choices are **baseline assumptions**, not discoveries. Neighboring
definitions live in `config/` and can change without rewriting the engine.

## Repository layout

```text
config/                 strategy, instrument, and research-scenario YAML
src/engine/             event-driven broker, orders, fills, portfolio
src/futures/            contracts, rolls, sessions
src/strategy/           FRS definition, signals, sizing (swappable)
src/data/               ingest, normalize, validate, synthetic generator
src/research/           backtest driver, robustness, report, CLI
tests/                  deterministic pytest + Hypothesis invariants
data/raw/               immutable vendor dumps (gitignored)
data/parquet/           normalized Parquet (gitignored)
runs/                   immutable run directories (gitignored)
```

## Scientific sequence

1. Make the engine correct (reconciliation, no look-ahead, conservative fills).
2. Freeze a baseline FRS definition.
3. Run the baseline.
4. Increase costs and delays.
5. Perturb thresholds and neighboring parameters.
6. Test alternative rollover assumptions.
7. Segment by instrument, year, volatility regime, direction, session, signal type.
8. Chronological walk-forward.
9. Reserve a final untouched holdout. Optimization never touches it.

Prefer broad stable parameter regions over isolated historical optima.
Identical inputs must reproduce identical outputs.

## Conservative execution model

- A completed bar cannot influence an order before its closing timestamp.
- Signals on completed bars cannot fill before the next valid market event.
- Market orders incur at least one adverse tick of baseline slippage.
- If stop and target are both touched in the same OHLC bar and the path is
  unknown, the default is **worst-case** (stop wins). The trade is marked
  ambiguous. The engine can also evaluate both paths as a sensitivity case.
- Continuous series are for signals only. They are not tradeable. Roll gaps
  never create artificial P&L.
- GC and MGC, ES and MES, NQ and MNQ are separate instruments.

With only 30-minute bars the next valid event is the next 30-minute open.
That is more conservative than the original QuantConnect next-minute fill.
Feed 1-minute bars if you want QC-like delay; the consolidator and engine
already support it.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

```bash
# unit + property tests (required after every engine change)
pytest

# build deterministic synthetic fixtures into data/parquet
frs synthesize --seed 42 --scenario random_walk
frs synthesize --seed 7 --scenario planted_frs

# validate parquet (never silently patches)
frs validate --root data/parquet

# freeze-and-run the baseline
frs backtest --scenario baseline

# cost, delay, parameter, roll, and walk-forward battery
frs robustness --suite standard

# harder battery: costs + reversal / prior-session / overnight defs + holdout
frs robustness --suite hard --dataset random_walk --start 2020-01-01 --end 2023-12-31

# phase 3: pre-registered morning+high-E gate, optimistic OCO, dual holdouts
frs robustness --suite phase3 --dataset planted_frs --start 2021-01-01 --end 2022-12-31 --seed 7
frs compose runs/suite_*.json --out runs/COMPOSE.md
frs analyze --run latest

# public continuous/CFD proxies (NOT dated CME contracts)
pip install yfinance
frs fetch --source yahoo_1h
frs robustness --suite hard --dataset yahoo_1h

# render the latest (or named) run report
frs report --run latest
```

Drop vendor files into `data/raw/` and run `frs ingest` to normalize them.
Raw files are never modified. Every repair or exclusion is recorded.

## What would count as survival

Not a pretty equity curve. Survival means economically meaningful expectancy
after realistic execution, transaction costs, futures rolls, parameter
perturbation, regime segmentation, chronological OOS testing, and adversarial
attempts to destroy the result.

If it fails, the report says exactly why. If it survives, the report names
the **smallest defensible version** of the mechanism that remains.

## Real data

This stack ships a synthetic generator so the engine, accounting, and
research pipeline are testable without proprietary CME history. Synthetic
markets cannot accept or reject FRS in the real world.

`frs fetch` can pull Yahoo continuous futures or Dukascopy spot/index
CFDs as **proxies**. They are not dated CME contracts. Drop vendor dated
contract bars into `data/raw/` and run `frs ingest` for the measurement
that would actually settle the hypothesis.

See `RESEARCH.md` for the current scientific record.
