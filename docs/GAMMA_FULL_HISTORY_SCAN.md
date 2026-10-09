# MyTrade — Whole local history Gamma fingerprint scanner (read-only)

## Why this exists

Research target: option PREMIUM multiples **2×, 3×, 5× and 10×**,
using **true fixed-contract** NIFTY CE/PE minute histories.
For valid 1-minute files, the study counts non-overlapping >=2× prospective
*retrospective outcome* episodes, nested 3×/5×/10× outcomes, and <2× matched
controls. It compares only at-or-before-decision features (prior option
premium move, OI, volume acceleration, historical Gamma/IV if genuinely present).

The scanner never trains or emits BUY/SELL signals. It never writes market
data, alters original CSV/ZIP files, contacts a broker or uses API tokens.

**Important:** Structural validation DOES NOT verify exchange provenance.
All results remain labelled EXPLORATORY_UNVERIFIED_SOURCE. No prediction
accuracy or gamma causal attribution is proven. Only a genuine tick-timestamped
Gamma source may count as observed historical Gamma; never invent it.

## Windows 11 Git Bash

Run from the repository folder on the Draft PR #5 branch:

    cd /d/RaajivvProject/myTrade
    git switch feature/gamma-investigation-no-archive
    git pull --ff-only
    python -m pytest -q --basetemp=./.pytest-local-tmp

Scan the **actual local folder** that holds your historical CSVs (replace
the illustrative folder path with the one on your own PC):

    python -m app.research.gamma_bulk_scan_cli --root "/d/Path/To/historical_data"

The CLI prints a JSON report to the terminal. Nothing is written by default.
You may explicitly save only the generated **summary**, outside the Git repo:

    python -m app.research.gamma_bulk_scan_cli --root "/d/Path/To/historical_data" > "/d/Path/To/gamma_scan_summary.json"

If the saved JSON contains local filenames, review/redact filenames before
sharing. Never share Upstox/other broker tokens, .env, PINs or passwords.

## Source admission

- **Accept structurally:** independent CSV per exact NIFTY CE/PE contract with
  columns timestamp,open,high,low,close,volume,oi,instrument_key,strike_price,
  option_type,expiry. Actual minute bars must be 1-minute continuous where
  events are measured. A single file can cover multiple trading dates of one
  contract. Optional truly historical Gamma, IV, Delta and Theta allowed.
- **Quarantine without event mining:** tick-only
  date,time,price,volume,oi CSVs. Minute candles cannot be reconstructed
  faithfully unless record ordering/provenance are verified; duplicate
  same-second prices make naive last-tick close unreliable.
- **Exclude from minute event mining:** official-looking daily bhavcopy,
  underlying index OHLC without option contract identity, unrecognized CSV.
- **ZIP:** supported, including nested ZIP members, in memory and without
  extraction. Safety limit 100 MB per member and 3 levels of ZIP nesting.
- **RAR/7z:** logged as unscanned, not silently counted as zero. For archives
  whose original source you can authenticate, use local 7-Zip to extract into a
  *separate copy*, then scan that directory. Keep original files unchanged.
- Exact duplicates by file bytes are skipped. Files with overlapping
  contract/day identities are rejected until explicitly merged and
  deduplicated with an auditable procedure. Do not count fragmented contract
  days twice.
- Minimum entry premium defaults to Rs 2, and horizon defaults to 30 minutes.
  This reduces misleading one-tick 2× premium ratios from Rs 0.05 to Rs 0.10.
  It does not guarantee fills or eliminate slippage.

## Event and control definitions

For each actual fixed contract, decision at the completed minute t; the
hypothetical entry benchmark is NEXT minute OPEN (t+1). Event ratio is
MAXIMAL FUTURE MINUTE CLOSE within the next H completed continuous one-minute
bars divided by this benchmark. No intraminute high-price hindsight or
changing ATM contract keys. Only time-t and earlier features are considered
as precursors; future labels are isolated.

Positive case: at least 2× forward close ratio. Controls: same fixed
contract, similar starting premium, similar time of day, below 2× and censored
near all positives. Events overlapping within a contract/time horizon are
deduplicated. Selected 3×/5×/10× counts are nested inside selected 2× events
and are not separately optimized; all counts are preliminary.

