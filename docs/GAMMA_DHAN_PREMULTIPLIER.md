# Dhan × pre-multiplier fingerprint discovery (research only)

Two related research tracks are kept together on this review branch.

1. `python -m app.research.dhan_rolling_preflight` audits locally cached Dhan expired **rolling ATM** Parquet. It reports rows, within-session strike switches, missing IV/OI/volume, and quarantined chunks. A constant strike **does not** verify a fixed option contract: expiry may roll. Hence validated 2×/3×/5×/10× event counts and accuracy remain `null`. This safety gate is deliberately non-bypassable by adding untrusted `expiry` columns to rolling Parquet.
2. `python -m app.research.gamma_bulk_scan_cli --root "/d/Path/To/verified_fixed_contract_csvs"` is the separate existing candidate scanner for genuine fixed-contract **1-minute NIFTY options**. See [GAMMA_FULL_HISTORY_SCAN.md](GAMMA_FULL_HISTORY_SCAN.md). It calculates retrospective future-minute-close multiples and pre-signal fingerprints using data at/before decision times, matched negative controls, and chronological expiry holdout of frozen, non-optimized screening rules. **Structural identity does not itself authenticate source market data**.

## Windows Git Bash

```bash
cd /d/RaajivvProject/myTrade
git fetch origin
git switch feature/dhan-pre-multiplier-validation
git pull --ff-only
source .venv-dhan/Scripts/activate
python -m pip install -r requirements.txt
python -m pytest -q
python -m app.research.dhan_rolling_preflight --root data/raw/dhan/backfill/NIFTY
# Real fixed-contract CSV data ONLY, if independently authenticated:
python -m app.research.gamma_bulk_scan_cli --root "/d/Path/To/fixed_contract_csvs"
```

No Dhan token is used by either command. Neither sends orders or writes market candles. Raw market caches and credentials stay on the user's machine.

## Empirical research admission gate

A valid positive example requires a provider-authenticated underlying, NSE instrument key, strike, CE/PE, actual expiry, real contiguous minute OHLC, a source-defined timestamp convention, and sufficient volume/quote validity. Dhan expired rolling API response does not supply enough identity; a Dhan candle alone is **not admitted** even if its strike did not switch. Avoid stitching rows across expiry/strike changes.

The research windows use next-minute open as a hypothetical entry and highest subsequent minute **close** within the same session; that is a retrospective *optimistic* upper bound, **not** a fillable return. Confirm at least six distinct expiries for any holdout metrics. Check all 2×/3×/5×/10× TP/FP/FN/TN, precision, recall, prevalence and false-positive rate, and test liquidity, bid/ask spreads, costs and actual realizable exit prices separately before a strategy can be considered validated.

Synthetic unit tests establish correct program logic only. Real-market fingerprint existence, reliable forecast accuracy, historical Gamma causality, independent source authentication, and positive trading expectancy are **not demonstrated** by this branch. No merge into main without review.
