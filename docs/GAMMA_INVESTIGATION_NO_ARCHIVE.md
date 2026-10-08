# MyTrade Gamma Investigation — no local history storage

This branch deliberately does **not** download, persist or back up market-history data. The previous local Parquet/SQLite archive proposals live in a separate Draft PR and remain untouched. This Gamma-only branch starts from main and focuses on two testable investigative workflows.

## Launch in Windows Git Bash

    cd /d/RaajivvProject/myTrade
    git fetch origin
    git switch --track origin/feature/gamma-investigation-no-archive
    python -m pip install -r requirements.txt
    python -m streamlit run app/dashboard.py

Open **Gamma Investigation** in the Streamlit sidebar. If that page doesn't appear, navigate from Streamlit's page menu. No new data folders are created by this feature.

### Live Gamma chain (Upstox Basic account)

1. Revoke a token previously pasted into a chat or other unsafe place and generate a NEW free, read-only Analytics Token at https://account.upstox.com/developer/apps (Analytics tab).
2. Keep it ONLY in the project's local .env as UPSTOX_ANALYTICS_TOKEN=...
3. Choose Upstox relative expiry current_week/next_week/current_month etc., then click **Fetch current option chain**.
4. Review exact NIFTY CE/PE strike+expiry+instrument key with real data: Gamma, Delta, IV, Theta, OI, volume, LTP, bid/ask, spread percent and scaled Gamma sensitivity.
5. Click Fetch again later in the SAME browser session to compare the two snapshots for identical instrument identities; there is no automatic refreshing and no disk storage. Close session or use Clear to discard snapshots.

Upstox official API: https://upstox.com/developer/api-documentation/get-pc-option-chain/
Analytics Token access: https://upstox.com/developer/api-documentation/analytics-token/

Important limitations: The option-chain endpoint is a live snapshot, not continuous 1-minute historical Greeks. Gamma × spot × 0.01 estimates the LOCAL change in delta from a 1% underlying price move, not a 5× premium prediction. Relative Gamma 75th percentile and coarse quote/volume/OI/Delta filters are investigation aids, not empirically optimized signals. If Upstox returns missing Greeks or invalid quotes, the tool marks them missing / unsuitable rather than inventing data. OI increase alone is not proof of directional intent.

### Retrospective fingerprint pilot (optional upload only)

Upload a CSV with exactly ONE actual NIFTY option contract identified by instrument_key, strike_price, option_type CE/PE and expiry, 1-minute timestamp+open+high+low+close+volume+oi. Optional gamma/delta/iv/theta must be historical values actually seen at those minutes. No fake metadata or changing ATM strikes allowed.

The in-memory engine computes at each candidate minute t:
- Completed 60-minute no-gap history (for eligibility)
- Mean minute volume over last five versus preceding 30, plus OI delta over 5 min
- Current Gamma and 5-minute Gamma difference only if actual historical Greek series exists
- Previous five-minute premium return (not future)
- Hypothetical next-minute OPEN entry, then max next-horizon minute CLOSE over complete forward window within same session
- Retrospective ≥3×/5×/10× close-multiple labels, and simple rates for volume acceleration ≥2× versus other eligible windows

These labels are descriptive opportunity upper bounds. The future best close is NOT known at time t, does not prove actual fill, is not fair value, cannot distinguish gamma from IV/delta/theta, and has severe overlapping-window dependency. Need many independent expiries and holdout testing to establish any useful early warning. No writes happen; a manual download CSV button is provided.

### Why expired option data still matters

Upstox Plus-only expired-instruments API can retrieve actual past fixed-strike contracts and OHLC, but Basic does not guarantee access. Do not infer actual historical Gamma or option 10× frequency from NIFTY index OHLC or a current option-chain snapshot. Alternative legitimately sourced contract CSVs can be investigated in memory without changing account plan.

Official: https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/

This UI never places orders, schedules trading, saves a broker data archive, creates Google Drive files or uploads token/market data to ChatGPT.


## Quick smoke-test without launching the dashboard

The Upstox Option Chain endpoint accepts relative expiry keywords such as current_week, next_week, and current_month, as well as an actual YYYY-MM-DD. These are documented by Upstox: https://upstox.com/developer/api-documentation/get-pc-option-chain/

Run in Windows Git Bash from the project root:

    python -m app.broker.upstox_chain_cli
    python -m app.broker.upstox_chain_cli --expiry current_week --execute

Dry run is network-free; execute makes exactly one read-only GET. The terminal prints summary counts and at most six gamma-sensitive research-watch contracts, using real contract identity. It cannot place any order and writes no market history. Actual returned expiries are printed; exchange quote timestamps and completeness cannot be inferred from request time. If outside trading hours, displayed quotes can be stale.

If you see HTTP 401/403 or an empty chain, first check that the freshly rotated Analytics Token is saved privately in UPSTOX_ANALYTICS_TOKEN and the requested expiry is valid. Never share a token, account password, or complete .env. You can share only redacted terminal output or counts for troubleshooting.


## Repeatability study across actual fixed-contract option CSVs

The third 3x/5x/10x events vs non-events tab runs
app.research.gamma_event_study.investigate_events on multiple uploads IN MEMORY,
with no file writing, no database, no JEV and no saved market history.

- Upload one actual NIFTY CE/PE fixed-strike/expiry contract per CSV.
- Exactly the same instrument key + strike + side + expiry is required through
  each file; the same contract cannot occur twice in the batch.
