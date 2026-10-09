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

Connection evidence supplied by the user: a local 2026-10-08 probe reported an Active Data API plan and 14 candles labeled 09:16–09:29, with 09:15 absent from the expected 15-label window. That is 93.33% label coverage under the probe’s assumed convention. Boundary exclusion, close-versus-open labels and source omission remain unresolved; do not manufacture the missing bar. Only three OHLC rows were shared. The agent has no direct Dhan account connection here; automated tests remain synthetic/mocked.

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

## Five-year newest-first backfill and preliminary analysis

The user explicitly requested this historical collection. The new command saves local broker responses under the ignored data directory, separately from the earlier memory-only probe. It does not modify the original Candle Lab or upload market data to GitHub.

As of 2026-10-09, the inclusive requested range is 2021-10-09 through 2026-10-09. The default selects NIFTY 50 index one-minute bars and ATM CALL/PUT rolling series with WEEK expiry flag and expiry code 0. It processes newest windows first, with at most 30 calendar days per window and an exclusive next-day end. This is 61 windows, 183 historical requests plus a profile check. Data for the current day may be incomplete.

From your existing Git Bash checkout:

```bash
cd /d/RaajivvProject/myTrade
git fetch origin
git switch feature/dhanhq-readonly-data-prep
git pull --ff-only
source .venv-dhan/Scripts/activate
python -m pip install -r requirements.txt

# Preview: no token read, network or output files
python -m app.broker.dhan_history --through 2026-10-09

# Collect and analyze; token stays in your local .env
python -m app.broker.dhan_history --through 2026-10-09 --execute
```

Keep any local changes if Git refuses a switch or pull. Repeating the exact command resumes from verified cached chunks instead of requesting them again. Keep the same --through and --from-date for stable request boundaries; moving the range produces a different chunk plan. A later run must stay within the provider's then-current five-year retention window. --max-requests 3 limits a pilot to three new data calls; --retry-empty revisits cached empty responses. An expired token, provider failure or validation failure stops the run after saving an incremental summary. Refresh the token locally and resume; cache corruption is retained for inspection and reported, never automatically deleted.

Optional relative strikes use the documented syntax, for example --strikes ATM ATM+1 ATM-1. --expiry-flag MONTH and --expiry-code 1 or 2 select other documented rolling buckets. Each extra relative strike adds two option requests per window. The default is a bounded research starting set, not every strike and historical expiry.

Outputs:

- chunks/<series>/*.raw.json.gz: original successful validated response.
- chunks/<series>/*.parquet: validated minute rows; rolling actual_strike is retained.
- chunks/<series>/*.manifest.json: request parameters, SHA-256 hashes and daily facts.
- daily_quality.csv: observed-date row counts, IV/OI availability, strike switches, and missing-label counts under both 09:15–15:29 opening-label and 09:16–15:30 closing-label conventions.
- history_summary.json: collection progress, empty windows, yearly source observations, index span change and median observed session range.

Use this to inspect the summary after collection:

```bash
cat data/raw/dhan/backfill/NIFTY/history_summary.json
```

FINISHED means all planned API windows were processed; it does not mean five years of complete or independently accurate market data. FINISHED_WITH_EMPTY_WINDOWS requires investigation. Missing counts cover observed dates only; the exchange calendar, completely absent sessions, special sessions and timestamp convention still need authentication. Prices are not adjusted or silently filled. The displayed index span change is based on first/last observed source prices, not a verified annual total return.

Historical rolling responses provide OHLC, IV, OI, volume, spot and actual strike, not historical Gamma/Delta or a verified fixed security ID and expiry for every bar. The summary deliberately leaves fixed-contract 2x/3x/5x/10x event count null. Even an unchanged strike can roll to another expiry. Do not calculate those multipliers by stitching ATM prices, infer dealer positions from OI, or label correlation as Gamma causation.

Share history_summary.json and daily_quality.csv for the next analysis. Retain raw chunks locally for detailed investigation; do not paste credentials or commit market-data archives. No five-year live dataset has yet been downloaded or analyzed in this agent session.
