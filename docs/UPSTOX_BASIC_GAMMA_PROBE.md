# MyTrade Gamma — Upstox Basic read-only one-minute availability probe

## Scope
Dry-run-first research module on Draft PR #5. No orders, models, signals, automated archives or scheduled collection. No Upstox Plus-only expired-instruments endpoints.

Official documentation:
- https://upstox.com/developer/api-documentation/analytics-token/
- https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
- https://upstox.com/developer/api-documentation/get-pc-option-chain/

Free Analytics Token is read-only with 1-year validity. The V3 candle response has OHLC, volume and OI but does NOT include historical Greeks or IV. Underlying index minutes since January 2022 are documented, but expired fixed-contract option coverage is NOT guaranteed.

## Windows 11 Git Bash setup
Use your existing repo clone and open your personal private environment file:

    cd /d/RaajivvProject/myTrade
    git fetch origin
    git switch feature/gamma-investigation-no-archive
    git pull --ff-only origin feature/gamma-investigation-no-archive
    python -m pip install -r requirements.txt
    python -m pytest -q tests/test_upstox_basic_history.py
    test -f .env || cp .env.example .env
    notepad .env

For first-time checkout, if git switch reports the branch is not local, run:

    git switch --track origin/feature/gamma-investigation-no-archive

Generate your own free token at https://account.upstox.com/developer/apps under Analytics. In your LOCAL .env, set UPSTOX_ANALYTICS_TOKEN to the real token value. Never send token, API secret, OTP, PIN, password or .env to ChatGPT or commit it.

## Step A: historical NIFTY index
First safe dry run; zero requests:

    python -m app.broker.upstox_basic_history_cli --mode index --date 2026-10-07

When locally authorized, one read-only GET:

    python -m app.broker.upstox_basic_history_cli --mode index --date 2026-10-07 --execute

Optional India VIX read-only one-day GET:

    python -m app.broker.upstox_basic_history_cli --mode vix --date 2026-10-07 --execute

Only in-memory summary is printed; no CSV/Parquet/DB/Google Drive broker data is saved.

## Step B: currently active exact options contract
Prefer running option-chain fetch during market hours; quotes may be stale outside market hours.

    python -m app.broker.upstox_chain_cli --expiry current_month --execute

Pick an ACTUAL strike and CE/PE from returned chain. In the command below, replace 25000 with a strike returned by the live chain:

    python -m app.broker.upstox_basic_history_cli --mode option --expiry current_month --strike 25000 --side PE --date 2026-10-07 --execute

For option mode, exactly one current option-chain GET resolves a fixed NSE_FO instrument key and one V3 historical candle GET probes the specified day. Only currently listed options are supported; the date must not be after real expiry. Returning zero bars is not evidence the contract never traded.

## Interpreting results
- 401/403: token or entitlement. Do not claim Plus access.
- 400/422: check requested date/contract and API constraints.
- Zero rows: explicitly unavailable or empty; never fabricate candles.
- 375/375 regular-session bars: internal completeness of normal 09:15–15:29 NSE session, not exchange-authenticated price quality. Special market days require independent session calendar.
- Missing bars and off-session bars fail the complete-session gate. No interpolation.
- The response does not include historical Gamma/IV or genuine 3x/5x/10x outcome evidence.

Share only sanitized terminal output (HTTP status, counts, instrument identity); NEVER credentials or trading-account numbers.

## Next gate
After live response evidence, independent data validation and broker terms review, request explicit user approval before any new persistent collector/archive. Previous separate Draft PR #4 includes index offline backups; gamma-only Draft PR #5 intentionally does not store broker payloads. Never represent a changing-strike ATM series as one fixed option.
