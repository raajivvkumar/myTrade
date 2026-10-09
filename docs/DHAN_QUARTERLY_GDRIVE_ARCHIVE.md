# Dhan NIFTY options — quarterly 1m + 5m ZIP archives to Google Drive

## Destination (already created and verified)

- Parent Google Drive folder: **MyTrade Market Data Archive**
- Archive folder: **Dhan_NIFTY_Quarterly_1m_5m**
- Google Drive folder ID: `12XxOYya_1umSUvbcR3haOKXsVH6muZ63`
- Link: https://drive.google.com/drive/folders/12XxOYya_1umSUvbcR3haOKXsVH6muZ63

**No actual Dhan candle files have been downloaded or uploaded by ChatGPT.**
The user's Dhan token is stored on their Windows PC, not accessible to
ChatGPT, GitHub Actions, or the Google Drive connector. The connected
Google Drive connector is used to create the destination folder; its
OAuth authorization is NOT transferable to a local Python process.
Local Drive upload requires an independent rclone sign-in.

## Historical retention: Jan 2021 is not available from Dhan today

As of **2026-10-09**, the rolling expired-options API documents a
**five-year sliding availability window**. Its earliest supported
date is approximately **2021-10-09**. Therefore:

| Quarter | Status from Dhan on 2026-10-09 |
| --- | --- |
| 2021 Q1 (Jan–Mar) | Beyond the five-year window; no downloadable Dhan rolling options candles |
| 2021 Q2 (Apr–Jun) | Beyond the five-year window |
| 2021 Q3 (Jul–Sep) | Beyond the five-year window |
| 2021 Q4 (Oct–Dec) | Partial: 2021-10-09 through 2021-12-31 |
| 2022 Q1 – 2026 Q3 | Date ranges inside Dhan's window; actual per-series availability must still be tested |
| 2026 Q4 | Quarter-to-date only (currently through 2026-10-09) |

Do not invent missing early-2021 minute bars or treat absent
rows as trading losses. The archive tool **previews** all early
quarters as unavailable, and creates archives only for eligible
periods. For older unavailable data, use an independently licensed
vendor with **verified source and expiry/security ID**, if available.

The source endpoint is
`POST https://api.dhan.co/v2/charts/rollingoption`, with **at most
30 calendar days per request** and `interval=1` or `interval=5`.
The source is a **rolling ATM-relative** NIFTY option chain: historical
Gamma is unavailable, and a constant strike is not a verified fixed
expiry/security ID.

Official documents:
https://dhanhq.co/docs/v2/expired-options-data/

## Archive format

Exactly one `.zip` for each *eligible* calendar quarter, named
`NIFTY_DHAN_YYYYQN_rolling_1m_5m.zip` (current quarter adds
`_through_YYYYMMDD`). Each ZIP contains:

```text
manifest.json
meta/1m/WEEK_1_ATM_CALL/20261001_20261010.json
1m/WEEK_1_ATM_CALL/20261001_20261010.csv
meta/5m/WEEK_1_ATM_CALL/20261001_20261010.json
5m/WEEK_1_ATM_CALL/20261001_20261010.csv
...
```

All `WEEK` and `MONTH` expiry codes 1/2/3 and supported ATM
offsets and CALL/PUT combinations are scheduled for both intervals,
with nonoverlapping 30-day-or-shorter vendor windows per quarter.
The raw original source is not a single fixed expiry option contract.

CSV keeps source OHLC, volume, OI, IV, spot, actual strike, and
parsed timestamp. Every request has journal metadata, including zero
rows (marked `EMPTY` rather than omitted). The quarter
`manifest.json` records eligible dates, counts, actual rows,
empty responses, source quality limits and coverage status.
The tool **does not claim** that returned data are complete, that
stock-exchange holidays are valid trades, or that Gamma multipliers
are independently verified.

For an incomplete quarter, `.zip.partial` stays in a dedicated
staging directory on your PC and can resume; it is not uploaded as
a finished ZIP. When a complete ZIP is uploaded, rclone compares
the Google Drive MD5 with the local MD5, then removes only the tool's
own completed local staging file. Existing *different* Drive archives
are never overwritten. A full quarter may be substantial; monitor
available Google Drive storage quota and network bandwidth.

