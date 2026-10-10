# MyTrade — Rolling Archive Quality Gates (Phase 1)

Branch: `feature/rolling-archive-quality-gates`

## Why
The 20 archived Dhan NIFTY quarterly ZIPs contain **ATM-relative rolling option series**, not proven histories of a single fixed option contract. A rolling series can change `actual_strike` within a trading day. A premium multiplier across such a switch cannot be used as a real, realizable, same-contract 2x/3x/5x trade.

This module creates an in-memory **descriptive proxy-quality view** from existing archives. It never changes the original Google Drive ZIPs, fetches Dhan, requests credentials, sends orders, calculates executable P&L or fabricates historical Gamma.

## Windows Git Bash

```bash
cd /d/RaajivvProject/myTrade
git fetch origin
git switch feature/rolling-archive-quality-gates
git pull --ff-only
source .venv-dhan/Scripts/activate

python -m pytest -q tests/test_rolling_archive_quality.py

# Pass EXISTING local copies of quarterly Google Drive ZIPs:
python -m app.research.rolling_archive_quality \
  --zip "/d/ExistingArchives/NIFTY_DHAN_2026Q2_rolling_1m_5m.zip" \
        "/d/ExistingArchives/NIFTY_DHAN_2026Q3_rolling_1m_5m.zip"
```

The CLI emits JSON to stdout, **not to disk**. Use authenticated rclone separately to copy the archive from your own Google Drive account if it is not available locally. Do not supply any passwords or API tokens to this module. Scan multiple ZIPs in one invocation to get combined row totals.

### API

`classify_rolling_frame(frame, interval_minutes=1)` returns an annotated **copy** of one Dhan CSV DataFrame. It does not delete, reorder, drop or patch raw OHLC.

Flags:
- `q_strike_switched`: `actual_strike` changed from the prior same-day row in that rolling CSV.
- `q_zero_volume`: reported volume is exactly zero; a missing volume is **not** zero.
- `q_zero_volume_nonflat`: reported zero volume but OHLC is not flat.
- `q_zero_volume_repeated_close`: zero-volume close equals previously recorded close **for this strike on the same date**.
- `q_source_negative_volume`: Dhan parser masked a source-negative volume and flagged it in `volume_invalid_negative`.
- `q_closing_boundary`: candle timestamp exactly at a known close boundary, treated as unverified.
- `q_outside_session`: timestamp does not fit configured sessions/boundary.
- `proxy_eligible`: a nonzero-volume inside-session row with no immediate strike switch and no masked negative-volume flag. **This means descriptive rolling-proxy research only; NOT a real fill.**

`require_fixed_contract_for_returns(...)` **always rejects** frames containing rolling `actual_strike` or `series`, even if they contain apparent contract fields. For non-rolling input it requires externally checked contract provenance and a stable instrument key, expiry, strike and side. That admission is still NOT historical Gamma or bid/ask fill validation.

## Session-aware labeling

A small explicit set of documented exceptional sessions is built in: 2021/22/23 Diwali Muhurat, 2024 Jan 20 and Mar 2 and May 18 Saturdays, 2024 Nov 1 Muhurat, 2025 Feb 1 Budget and Oct 21 Muhurat, and 2026 Feb 1 Budget. From **3 August 2026** the assumed regular derivatives close is 15:40 IST; earlier it was 15:30. End times are exclusive; observations at the exact end are separately marked closing-boundary candidates.

**This is not an exhaustive NSE holiday calendar.** Weekday holidays must be passed through `closed_dates`; new/modified sessions through `sessions_by_date`. When official calendars are missing, session labeling is provisional. Special-session times must be rechecked against current official exchange circulars before using the filters to train a model.

In the 2024-05-18 disaster recovery session, timestamps between 10:00 and 11:30 and after 12:30 should not be treated as normal execution minutes. For 2025-10-21 Muhurat, after 14:45 rows are outside trading. From 2026-08-03, 15:30 is an ordinary minute until the new 15:40 boundary.

## Hard research guardrails

1. Preserve raw Google Drive ZIPs and request metadata forever; all labels are derived.
2. Do NOT treat `proxy_eligible=True` as authorization for trading or same-contract returns.
3. Do NOT forward-fill missing OHLC, volume, OI, IV or Gamma to manufacture event labels.
4. Do NOT infer an instrument's exact expiry from Dhan's `WEEK/MONTH` + relative ATM offset.
5. Restrict contract-level multiplier studies to externally evidenced immutable instrument IDs + expiry + exact strike + side.
6. Even after fixed-contract mapping, real fills, slippage, spreads and model out-of-sample performance remain unverified.
7. No live trading actions are implemented.

## Current phase status

- Completed: in-memory quality classification, archive-structure and row-count verification, read-only ZIP CLI, strict fixed-contract gate, synthetic unit tests.
- Pending: verified comprehensive exchange calendar, independent historical per-contract identification, guarded research-data joins, full 20-quarter CLI exercise on authenticated ZIPs, realistic execution modeling, and leak-free walk-forward validation.
- Evidence source: [MyTrade dataset audit Google Doc](https://docs.google.com/document/d/18rnBpIBIsgr9WvLnaq4V12Ukz8Qfg4p-eSreZ7GIBfs/edit).
- No claim is made here about discovering actual Gamma multiplier trades.