The dashboard/study distinguishes **2× premium return** from **2× recent volume
relative to baseline**; they are different variables. Missing historical
Greeks stay NULL, never zero. Do not infer Gamma from spot candles.

## Current availability limitation

The current connected workspace included one day of 2016 NSE-style
F&O daily bhavcopy and small unverified April 2024 tick CSVs, not every
expiry's verified fixed-option minute OHLC. The research scanner categorizes
those files but CANNOT make them independent valid 2×/5× trade examples.
The user's historical folders and private Upstox backups must be accessible
locally, or independently sourced minute-history files supplied to the app,
for a multi-year cohort study.

After scanning, review report keys:
file_status_counts, unscanned_container_types,
exact_contract_minute_files_accepted_structurally,
eligible_start_minutes, observed_2x_events/3x/5x/10x,
matched_below_2x_controls, unique_event_dates,
unique_expiries, and candidate_condition_comparison with denominators.

**Before a strategy:** independently authenticate contract-minute data,
complete out-of-sample walk-forward evaluation across held-out expiries,
and model actual bid/ask spreads, fill assumptions, charges and slippage.
Prefer comparing at least a diverse set of expiry dates and both positive
and negative contracts, not overlapping windows alone.

## Frozen fingerprint versus future-expiry holdout (new)

The scanner now includes a \`fingerprint_holdout\` object. This is intentionally
separate from retrospective \`observed_2x_events\` and matched case/control
summaries. It seeks potential *false positives* as well as successful cases.

The preregistered, **NOT optimized** candidate screens are:

- \`VOLUME_GE_2_AND_PREMIUM_MOMENTUM_GE_20PCT\`: preceding 5-minute
  volume mean >=2× the previous 30-minute mean AND the previous 5-minute
  option premium return >=20%.
- \`VOLUME_GE_2_AND_OI_RISE_GE_10PCT\`: the same volume condition AND
  genuinely observed 5-minute OI increase >=10%.
- \`PREMIUM_MOMENTUM_GE_20PCT\`: trailing 5-minute option premium return >=20%.

These are candidates to FALSIFY, not established fingerprints. If a required
feature is missing, the rule does not alert and the missing-feature count is
shown explicitly; a null Greek, IV or OI change is never treated as zero.

To avoid choosing winning historical timestamps after seeing the outcome,
all eligible decision minutes are sampled using a fixed grid anchored at
09:15 IST, once every selected horizon minutes **per fixed-contract/day**.
The grid does not inspect future outcomes; a continuous forward path is still
needed to label historical outcomes. For example at H=30, potential sample
decisions are 09:15, 09:45, 10:15, ... when enough preceding minutes exist.

Expired cycles are divided CHRONOLOGICALLY: oldest 75% (subject to at least
two most recent expiry groups) in training-period descriptions, newest expiry
groups in the separate holdout. No screen parameters are learned or adjusted
using the holdout or even the earlier period. If fewer than **6 unique
expiries** remain after the validity/sample gates, the holdout report is
\`INSUFFICIENT_EXPIRY_DIVERSITY_NO_HOLDOUT_CLAIM\` and omits numerical fold
metrics entirely. Each frozen rule reports per multiplier (2, 3, 5, 10):
\`TP/FP/FN/TN\`, alert count, precision, recall, false-positive rate, baseline
prevalence, and descriptive precision-to-prevalence ratio. When no alerts
or eligible positives exist, undefined ratios are **null**, never 0/100%.

**Critically:** any reported fold still has \`prediction_validated: false\`,
\`historical_source_verified: false\` and \`trading_signal: null\`.
Contracts in a given expiry are correlated; sharing an expiry/date does not
create new independent trials. There is no price-execution benchmark with
bid/ask, charges, slippage or orders. Do not describe the resulting metrics
as live-trading accuracy, expected profits, or a validated Gamma mechanism.

The normal local CLI runs both the earlier case/control screen and this
holdout probe, without sending local CSVs anywhere. The output file list may
contain identifying local folder paths; redact that list if sharing the
generated JSON. 