## Windows 11 + Git Bash setup

```bash
cd /d/RaajivvProject/myTrade
git fetch origin
git switch feature/dhan-quarterly-gdrive-archive
git pull --ff-only
source .venv-dhan/Scripts/activate

# Existing isolated Windows-safe offline suite
bash scripts/run_tests_gitbash.sh

# Confirm rclone is installed (see https://rclone.org/downloads/)
rclone version

# Perform your OWN interactive Google authorization, locally on your PC.
rclone config
```

In `rclone config`, create a remote called **dhanarchive** using
storage type **Google Drive**. Select the OAuth access scope that
permits uploading files to the user's archive folder. Log in only
through Google's browser OAuth flow and do not paste any OAuth
credentials, refresh tokens, Dhan token or TOTP into ChatGPT.

The exporter pins `--drive-root-folder-id` on every remote command
to the specific archive folder created above. The rclone remote's
configured path is not trusted to choose a different destination.

Verify the authorization:

```bash
rclone lsf dhanarchive: --drive-root-folder-id 12XxOYya_1umSUvbcR3haOKXsVH6muZ63

# Preview entire period and request count: DOES NOT CALL Dhan or upload.
python -m app.broker.dhan_quarterly_drive_archive \
  --from-date 2021-01-01 --through 2026-10-09

# First actual eligible quarter, both 1m and 5m; can resume if interrupted.
python -m app.broker.dhan_quarterly_drive_archive \
  --from-date 2021-01-01 --through 2026-10-09 \
  --quarter 2021Q4 --execute --progress

# After first archive is verified on Google Drive, full selected backfill.
python -m app.broker.dhan_quarterly_drive_archive \
  --from-date 2021-01-01 --through 2026-10-09 \
  --execute --progress
```

Use fresh active Dhan Data API subscription and local token in
`.env`. Real option reads take substantial time: there are **140
rolling option expiry/strike/side selections per up-to-30-day window,
multiplied by both 1m and 5m**. A calendar quarter can require
3–4 windows, so often 840–1,120 Dhan API calls. The preview prints
the exact expected total: on 2026-10-09 the January-2021-through-today
plan has **21 eligible quarterly ZIPs, 76 bounded request windows and
21,280 read-only calls** (140 expiry/offset/side combinations x
2 intervals per window). Every call is rate-spaced at least
0.25 seconds and uses bounded retries on 429/5xx.

Each eligible quarter is archived as one file, then an MD5-verified
upload is performed. Completed archives already on Drive are
protected from overwrite. The script runs **only while the PC's
terminal is executing**: it is not a background server or scheduled
task. Interrupted quarters retain their `.partial` staging archive
under `D:\\RaajivvProject\\myTrade_Dhan_archive_staging` and resume
on the next run. **Do not remove the staging directory manually
mid-quarter.** Files that were successfully uploaded and verified
are removed from staging automatically.

## Safety and scientific validity

No order API calls, margin requests, broker orders or trades.
No credentials in output, GitHub, zip files or Google Drive.
Existing Upstox Drive folders and other user Drive files unchanged.
Dhan's rolling options data cannot prove a same-contract 500% option
price gain, historical Gamma, or astrology/numerology influence.
Accurate analysis needs a separately validated historical
fixed-expiry source; keep a strong separation between rolling
price-window research and genuine option contract backtests.

### Safe reruns after an earlier quarter was uploaded

The tool checks the remote's existing archive filenames before starting.
After you upload e.g. `2021Q4` using `--quarter 2021Q4`, the later
unrestricted backfill will **skip the already-present Q4 ZIP** rather than
re-download its 1m/5m data or overwrite it. If its local ZIP was already
removed after verification, the rerun can observe the remote MD5 but
cannot compare the bytes to a no-longer-local ZIP; it labels this
`EXISTING_REMOTE_ARCHIVE_UNVERIFIED_THIS_RUN`, **never a newly
verified data coverage claim**. To independently verify historic
remote ZIPs, download them or use an external attestation and inspect
their manifest and integrity. Other quarters continue normally.
