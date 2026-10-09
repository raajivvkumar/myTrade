# MyTrade: Private offline NIFTY 1-minute data archive

The user-owned Google Drive folder is:
https://drive.google.com/drive/folders/1jH6SgOQJdt9pwF1XCWPMxiGfm9otRWVf

The intended NIFTY data subfolder is:
https://drive.google.com/drive/folders/1_PCH1aYWtf5dD3y2eKGelgBtCKsNJeaX

Once 1-minute data is collected, the research/backtest engine works offline with local Parquet; neither Upstox login nor a current Analytics Token is needed to read it. Backups are separated from public GitHub source and are not published.

Source data: Upstox V3 NIFTY index candles with timestamp, OHLC, volume and OI, NOT expired CE/PE option contract candles or historical option gamma. Actual 2022-2026 coverage must be measured and gaps reported. This archiver accepts exactly NIFTY 1-minute index records with provider provenance.

## Setup on Windows 11 / Git Bash

    cd /d/RaajivvProject/myTrade
    git fetch origin
    git switch feature/upstox-expired-options-readonly
    python -m pip install -r requirements.txt

Generate a FREE read-only Analytics Token at https://account.upstox.com/developer/apps, select Analytics and generate token. Put it privately in the local .env as UPSTOX_ANALYTICS_TOKEN. Never place it in Google Drive data, GitHub, web chat, or browser client JavaScript.

## Dry run: no network or token

    python -m app.broker.upstox_index_cli --from-date 2026-03-23 --to-date 2026-03-24

## First real sample: once token is set locally

    python -m app.broker.upstox_index_cli --from-date 2026-03-23 --to-date 2026-03-24 --execute

The downloader now saves BOTH:
1. Original: data/raw/upstox/index_unvalidated/NIFTY/1minute/
2. Independent local mirror outside git repo: ../MyTradeOfflineArchive/upstox_index_v3/NIFTY/1minute/

Each has an immutable Parquet and JSON metadata; the backup has archive_catalogue.json with per-file SHA256 checksums. It refuses overwriting previously captured data with different bytes. The offline copy survives expiry, token expiration, loss of Upstox access and broker deletion (subject to your own disk health). Keep independent backups.

## Check offline archive, no Upstox login

    python -m app.data.market_archive verify

For an archive made before this feature was installed:

    python -m app.data.market_archive backup

## Google Drive: private backup

Simple method: open the target NIFTY_1m_Upstox_V3 folder link above, upload local archive folder contents preserving their directory hierarchy and the catalogue JSON. Your Google Drive account remains necessary to access private files; Upstox account does not.

Optional automated method: Install rclone from https://rclone.org/downloads/ . In Git Bash, run:

    rclone config

Create a Google Drive remote named gdrive, choose Google Drive storage and authenticate with Google on your own computer. Verify the folders:

    rclone lsd gdrive:

Upload the immutable archive:

    python -m app.data.market_archive drive-sync --rclone-remote "gdrive:MyTrade Market Data Archive/NIFTY_1m_Upstox_V3"

This command does NOT call Upstox. It verifies SHA256 locally, then uses immutable checksum-aware rclone copy. It never deletes existing files; a mismatch fails rather than silently replacing data. Check cloud integrity afterwards:

    rclone check "../MyTradeOfflineArchive/upstox_index_v3" "gdrive:MyTrade Market Data Archive/NIFTY_1m_Upstox_V3" --checksum

Do not share this folder publicly or redistribute broker-source records; verify Upstox data licensing/retention terms for your intended use.

## Restoring and backtesting without any broker token

Download/sync the private Drive archive to another computer, then verify its checksums. Run:

    python -m app.data.market_archive verify --backup-dir "../MyTradeOfflineArchive/upstox_index_v3"

Use any backed-up Parquet for research (an archive with a 2-day sample may be too short for meaningful backtesting):

    python main.py backtest --input "../MyTradeOfflineArchive/upstox_index_v3/NIFTY/1minute/2026-03-23_to_2026-03-24_inclusive.parquet"

## Full backfill since 2022

Provider docs advertise minute V3 history starting January 2022, with a maximum one-month request limit. The downloader splits into 28-day or smaller nonoverlapping windows, defaults to MAX ONE window per --execute run and auto-mirrors each successfully downloaded window. Do not request the entire 2022-present range until a real sample, NSE time/spot cross-validation and broker licensing checks succeed.

Example larger short batch after verification:

    python -m app.broker.upstox_index_cli --from-date 2026-01-01 --to-date 2026-02-28 --execute --max-windows 3 --pause-seconds 2

Original archives and backup copies remain private local files. Adding Google Drive upload requires separately configured Google authorization and the explicit drive-sync command. No actual trading or automatic scheduling is implemented.
