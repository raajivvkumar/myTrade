# Upstox Free Active NIFTY Options: Read-only 1-minute Archive Pilot

Scope: ONE actively-listed exact NIFTY CE/PE contract and ONE trading day per request. This work is on DRAFT PR #4, isolated from Gamma-only DRAFT PR #5 and main.

Official endpoints: https://upstox.com/developer/api-documentation/analytics-token/ ; https://upstox.com/developer/api-documentation/get-pc-option-chain/ ; https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/

## Windows 11 setup (Git Bash)

    cd /d/RaajivvProject/myTrade
    git fetch origin
    git switch feature/upstox-expired-options-readonly
    git pull --ff-only
    python -m pip install -r requirements.txt
    python -m pytest -q tests/test_upstox_active_option_archive.py --basetemp=./.pytest-local-tmp

If the branch does not exist on your computer: git switch --track origin/feature/upstox-expired-options-readonly

Get FREE read-only Analytics Token from https://account.upstox.com/developer/apps > Analytics. Save only in PRIVATE local .env under UPSTOX_ANALYTICS_TOKEN. Never share token/OTP/PIN/password or .env in chat/GitHub.

## First 0-network dry run

Replace example strike 25000 with a real ACTIVE strike and choose current_week/current_month as applicable:

    python -m app.broker.upstox_active_option_archive --expiry current_week --strike 25000 --side PE --date 2026-10-07

## Real single-day GET (NO archive)

    python -m app.broker.upstox_active_option_archive --expiry current_week --strike 25000 --side PE --date 2026-10-07 --execute

Exactly two read-only GETs: V2 current option chain for exact NSE_FO contract identity and V3 historical 1-minute option candles on a single selected day. Date may lack data if contract did not exist then. Only active contracts are permitted. No Plus-only expired endpoints.

## User-authorized permanent local capture

Only after seeing valid data and confirming broker data retention rights:

    python -m app.broker.upstox_active_option_archive --expiry current_week --strike 25000 --side PE --date 2026-10-07 --execute --save

Both --execute and --save are required to persist data. The optional --source-dir and --backup-dir options choose two independent directories. Defaults: primary data/raw/upstox/options_unvalidated (Git-ignored), mirror ../MyTradeOfflineArchive/upstox_options_v3 (outside repository).

Each path is scoped by actual NIFTY expiry, strike+CE/PE and SHA256 hash of instrument key, plus day. Each captures an immutable Parquet and a neighboring JSON manifest with SHA256, source and full-session quality. Both primary and independent mirror are verified; conflicting source or backup bytes are rejected, not silently overwritten. One missing-minute candle cannot be invented or marked research-ready. A historical bar response does not supply historical gamma/IV or execution bid/ask. Exact market provenance still needs verification; a structurally complete 375-minute NSE session is not proof of authenticity.

Orders, trades, unattended scheduling, automatic Google Drive upload, old expired-option downloading and in-repo data commits are NOT supported. Live-session data cannot be saved before 15:31 IST. Store backups privately according to Upstox/NSE market-data policies.

Next milestone: verify real active option returns on user PC, then add a LIMITED approval-gated multi-strike end-of-day runner and expiry-aware quality manifest. Do not label current spot or current Greek snapshots as historical option data.

## Later: verify offline AFTER option expiry, without any Upstox token

    python -m app.data.option_archive_verify

Or specify separate private source and mirror directories:

    python -m app.data.option_archive_verify --source-dir data/raw/upstox/options_unvalidated --backup-dir ../MyTradeOfflineArchive/upstox_options_v3

This reads the immutable saved Parquet and provenance JSON; compares exact contract/expiry/day identity, primary and mirror SHA256, and checks that both copies remain intact. Neither broker access nor an unexpired contract is needed. This proves local integrity only, NOT exchange source authenticity or lawful redistribution.
