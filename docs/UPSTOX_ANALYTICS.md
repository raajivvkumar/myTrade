# MyTrade: Basic Upstox + FREE Analytics Token (Historical V3)

An Upstox Basic account is enough to begin index direction and market-context research. The dedicated Analytics Token is FREE, valid for one year and GET-only. It allows normal Historical Data API and cannot execute trades. This is separate from the Plus-gated Expired Instruments option-contract APIs.

- Token docs: https://upstox.com/developer/api-documentation/analytics-token/
- Upstox V3 historical: https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
- Plus-only expiry list: https://upstox.com/developer/api-documentation/get-expiries/

## Personal setup (Windows Git Bash)

1. Log in to https://account.upstox.com/developer/apps
2. Open the Analytics tab, choose Generate Token, and copy it privately.
3. On your PC only, copy .env.example to .env if missing, and set UPSTOX_ANALYTICS_TOKEN to your token. Never paste tokens into a chat, GitHub or deployment config.
4. Run from the repository root:

    git fetch origin
    git switch feature/upstox-expired-options-readonly
    python -m pip install -r requirements.txt
    python -m pytest -q

## First test: no token required, ZERO network calls

    python -m app.broker.upstox_index_cli --from-date 2026-03-23 --to-date 2026-03-24

This only prints the V3 source, 28-day windows, output paths and UNVALIDATED flag.

## First real market-data retrieval — one small window

    python -m app.broker.upstox_index_cli --from-date 2026-03-23 --to-date 2026-03-24 --execute

Only one GET request to V3 historical index candles is made. No Upstox Plus, expiring option contract lookup, OTP, or trading order is involved. Candle CSV is not assumed; each response is validated and saved to a new Parquet file and JSON manifest in data/raw/upstox/index_unvalidated/NIFTY/1minute.

The API documents minute-based records as available from January 2022. Actual history, missing timestamps and index-minute completeness still require live testing. Requests are segmented into <=28 inclusive calendar days, below the documented one-month request cap, and --max-windows defaults to 1. For large backfill, deliberately set a higher --max-windows after validating small samples.

Index candles are NOT historical option premiums: index V3 may have volume and OI values equal to zero. No direct fixed-contract IV/Greeks, buy/sell signal, or validated gamma prediction is created from this. You will need Upstox Plus (or an alternative licensed source) for precise expired option contracts, plus external NSE cross-validation.

Security: Older versions of the original repository's .env.example contained credential-like values; rotate any that were real. Sanitized .env.example does not remove them from git history. Do not expose the Analytics Token or set trading permissions.

## Backtest progression

1. Start with one NIFTY index 1m sample and validate timestamp/spot against NSE.
2. Backtest directional changes, candle-accuracy, and false-positive behavior with strict no-lookahead.
3. Archive provider provenance and detect missing minutes; never silently create synthetic price candles.
4. Expand index date windows and inspect market regimes. Do not mistake accuracy on NIFTY index for Gamma Multiplier premium forecast accuracy.
5. After validation and if required, choose whether Plus is worth the higher equity-options transaction brokerage for access to fixed-strike expired option history.
