# DhanHQ Data API setup for MyTrade (read-only)

The MyTrade Python integration uses the DhanHQ v2 expired rolling-options historical endpoint. You can dry-run without a subscription, token, or network connection. Live order placement is intentionally not implemented here.

## Local setup (Windows Git Bash)

From the project root, install:

    python -m pip install -r requirements.txt

Copy .env.example to .env. Only after subscribing, generate a fresh token in Dhan Web > My Profile > Access DhanHQ APIs. Fill DHAN_ACCESS_TOKEN in the local .env. Never commit or paste the token; manually generated tokens are usually valid for 24 hours.

## Preview (no credentials needed)

    python -m app.broker.dhan_cli rolling --from-date 2026-03-23 --to-date 2026-03-25 --expiry-flag MONTH --expiry-code 0 --strike ATM --side PUT

End date is exclusive. This previews request windows of 30 days maximum and paths. No Data API calls occur.

## When subscribed

    python -m app.broker.dhan_cli status

Profile checks Data API plan state. After it reports Active, run one small data smoke test:

    python -m app.broker.dhan_cli rolling --from-date 2026-03-23 --to-date 2026-03-25 --expiry-flag MONTH --expiry-code 0 --strike ATM --side PUT --execute

This sends only GET /v2/profile and POST /v2/charts/rollingoption; no orders are supported. First successful response saves a Parquet file under data/raw/dhan/rolling/NIFTY/ and a JSON provenance manifest. Existing files are not overwritten. All outputs are marked UNVALIDATED. Repeat with --side CALL or documented ATM offsets only after checking the sample.

## Important: rolling data is not fixed-contract price history

Dhan represents historical options using ATM / ATM+n / ATM-n selection, which can change actual strike over time. MyTrade retains the real strike for every timestamp; never join a rolling series into a fake single-contract 5x/10x/50x price move. The endpoint does not explicitly identify every historical expiry in its response; expiry code alone is not an exact contract ID. Do not pass this output directly into MyTrade fixed-contract HistoryArchive or options backtesting until contract identity and expiry have been independently confirmed.

The API supports 5 years of rolling records, OHLC, IV, volume, OI and spot, but not every distant OTM contract. Maximum 30 calendar days per request, 1/5/15/25/60-minute intervals. Use separate date windows and query combinations; backfill only after a verified small sample.

## Data validation before Gamma research

Cross-check multiple NSE official option contract dates, minute timestamps where available, NIFTY spot, OI, volume and strike identity. Audit missing bars, duplicates, strike switches, expiry calendar and liquidity. Extract candidate pre-gamma fingerprints only within a verified unchanged contract; then evaluate false positives, fillable bid/ask spreads, brokerage, slippage and unseen expiries. Py_Vollib or Dhan Greeks measure modeled sensitivities, not actual dealer gamma exposure.

Official sources:
https://dhanhq.co/docs/v2/expired-options-data/
https://dhanhq.co/docs/v2/authentication/
https://dhanhq.co/docs/v2/annexure/

## Security

Source control holds a blank .env.example; your real .env is git-ignored. If any earlier credential values in repository history were genuine, rotate them immediately. Removing them from the latest commit does not erase history.
