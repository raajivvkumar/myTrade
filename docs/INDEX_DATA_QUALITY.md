# NIFTY 1-minute raw-data QA and missing-minute audit

This report is strictly OFFLINE. It does not call Upstox, NSE, or any trading endpoint and needs NO token. It reports whether the downloaded archive itself is internally consistent, and intentionally does not claim the data matches NSE.

Run in Git Bash from repository root AFTER downloading at least one Upstox V3 index batch:

    git fetch origin
    git switch feature/upstox-expired-options-readonly
    python -m pytest -q
    python -m app.data.market_archive verify
    python -m app.data.index_quality

Default source: ../MyTradeOfflineArchive/upstox_index_v3
Default output: ../MyTradeOfflineArchive/reports/

Results:
- nifty_1minute_quality.json — file count, row count, exact duplicate timestamps, conflicting duplicated rows (fail), checksum validation, observed missing minutes, off-session bars, examples, and explicit external reference status
- nifty_1minute_sessions.csv — one row per OBSERVED session, its first and last bar, OHLC extrema, minute count and expected vs missing 1-minute timestamps

Normal-session grid used only for dates with at least one observation: 09:15 to 15:29 IST inclusive, 375 minutes/day. This is a conventional full-day NSE session, not a guarantee that every listed date was a full normal session. Special sessions, unexpected holidays and missing complete trading days need an independently sourced NSE holiday/session calendar. Never manufacture candles to fill gaps, or assume 750 bars means prices have been exchange-verified.

Any actual NSE independent cross-check is a SEPARATE optional operation: provide your OWN legal independent 1-minute reference CSV with columns timestamp,open,high,low,close and matching India-local timestamp convention:

    python -m app.data.index_quality --reference-csv "D:/IndependentData/nifty_1m_reference.csv"

Differences over 0.5 index points across any open/high/low/close column are counted. A supplied CSV is NOT automatically certified as official NSE data. Comparison only covers overlapping minutes; report shows excluded minutes. Do not conflate 1-minute close with the official daily closing index value, which can be calculated differently.

The data remains UNVALIDATED and must not be silently merged with fixed-strike option premium history or used to claim historical Gamma Multiplier success. Backtesting on index OHLC alone tests directional hypotheses, not option 5x/10x returns.

For additional data, begin with a modest non-overlapping period after validating the initial sample. The CLI imposes 28-day max windows and max-windows 1 unless explicitly raised. If the intended full historical coverage begins in 2022, verify a historical 2022 sample before investing time in a large backfill. Save every successful download in the private archive and repeat the audit. Example first old sample:

    python -m app.broker.upstox_index_cli --from-date 2022-01-03 --to-date 2022-01-07 --execute
    python -m app.data.market_archive verify
    python -m app.data.index_quality

Backups can optionally be uploaded to the existing user-owned private Google Drive location using the documented rclone sync, but that requires GOOGLE authorization on the user's PC. It does not upload automatically; none of these commands place an order.


## NIFTY end-of-day CLOSE is not last 1-minute CLOSE

The CSV field `last_minute_close` is the observed 15:29 IST one-minute bar's close (15:29–15:30 interval). **It is not the official NIFTY daily closing index**. NSE computes the official daily NIFTY close from constituents' weighted-average closing prices over the last half-hour, so the index daily close may legitimately differ from the final minute spot index level.

Reference: https://www.nseindia.com/static/products-services/indices-faqs

This report now includes `close_definition`, `daily_close_definition`, and `daily_close_warning`, to prevent accidental misinterpretation when backtesting.

### Compare against independent daily OHLC

Export your own legitimately obtained daily NIFTY 50 reference as a CSV containing:

```csv
date,open,high,low,close
2026-03-23,22824.35,22851.70,22471.25,22512.65
2026-03-24,22878.45,23057.30,22624.20,22912.40
```

The two rows are illustrative values published in market-data histories. **They are not an embedded, licensed, NSE-certified file**; verify their provenance before relying on them.

Compare your entire 1m archive against that independent reference:

```bash
python -m app.data.index_quality --daily-reference-csv "D:/IndependentData/nifty_daily_ohlc.csv"
```

The audit compares daily **open, high, and low** against 1m aggregates at your chosen point tolerance and records mismatched session dates. It **does not demand equality of daily index closing value and last-minute spot close**. Instead, the daily CSV includes `reference_daily_close` and `reference_close_minus_last_minute_points` as informational data. A user-supplied reference CSV does not automatically become an exchange-verified source; its provenance must be reviewed.

This is a reporting fix only; no downloaded or archived raw prices are modified.
