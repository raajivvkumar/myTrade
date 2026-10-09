# MyTrade Upstox expired NIFTY option history

Purpose: one fixed NIFTY CE/PE contract, one actual strike, one expiry, one historical lot size, then 1-minute price/volume/OI verification. Upstox Plus is required for expired-instruments APIs.

IMPORTANT: Upstox Get Expiries officially documents only up to SIX MONTHS of available expired dates. Older contract examples on the historical endpoint are NOT a guarantee of five years of all strikes. Use a real API test before assuming deeper coverage.

## Windows Git Bash setup

    git fetch origin
    git switch feature/upstox-expired-options-readonly
    python -m pip install -r requirements.txt
    python -m pytest -q

Create a local .env copied from .env.example. When you have an Upstox account, Plus access, and a developer app, generate a short-lived token at https://account.upstox.com/developer/apps and enter UPSTOX_ACCESS_TOKEN locally. Never share tokens or account PINs. The normal token is valid until about 3:30 AM IST the following day.

## Preview without ANY subscription (no network)

    python -m app.broker.upstox_cli expiries
    python -m app.broker.upstox_cli contracts --expiry 2026-03-24
    python -m app.broker.upstox_cli probe --expiry 2026-03-24 --strike 23000 --side PE

## After activating Upstox Plus

    python -m app.broker.upstox_cli status --execute
    python -m app.broker.upstox_cli expiries --execute

Choose an expired date returned by the service, then discover real strike prices:

    python -m app.broker.upstox_cli contracts --expiry 2026-03-24 --limit 25 --execute

Choose a REAL returned NIFTY PE/CE strike:

    python -m app.broker.upstox_cli probe --expiry 2026-03-24 --strike 23000 --side PE --from-date 2026-03-23 --to-date 2026-03-24 --execute

Dates and strike here are examples only and may not be returned in your account. To test older retention, query an old expiry explicitly with contracts --expiry YYYY-MM-DD --execute and record its result. Do not assume the old expiry is supported. Access to user profile is NOT proof of Plus.

This implementation is GET-only, no order placement or websocket trading, and network access is disabled unless you add --execute. It caches contract-identifying parquet plus a JSON provenance manifest at data/raw/upstox/unvalidated. It never silently overwrites historical captures and never inserts unvalidated data into the trusted contract archive.

Before treating a 10x observed option move as tradable, cross-check the exact expiry, strike, side, timestamps, OHLC, open interest, volume and lot size with independent NSE official records, identify minute gaps, and simulate realistic trading costs and slippage. Broker source != NSE-verified source. No direct historical IV/Greeks is exposed by the expired candle endpoint.

Official docs:
https://upstox.com/developer/api-documentation/get-expiries/
https://upstox.com/developer/api-documentation/get-expired-option-contracts/
https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/
https://upstox.com/developer/api-documentation/authentication/

SECURITY: blank .env.example sanitizes earlier secret-like values. If those were genuine secrets, rotate them: earlier Git history still contains them.
