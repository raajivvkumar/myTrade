"""Upstox Basic NIFTY Gamma Investigation smoke test, no stored market history.

Default is DRY RUN. --execute performs only one GET /v2/option/chain.
Outputs are exploratory / NOT a BUY signal and never saved to disk.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from app.broker.upstox_chain import get_option_chain
from app.research.gamma_lab import normalize_chain


def build_parser():
    p = argparse.ArgumentParser(
        description="Inspect live NIFTY option Gamma (read-only, no data archive)"
    )
    p.add_argument(
        "--expiry", default="current_week",
        help="current_week, next_week, current_month, etc or YYYY-MM-DD",
    )
    p.add_argument(
        "--top", type=int, default=6,
        help="Maximum number of exploratory candidates to display (1–20)",
    )
    p.add_argument(
        "--execute", action="store_true",
        help="Explicitly allow one read-only Upstox Option Chain GET",
    )
    return p


def format_num(value, digits=2):
    if pd.isna(value):
        return "missing"
    return f"{float(value):,.{digits}f}"


def run(args) -> dict:
    if not 1 <= args.top <= 20:
        raise ValueError("--top must be between 1 and 20")
    if not args.execute:
        print("DRY RUN: no Upstox request, login or local market-data files.")
        print(f"Will GET NIFTY options chain: expiry={args.expiry}.")
        print("Add --execute after UPSTOX_ANALYTICS_TOKEN is saved in LOCAL .env.")
        return {"status": "DRY_RUN"}
    chain = normalize_chain(get_option_chain(expiry=args.expiry))
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    print(f"Upstox NIFTY option-chain investigation at {now:%Y-%m-%d %H:%M:%S} IST")
    print(f"Requested expiry: {args.expiry}; option rows: {len(chain)}")
    print("CAUTION: Request time is NOT exchange quote timestamp.")
    if now.weekday() >= 5 or not (time(9, 15) <= now.time() <= time(15, 30)):
        print("MARKET CLOSED/OUTSIDE NORMAL HOURS: quotes may be stale.")
    if chain.empty:
        print("No option rows available. Check expiry/permissions.")
        return {"status": "EMPTY", "option_rows": 0}
    print("Returned actual expiries: " + ", ".join(sorted(chain.expiry.unique())))
    print(f"Missing Gamma: {int(chain.gamma.isna().sum())}")
    print(f"Invalid/missing bid–ask: {int((~chain.quote_valid).sum())}")
    print(f"Basic liquidity-screen passing: {int(chain.liquid_screen.sum())}")
    print(f"Gamma-relative research-watch rows: {int(chain.investigate_only.sum())}")
    selected = chain.loc[chain.investigate_only].head(args.top)
    if selected.empty:
        print("No current rows pass all exploratory rules. NO trade inferred.")
    else:
        print("EXPLORATORY GAMMA WATCH (NOT BUY/SELL SIGNAL):")
        for row in selected.itertuples():
            print(
                f" {row.expiry} {row.strike_price:g} {row.side}"
                f" | premium={format_num(row.ltp)}"
                f" | Γ={format_num(row.gamma, 6)}"
                f" | Δ={format_num(row.delta, 3)}"
                f" | IV={format_num(row.iv)}"
                f" | OI={format_num(row.oi, 0)}"
                f" | volume={format_num(row.volume, 0)}"
                f" | spread%={format_num(row.spread_pct_mid)}"
                f" | Γ×1%spot delta-shift={format_num(row.delta_shift_for_1pct_spot, 3)}"
            )
    print(
        "Gamma rank is RELATIVE within same expiry and side. "
        "No probability, future premium multiplier, fill, or profitable edge proven."
    )
    return {
        "status": "RESEARCH_ONLY",
        "option_rows": len(chain),
        "gamma_watch_rows": int(chain.investigate_only.sum()),
        "quote_invalid_rows": int((~chain.quote_valid).sum()),
        "gamma_missing_rows": int(chain.gamma.isna().sum()),
        "real_expiries": sorted(chain.expiry.unique().tolist()),
    }


def main():
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
