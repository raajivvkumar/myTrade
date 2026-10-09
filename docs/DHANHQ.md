# DhanHQ Data API setup for MyTrade (read-only)

The MyTrade Python integration uses the DhanHQ v2 expired rolling-options historical endpoint. You can dry-run without a subscription, token, or network connection. Live order placement is intentionally not implemented here.

## Local setup (Windows Git Bash)

From the project root, install:

    python -m pip install -r requirements.txt

Use GitHub branch `feature/dhanhq-readonly-data-prep` (draft PR #3) as the code source. Do not merge it into main merely to try the connection. Your local checkout is only the execution environment; real broker credentials stay outside Git.

Copy .env.example to .env only if .env does not already exist. Generate a fresh token in Dhan Web > My Profile > Access DhanHQ APIs. Set DHAN_ACCESS_TOKEN in this checkout's local .env. The CLI now loads this exact .env for status and explicit execution, without overriding exported environment variables. Never commit or paste the token; manually generated tokens are valid for 24 hours. The profile check works before buying a Data API subscription and reports its status; candle requests require an active Data API plan.

Existing Windows Git Bash checkout:

```bash
cd /d/RaajivvProject/myTrade
git status --short
git fetch origin
git switch feature/dhanhq-readonly-data-prep
git pull --ff-only
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

If Git reports conflicting local changes, retain them and stop the switch rather than resetting or deleting them. If the existing virtual environment references a missing Python installation, install Python 3.12+ and create a separate environment with `python -m venv .venv-dhan`, then activate `.venv-dhan/Scripts/activate`.

## Small NIFTY 1-minute probe

Preview the request without credentials, network access, or data writes:

```bash
python -m app.broker.dhan_cli nifty --date 2026-10-08
```

After setting your fresh token locally:

```bash
python -m app.broker.dhan_cli status
python -m app.broker.dhan_cli nifty --date 2026-10-08 --execute
```

The probe makes one GET /v2/profile and, only with an active data plan, one POST /v2/charts/intraday for NIFTY 50 index (13 / IDX_I / INDEX), 09:15 inclusive to 09:30 exclusive IST on the chosen date. It validates OHLC, finite values, minute timestamps, duplicate/order errors and window bounds. The JSON summary reports actual candle count and every missing minute; partial coverage is INCOMPLETE, never filled with synthetic bars. It keeps candles in memory only, displays at most three sample candles, and creates no history archive. Choose a past trading date; an empty holiday response is an error. A complete sample tests access and basic structure, not independent market accuracy or option contract identity.

Share only the sanitized summary or error, never your token or .env. There are no order endpoints or scheduled requests. Static IP is required by Dhan for order placement, not this data-only workflow. Redirects are disabled so credentials are not forwarded to another host.

Live connection status for this implementation: no Dhan token was available in the agent session or the inspected local .env; no broker request was made. Automated tests use synthetic/mocked responses. Activate/confirm Data APIs yourself through Dhan; this code does not purchase a plan or rotate your account credentials.

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
https://dhanhq.co/docs/v2/historical-data/
https://github.com/dhan-oss/DhanHQ-py

## Security

Source control holds a blank .env.example; your real .env is git-ignored. If any earlier credential values in repository history were genuine, rotate them immediately. Removing them from the latest commit does not erase history.
