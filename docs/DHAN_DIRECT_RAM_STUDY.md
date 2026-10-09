# MyTrade: DIRECT Dhan data to RAM (no option-price data archive)

This branch adds a read-only, memory-only command. Market data is fetched
directly from DhanHQ at run time, validated and summarized in Python memory.
The raw broker responses, 1-minute option candles and historical data ARE
NOT saved locally, in Google Drive, in GitHub, or in any database.

The access token stays in the existing local .env or process environment.
Never paste credentials into ChatGPT or put them in GitHub Actions secrets
for this experiment. No trade/order API is called.

## Commands on Windows Git Bash

~~~bash
cd /d/RaajivvProject/myTrade
git fetch origin
git switch feature/dhan-direct-ram-study
git pull --ff-only
source .venv-dhan/Scripts/activate
python -m pip install -r requirements.txt
python -m pytest -q

# Display the query plan; no Dhan requests, no token read, no file writes.
python -m app.research.dhan_direct_ram_study --from-date 2026-10-01 --through 2026-10-09 --full

# Small real authenticated test: at most two rolling series calls.
python -m app.research.dhan_direct_ram_study --from-date 2026-10-01 --through 2026-10-09 --max-requests 2 --execute

# Direct full 5-year universe without caching any broker market data.
python -m app.research.dhan_direct_ram_study --from-date 2021-10-09 --through 2026-10-09 --full --execute --progress
~~~

The full universe includes WEEK and MONTH, Near/Next/Far expiry code
1/2/3, CALL and PUT, ATM plus/minus 10 for Near and plus/minus 3 for
Next/Far, all 1-minute series. With 61 thirty-day blocks this gives
8540 rolling-option calls. Deliberate rate-limit throttling is >=0.25s.
Provider entitlement, empty responses, market-data quality and other
errors can stop or limit coverage. Partial results are marked partial.
No caching means subsequent runs re-request the data.

## What this actually analyzes

Descriptive event-versus-non-event cohort means and feature denominators are also
calculated in RAM for previous 5m IV change, spot move, strike/spot moneyness,
volume acceleration, premium momentum and OI change. These are observational,
not feature-selection evidence or claims of causality.

Frozen pre-event hypotheses:
1. 5-minute option premium momentum >=20 percent;
2. recent 5-minute average volume at least double the previous 30-minute
   average AND prior 5-minute premium rise >=20 percent;
3. volume >=2x AND prior 5-minute OI growth >=10 percent.

Signals read only the last 35 completed observed minutes. On a clock grid
anchored at 09:15 IST, an observed descriptive outcome uses the next
minute OPEN as a hypothetical benchmark and the maximum CLOSE during the
following 30-minute same-session window. Ratios 2x/3x/5x/10x are
retrospective ROLLING-STABLE-STRIKE PROXIES, NOT actual option contract
multiple-trades. Future strike changes or missing bars are censored rather
than considered a failed event.

Within a response, only aggregations per session are held in RAM and raw
candles are discarded. If at least 10 days contain labeled candidate
windows, frozen rules are scored on the earliest 75 percent of observed
session dates and last 25 percent held out. Scores include TP, FP, FN,
TN, precision, recall, FPR and baseline event frequency. Sessions are
grouped across all request aliases; aliases still create correlated
samples and possible duplicated historical contract observations.

Limitations that MUST remain visible:

- Dhan Expired Options supplies rolling ATM-offset OHLC/spot/IV/OI/volume,
  with actual strike but without trustworthy per-minute fixed NSE security
  ID and actual contract expiry. Constant strike != same option contract.
- Historical Delta/Gamma and historical bid/ask book are not returned by
  this endpoint. High implied Gamma cannot be established from OI alone.
- The very best subsequent minute CLOSE is retrospective and optimistic.
  This is not an execution price or profitable trading signal.
- Rejecting windows with a future strike switch creates selection bias.
- Expiry cycles are not individually identified, so the date-based
  holdout is only a proxy, NOT true contract-expiry walk-forward validation.
- The code and GitHub CI use mocked/synthetic responses only. Actual live
  calls require a fresh user's authorized Dhan token with active data plan,
  which ChatGPT GitHub integration cannot access. Do not invent real data
  counts or successful Gamma signatures.
- User request: no raw history saved to local or Google Drive. This script
  respects that; it only prints a sanitized aggregate JSON to the terminal.
  The summary itself may remain in terminal scrollback. Do not redirect
  command output to a file.
