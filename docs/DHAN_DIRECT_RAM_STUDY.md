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


## v2: exact-minute proxy events + Vedic transitions + strike numerology

Work on review branch `feature/dhan-event-astro-validation` (not merged).
This is **descriptive research**, not evidence that a planet moves option prices.

### Setup and small authenticated test (Windows Git Bash)

```bash
cd /d/RaajivvProject/myTrade
git fetch origin
git switch feature/dhan-event-astro-validation
git pull --ff-only
source .venv-dhan/Scripts/activate
python -m pip install -r requirements.txt -r requirements-astro.txt
python -m pytest -q

# First: read-only six rolling-series calls; no broker data saved anywhere.
python -m app.research.dhan_direct_ram_study \
  --from-date 2026-09-01 \
  --through 2026-10-09 \
  --full --max-requests 6 --execute \
  --vedic-astrology --volume-baseline-minutes 10
```

Use `--volume-baseline-minutes 30` for the original 35-candle lookback and
`10` for the **experimental** 16-candle lookback. The original three
past-only 5-minute screening rules are not re-fitted. The 15-minute
premium/OI/IV changes are reported descriptively alongside them.
Do **not** redirect the printed report into files if you want a strict
no-research-data-persistence workflow. Library source files and tests are
committed to Git; market candles and ephemeris-derived market-event files
are not.

### Proxy premium crossing time

For each descriptive 2x/3x/5x/10x event in the `first_crossing_examples`
report, the program returns:

* `decision_time_ist`: first screening instant on the fixed clock grid;
* `hypothetical_entry_time_ist`: next 1m candle label, **not a broker fill**;
* `first_observed_crossing_close_ist`: earliest following **1m CLOSE**
  above the threshold, **not the exact second** of an intrabar crossing;
* `minutes_from_decision`, option side/series, actual rolling strike,
  hypothetical entry price and crossing close, and rules true at decision;
* full Vedic coordinates **at signal time and threshold crossing** when
  `--vedic-astrology` is set.

Dhan's minute labels are represented in IST. Source convention (bar-open
versus bar-close label), continuous security ID, actual expired contract
expiry, and bid/ask execution are **not verified**. No real multiplier,
Gamma magnitude or rupee profit is asserted.

### Astrology/astronomy definitions

Optional `pysweph` (import name `swisseph`) computes historical
*geocentric sidereal Lahiri* longitudes offline using the Moshier method.
At every displayed event it reports all nine grahas: Sun, Moon, Mars,
Mercury, Jupiter, Venus, Saturn, mean Rahu and its opposite Ketu, their
rashi, rashi degrees/arcminutes, nakshatra, pada, and apparent retrograde
status. Also reports instantaneous geocentric tithi, paksha and yoga
indicators; **not** an address-specific sunrise Panchang.

For each date that contains returned Dhan minute bars, `session_transits`
examines **regular NSE session 09:15–15:29 IST** for changes in rashi,
nakshatra, nakshatra pada and apparent retrograde. A 10-minute bracket
search is refined to a calculated second when a discrete transition is
found. Such a transition timestamp is astronomical/model-derived and is
not a precise market transaction timestamp; very brief state
reversals inside one bracket could be missed.

`vedic_transit_strike_numerology` supplies the exact strike and side,
its digit sum/root, the planetary transition time, the first candle
label at/after the transition, premium change in prior 15m and later 30m,
and whether the **later maximum minute CLOSE** met a 2x proxy. A stable
absolute strike and contiguous minute bars over the whole study window
are mandatory, so many observations will be censored. A limited
within-series, same-strike comparison of +60m/-60m controls is shown
when present. This is not an independent matched historical expiry
control nor proof of a planetary association.

**Strike numerology:** 22400 => 2+2+4+0+0 = compound 8, root 8;
22450 => compound 13, root 4. The strike digit sum is an arithmetic
numerology feature, **not** a Chaldean alphabet/name value.
**Example date:** 2026-08-08 => day number 8, full-date digit
sum 26, reduced root 8; the day was **Saturday**, so there was no
regular NIFTY intraday session. Never label a hypothetical example as
a real gain.

The report groups descriptive observations by planet/change type,
CALL/PUT, exact strike, compound/root value, and includes denominators,
censored reasons and non-event comparisons where available. A planetary
coordinate changes continuously; the defined `transition` event means
a **discrete rashi, nakshatra/pada or retrograde-status** boundary.

Scientific caveat: planets, numerology, weekdays and market variables
co-vary over calendar time. Results require placebo/control times,
out-of-sample and multiple-hypothesis corrections and should not be
presented as causal or reliable trading signals. No effect-size
confidence or success rate is claimed from the earlier 60 all-negative
proxy windows.

### Optional library and licensing

The research script without `--vedic-astrology` requires no ephemeris
package. For optional Vedic calculations, install
`requirements-astro.txt`, which currently selects `pysweph==2.10.3.6`
and has CPython 3.12 Windows wheels. **Swiss Ephemeris has AGPL vs
commercial licensing constraints**: review licensing before distributing
or hosting a product that uses it. Running it privately for personal
research is not the same as a license clearance for commercial hosting.
