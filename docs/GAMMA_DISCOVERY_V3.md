# Gamma Multiplier Research v3 — observational discovery, RAM only

**Status:** draft research, not a verified trade-prediction model. Branch:
`feature/dhan-gamma-discovery-v3`. Based on the v2 Vedic/strike
numerology research branch and the reviewed Windows test fixes.

## What changed

The old study samples decisions every `horizon` minutes and requires a
complete unbroken *future* 30-minute window to label even a positive.
This can censor an observable 2x event before a later rolling strike
switch. v3 adds a separate all-minute scanner:

- Segment each returned minute series at changes in actual rolling
  strike, timestamp gaps and session boundaries.
- Treat the OPEN at a candidate minute as a hypothetical entry
  benchmark (not a fill). Freeze all pre-signals **at previous completed
  minute**, never use the entry bar CLOSE for an entry signal.
- Search **later** minute CLOSEs within `--horizon 60` by default.
  Report earliest observed crossing minute for **2x, 3x, 5x, 10x**,
  irrespective of whether the series switches after the crossing.
- A positive before a later cutoff is observed. When there was no
  crossing and the full observation horizon is missing, mark the
  outcome *censored*, NOT a failed trade or true negative.
- Compress overlapping positive entry anchors into nonoverlapping
  threshold crossing **episodes** in each rolling series, while
  reporting overlapping positive-anchor counts separately. The episodes
  are *not* verified independent fixed contracts.
- Output chronological event examples with absolute rolling strike,
  simple compound digit total, reduced root, date digit total,
  hypothetical entry OPEN, observed crossing CLOSE, entry/crossing minute
  labels, earliest observed 5m momentum sign before crossing, minutes
  from sign to crossing, and frozen past-only premium/volume/OI features.
- The `--vedic-astrology` switch optionally adds an offline historical
  Lahiri geocentric chart **at entry, early momentum sign (if observed),
  and first crossing**. The extra `--vedic-transits` switch adds v2
  planet sign/nakshatra/pada transition comparisons, with descriptive
  +/-60-minute same-strike controls where available.
- Score past-only rules against all observed positive and *complete*
  negative anchor outcomes, reporting missing-feature denominators and
  event prevalence by strike root. **Do not treat all overlapping minute
  anchors as independent statistical trials.** Out-of-sample
  fixed-expiry holdout remains blocked until actual contract identities
  are available.

**Neither this scanner nor the Dhan API provides historical Gamma,
a historical bid/ask book, an independently verified expiry/security ID
per minute, realized trading P&L, or an exact intrabar event second.**
All recorded multiplier values are *rolling ATM-alias premium proxies*,
not confirmed true same-contract option trades.

## Windows Git Bash: run safely

```bash
cd /d/RaajivvProject/myTrade
git fetch origin
git switch feature/dhan-gamma-discovery-v3
git pull --ff-only
source .venv-dhan/Scripts/activate

# Offline tests. Windows-specific temp directory workaround is retained.
bash scripts/run_tests_gitbash.sh

# Query plan only — zero Dhan calls and no token needed.
python -m app.research.dhan_event_discovery_v3 \
  --from-date 2026-09-01 --through 2026-10-09 --full

# Authenticated RAM-only pilot: SIX near-expiry ATM/ATM±1 CE/PE
# calls in newest 30-day request block. Requires local Dhan token.
python -m app.research.dhan_event_discovery_v3 \
  --from-date 2026-09-01 \
  --through 2026-10-09 \
  --full --max-requests 6 \
  --horizon 60 --min-price 2 \
  --vedic-astrology --vedic-transits \
  --execute
```

The output is sanitized JSON printed to your terminal. **Do not redirect
it to a file** if you do not want to save even the research summary.
Broker candles and raw Dhan responses remain in program RAM only; no
data or trading orders are written to local files, GitHub, Google
Drive or any broker order endpoint. Synthetic pytest fixtures do
temporarily use pytest's separate scratch folder, which is cleaned.

Default `--max-requests` is intentionally 6. `--full` expands the
potential request universe but never overrides the explicit request
cap. The first six requests cover just the **newest 30-day request
window** with the six near-expiry ATM ±1 CALL/PUT selections. They do
**not** represent every strike, expiry and date back to September 1.
A broader backtest must be budgeted for provider rate limits and
entitlement; increase the cap explicitly.

The `--vedic-astrology` and `--vedic-transits` options require the
optional `pysweph` package (`requirements-astro.txt`), which was
already tested in Linux and Windows CI. Swiss Ephemeris has AGPL or
commercial licensing implications if the project becomes a product.

## Interpretation

1. **Observed episode count** measures retrospective minute-bar
   thresholds on rolling stable-strike series. It is NOT realized
   multiplies of profit, true Gamma or independent option identities.
2. **Positive anchor windows** count every historical entry OPEN
   preceding a threshold crossing; these strongly overlap.
3. **Known negatives** require the full uninterrupted horizon.
4. **Censored negatives** are explicitly excluded from confusion
   matrices, preventing late-strike-switch survival bias from
   masquerading as 100% accuracy.
5. **Past signals** are available only once enough earlier candles
   exist. Earlier unavailable features are counted as missing, not
   classified as failed predictions.
6. **First pre-cross momentum sign** may occur *after* the hypothetical
   entry and is retrospective. Do not backdate it as an executable
   signal.
7. **Numerology/astrology** metadata is descriptive. Compare event
   and non-event rates with matched dates, strikes, option side,
   moneyness, volatility, market direction and time-of-day; account for
   multiple comparisons. No verified causal relation is asserted.
8. **Real fixed-contract results** require identifying the true
   expiry/security ID for every historical minute; rolling data alone
   cannot clear that gate.

## Quality and stop conditions

The command is preview-only unless `--execute` is explicitly supplied.
It requires an active Dhan data plan, checks the request period and
response schema, throttles calls by at least 0.25 seconds, stops on
an unexpected response, and emits controlled diagnostics without
revealing tokens or raw vendor responses. Read-only endpoints only.
