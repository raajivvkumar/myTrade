# Gamma Multiplier Fingerprint Research, Phase 1

STATUS: exploratory / no confirmed Gamma Multiplier pattern, no trading signal.
Only fixed real expiry, strike and CE/PE option candles can validate a 3x/5x/10x premium outcome. NIFTY 1-minute index OHLC, even if years long, cannot tell us whether a specific option jumped or what caused the premium movement. The user's first 750 NIFTY index candles were confirmed via audit summary only; they were not available for direct computation in this assistant environment.

## Falsifiable early-warning hypothesis, NOT calibrated

H1: 5-minute absolute NIFTY close movement >=12 basis points, the latest 5-minute bar-spans >=1.35 times the median of preceding 30 bars (not overlapping with latest five), latest 5-min absolute minute returns >=1.3 times the preceding 30, and at least four of five minute moves agree in direction. Within-day 36-consecutive-minute lookback is required; do not bridge holidays, nights or gaps. Report at most one index watch per 15 minutes. Use UP as hypothesis for CE and DOWN for PE. This is a pre-registered illustration, not fitted probabilities.

H2: with one real fixed NIFTY expired CE/PE contract, optionally include volume acceleration >=2.0 times prior baseline (mean of last five to mean of prior 30). Record option five-minute OI percent changes but do NOT infer bullishness solely from an OI sign. Document IV/theta/delta as unobserved confounders unless separately available.

Every spot feature used in a signal is calculated from candles whose ending timestamp <= decision boundary. Hypothetical option ENTRY is next minute OPEN. Observed MAX OPTION MULTIPLE uses future minute CLOSE values, not intraminute HIGH or market fills. Require a full uninterrupted 30-minute forward path within the same trading day and expiry. Reject rolling ATM contract switch, missing OI/volume, missing identity, incomplete paths, or low initial premium under Rs 2. Record 3x, 5x, 10x as retrospective observational upper bounds, never executable backtest P&L. No trade orders or tokens are used in this research module.

H3: judge lift and false-positive rate versus eligible sample only AFTER collecting many independent expiries. Overlapping forward horizons, selection biases and premium-filling slippage mean simple percentages are exploratory, not statistical evidence or guaranteed 5x returns. Proper research requires train/validation/test divided by expiry, independent holdout, no repeated overlapping signal events, bid/ask cost modelling, and broad market regimes.

## On Windows Git Bash run the available data NOW

    cd /d/RaajivvProject/myTrade
    git fetch origin
    git switch feature/upstox-expired-options-readonly
    git pull --ff-only origin feature/upstox-expired-options-readonly
    python -m pytest -q
    python -m app.research.gamma_cli

No Upstox login/token or network is required. It reads the locally archived NIFTY 1m Parquet and verifies available backup catalogue. Two new files are generated outside GitHub:

    ../MyTradeOfflineArchive/research/gamma_fingerprints/index_precursor_events.csv
    ../MyTradeOfflineArchive/research/gamma_fingerprints/gamma_research_summary.json

Upload those two text files (never your token) for a real numerical review of the 750-bar sample; an empty candidate file is possible and is NOT an error. Two days is not enough to prove a multi-fold options forecast.

## Optional exact option-contract outcome labels

Provide one Parquet file captured from an actual fixed NIFTY expired option contract (do NOT concatenate strikes or expiry series). It must carry timestamp, OHLC, volume, oi, instrument_key, symbol, strike_price, option_type, expiry, lot_size with the exact same identity for every row. This requires independent legal source and actual availability; Upstox's expired contract source currently requires Plus.

    python -m app.research.gamma_cli --option-parquet "D:/ExactContract/NIFTY_FixedExpiry_Strike_PE.parquet"

The result additionally writes exact_contract_outcome_observations.csv. Never call a maximum future-close observation an executable fill. All research records stay UNVALIDATED until verified against independent market data.

## Next evidence milestones

1. Run the 750-bar offline index precursor scan and inspect its event times and false-looking setups.
2. Check that data from a week in early 2022 is truly available from Upstox V3; if yes, backfill in <=28-day windows with reasonable pauses and keep raw immutable Parquet.
3. Obtain verified fixed-contract expired option candles for many expiries; measure 3x/5x/10x rate, OI/volume leading indicators, holdout false positives, and time-to-event after each precursory watch.
4. Separate price jumps caused by delta, gamma, IV and time decay. An unexplained premium rally is not proof of gamma causation.

Upstox official documentation:
https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/
https://upstox.com/developer/api-documentation/get-expiries/

The current user account is Basic. Upstox Expired Instruments API requires Plus, while index V3 can be accessed via Basic Analytics Token. Historical expiry-list retention is documented up to six months; older fixed-contract availability must be confirmed before upgrading.