- Each candidate decision timestamp t needs 60 prior contiguous regular-session
  bars and a COMPLETE future 30m (configurable) path in the same session.
- The entry benchmark is the NEXT minute OPEN, and the label is maximal
  future minute CLOSE / that entry. These 3x/5x/10x retrospective opportunity
  labels are NOT realized executable profits or evidence Gamma caused the rally.
- Deduplicate positive episodes whose decision windows overlap.
- Compare them with actual observed <3x controls from the SAME fixed option
  contract and same day within two hours and with entry premium between 0.5x
  and 2x of the case. Censor controls near ANY positive episode; never
  fabricate an unmatched control.
- Candidate pre-event features: option price returns, OI changes, Gamma/Delta/IV/
  Theta changes over 5, 15, 30, 60 minutes, recent volume vs preceding 30 minutes,
  plus spread/moneyness if supplied. Missing historical Greeks remain MISSING.
- Frozen descriptive hypotheses: volume surge >=2x; premium 5m momentum >=20%;
  OI rise >=10% in 5m; IV increasing over 5m; Gamma increasing over 5m.
  The compare table includes numerator and denominator for each cohort to avoid
  mistaking missing Greeks for a negative signal.
- 20 independent real examples are NOT available in the repo. Reaching 20
  unverified overlapping strike events is NOT statistical validation. Even with
  many inputs, events on the same market day/expiry share the same market shock.
  Assess independent dates/expiries, external market-data provenance, and
  walk-forward held-out periods before claiming predictive usefulness.

Previously excluded, unverified old historical archives MUST NOT be re-used
unless the user explicitly reauthorizes that dataset. The new research tool
accepts only legitimately sourced CSVs the user chooses for review.


## User-approved 2024 legacy CSV validation — October 8, 2026

User authorized checking previously excluded legacy NIFTY CSVs, but only
admitting contracts that pass validation. This is NOT blanket permission
to treat that unverified dataset as authentic.

- Nine old April 2024 fixed-strike-named CSVs (34,692 rows) were inspected.
- Their raw format is SECOND-resolution date/time/price/volume/oi, not verified
  one-minute OHLC. Multiple prices can occur within the same second.
- Structural checks show 21450PE (366/375 minutes), 21500PE (375/375),
  21550PE (369/375), 21600PE (367/375) are the four denser contracts.
  Five other files have only 5, 13, 34, 61 or 168 observed minutes and fail
  preliminary research-coverage thresholds.
- Despite plausible expiry, 0.05 price grid and lot-50 volume divisibility,
  the original provider, actual contract key and minute-level option premiums
  are not independently authenticated. Therefore **ZERO of nine source
  contracts has yet passed the full research admission gate**. No real 5x
  repeatability results should be inferred from this legacy sample.
- Added Streamlit tab Legacy tick CSV validation gate and
  app.research.gamma_legacy_validation.audit_legacy_tick_csv. The tab reports
  metadata/coverage integrity IN MEMORY and NEVER promotes tick rows to
  gamma events. An NSE historical F&O daily bhavcopy may support external
  end-of-day confirmation, but it cannot certify same-second tick order,
  exact minute closes or historical Greeks.
- Do not interpolate missing 1-minute premiums, assign daily OI to every
  tick, invent bid/ask spreads, or assume max/min tick prices are fills.
  Quarantined files remain outside prediction training.

Historic NIFTY weekly expiry was Thursday before 2025 revision; see NSE
circular https://nsearchives.nseindia.com/content/circulars/FAOP66938.pdf.
The official NSE daily F&O report can be found at
https://www.nseindia.com/all-reports-derivatives. Both are separate
from independent contract minute-level authentication.


## NSE official F&O daily corroboration, no local archive

NSE Indices historical data page is for UNDERLYING INDEX (NIFTY 50 etc.)
daily OHLC and NOT actual expired strike CE/PE minute OHLC.

NSE free contract-wise historical daily records list option trading date,
expiry, strike, option side, DAILY open/high/low/close, contracts and end-of-day
OI, not minute-level gamma, IV or minute OHLC:
https://www.nseindia.com/report-detail/fo_eq_security

Before the July 8, 2024 UDiFF format switch, official original NSE daily F&O
bhavcopy used:
- https://archives.nseindia.com/content/historical/DERIVATIVES/2024/APR/fo03APR2024bhav.csv.zip
- https://archives.nseindia.com/content/historical/DERIVATIVES/2024/APR/fo04APR2024bhav.csv.zip

Added a read-only IN-MEMORY button in the Legacy Validation tab that attempts
both NSE-owned archive hosts and filters OPTIDX/NIFTY, expiry 04-Apr-2024,
strikes 21450/21500/21550/21600, PE, daily data only. If NSE rejects requests
from hosted/cloud environments, the UI truthfully reports unavailability;
no scraped, generated or third-party substitute is used. Source ZIP and rows
are NOT written to disk.

Even if NSE official daily high-low encloses every legacy second-price, that
only corroborates a *daily* bound, NOT the second-level sequence or independent
1-minute prices. NSE's separate paid historical F&O Order & Trade product can
supply trade records suitable to rebuild one-minute bars if obtainable:
https://www.nseindia.com/static/market-data/eod-historical-data-subscription
Ask marketdata@nse.co.in for April 3–4, 2024 NIFTY fixed-strike contract
trade records and ask whether archived minute OI/IV/Greeks is supplied.
Never claim Greeks are NSE-observed if inferred using model assumptions.

No JEV, order placement, automated history collection or persistent archive.
