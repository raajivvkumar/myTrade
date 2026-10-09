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
