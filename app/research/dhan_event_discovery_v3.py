"""Dhan rolling option 1m multiplier discovery (RAM only, no orders).

Preview by default. With --execute, makes ONLY authorized read-only
Dhan rolling-option requests and prints summaries; NEVER archives candles.
Note: historical fixed-contract identity / true Gamma remains UNVERIFIED.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from zoneinfo import ZoneInfo
import json
import math
import sys
import time

from app.broker.dhan_cli import load_local_credentials
from app.broker.dhan_historical import DhanAPIError, DhanClient
from app.broker.dhan_history import (
    CandleWindowError, five_year_start, parse_bars, VALIDATION_RULES,
)
from app.research.dhan_direct_ram_study import queries
from app.research.option_multiplier_event_scan import scan_rolling_frame
from app.research.five_year_ram_accumulators import (
    StreamingRollingSummary, StreamingTransitSummary,
)


def run(args, *, client=None, sleeper=time.sleep):
    if args.from_date > args.through or args.from_date < five_year_start(args.through):
        raise ValueError("Date range invalid or beyond the 5-year Dhan limit")
    if not 1 <= args.horizon <= 120 or not math.isfinite(args.min_price) or args.min_price <= 0:
        raise ValueError("Invalid horizon or minimum premium")
    if args.max_requests is not None and args.max_requests <= 0:
        raise ValueError("max-requests must be positive")
    if not math.isfinite(args.pause) or args.pause < .25:
        raise ValueError("A minimum Dhan request pause of 0.25 seconds is required")
    if getattr(args, "vedic_transits", False) and not getattr(args, "vedic_astrology", False):
        raise ValueError("--vedic-transits requires --vedic-astrology")
    planned = sum(1 for _ in queries(args.from_date, args.through, args.full))
    start_request = getattr(args, "start_request", 0)
    if (type(start_request) is not int or
            start_request < 0 or start_request >= planned):
        raise ValueError("start-request must be between 0 and planned requests minus one")
    report = {
        "mode": "HISTORICAL_ROLLING_OPTION_PROXY_DISCOVERY",
        "status": "PREVIEW_NO_API_CALLS",
        "planned_api_requests": planned,
        "requested_start_date": args.from_date.isoformat(),
        "requested_end_date_inclusive": args.through.isoformat(),
        "read_order": "NEWEST_30_CALENDAR_DAYS_FIRST",
        "completed_api_requests": 0,
        "skipped_prior_api_requests": start_request,
        "next_request_index_zero_based": start_request,
        "covered_entire_requested_universe": False,
        "coverage_fraction_of_requested_calls": 0.0,
        "max_requests": args.max_requests,
        "rows_received": 0,
        "empty_responses": 0,
        "market_files_saved": 0,
        "orders_sent": 0,
        "horizon_minutes": args.horizon,
        "min_hypothetical_entry_price": args.min_price,
        "vedic_astrology": bool(getattr(args, "vedic_astrology", False)),
        "vedic_transits": bool(getattr(args, "vedic_transits", False)),
        "history_verified_exact_contract": False,
        "verified_historical_gamma_events": None,
        "verified_real_trading_accuracy": None,
        "warnings": [
            "Dhan expired rolling options return ATM-relative series, not a verified expiry/security ID.",
            "Positive threshold crossings are historical 1m CLOSE observations, not executable profits.",
            "Using a future close for labels is retrospective; this is not a forward trading signal.",
        ],
    }
    if not args.execute:
        return report
    if client is None:
        load_local_credentials()
        client = DhanClient()
    if str(client.profile().get("dataPlan", "")).lower() != "active":
        report["status"] = "BLOCKED_DATA_PLAN_NOT_ACTIVE"
        return report
    scan_summary = StreamingRollingSummary(max_examples=args.max_examples)
    transit_summary = (StreamingTransitSummary(max_examples=args.max_examples)
                       if getattr(args, "vedic_transits", False) else None)
    astro_fn = None
    if getattr(args, "vedic_astrology", False):
        from app.research.vedic_event_ephemeris import sidereal_positions
        astro_fn = sidereal_positions
    if getattr(args, "vedic_transits", False):
        from app.research.vedic_strike_transit_study import transit_strike_observations
    for request_index, q in enumerate(
            queries(args.from_date, args.through, args.full)):
        if request_index < start_request:
            continue  # Explicitly skipped earlier queries; never claim full coverage.
        if args.max_requests is not None and report["completed_api_requests"] >= args.max_requests:
            break
        sleeper(args.pause)
        entry = {
            "series": f"{q.expiry_flag}_{q.expiry_code}_{q.strike}_{q.side}",
            "start": str(q.start), "end": str(q.end),
            "payload": q.payload(),
        }
        stage = "REQUEST"
        try:
            raw = client._call("POST", "/charts/rollingoption", q.payload())
            stage = "VALIDATE_MINUTE_CANDLES"
            frame = parse_bars(raw, entry)
            stage = "OBSERVED_PREMIUM_EVENT_SCAN"
            if not frame.empty:
                scan_summary.add(scan_rolling_frame(
                    frame, series=entry["series"], side=q.side,
                    horizon=args.horizon, min_price=args.min_price,
                    astrology_fn=astro_fn, max_examples=args.max_examples))
                if getattr(args, "vedic_transits", False):
                    stage = "VEDIC_TRANSIT_DESCRIPTIVE_CONTROLS"
                    transit_summary.add(transit_strike_observations(
                        frame, series=entry["series"],
                        expiry_flag=q.expiry_flag, expiry_code=q.expiry_code))
        except (DhanAPIError, ValueError, RuntimeError, KeyError, TypeError,
                CandleWindowError) as exc:
            report["status"] = "STOPPED_AT_FAILED_REQUEST"
            report["failure"] = {
                "request_index_zero_based": request_index,
                "stage": stage, "exception": type(exc).__name__,
                "window_start": str(q.start), "window_end_exclusive": str(q.end),
                "expiry_flag": q.expiry_flag, "expiry_code": q.expiry_code,
                "strike_offset": q.strike, "side": q.side,
            }
            if isinstance(exc, DhanAPIError):
                report["failure"]["dhan"] = exc.diagnostic()
            elif isinstance(exc, CandleWindowError):
                report["failure"]["bounds"] = exc.details
            elif str(exc) in VALIDATION_RULES:
                report["failure"]["validation_rule"] = str(exc)
            break
        report["completed_api_requests"] += 1
        report["next_request_index_zero_based"] = request_index + 1
        report["last_completed_window_start"] = str(q.start)
        report["last_completed_window_end_exclusive"] = str(q.end)
        report["last_completed_series"] = entry["series"]
        report["empty_responses"] += int(frame.empty)
        report["rows_received"] += int(len(frame))
        del frame, raw
        if args.progress and report["completed_api_requests"] % 10 == 0:
            print(
                f"Completed {report['completed_api_requests']}/{planned} rolling requests (RAM only)",
                file=sys.stderr,
            )
    else:
        report["status"] = ("COMPLETE_ROLLING_PROXY_ONLY" if start_request == 0
                            else "PARTIAL_SKIPPED_EARLIER_REQUESTS")
    if report["status"] == "PREVIEW_NO_API_CALLS":
        report["status"] = "PARTIAL_MAX_REQUESTS"
    report["coverage_fraction_of_requested_calls"] = round(
        report["completed_api_requests"] / planned, 6)
    report["covered_entire_requested_universe"] = (
        report["status"] == "COMPLETE_ROLLING_PROXY_ONLY"
        and report["completed_api_requests"] == planned
        and report["empty_responses"] == 0)
    report["discovery"] = scan_summary.result()
    if transit_summary is not None:
        report["transit_comparison"] = transit_summary.result()
        report["transit_comparison"]["predictive_gamma_causation"] = None
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Always target the latest five calendar years in NSE local time.
    today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    parser.add_argument("--from-date", type=date.fromisoformat,
                        default=five_year_start(today_ist))
    parser.add_argument("--through", type=date.fromisoformat,
                        default=today_ist)
    parser.add_argument("--full", action="store_true",
                        help="All listed ATM offsets/expiry buckets. Default only near ATM CE/PE.")
    parser.add_argument("--max-requests", type=int, default=None,
                        help="Optional safety cap; omitting it allows ALL planned calls.")
    parser.add_argument("--start-request", type=int, default=0,
                        help="0-based offset for intentionally partial RAM-only runs. "
                             "Separate runs cannot be combined automatically without persistence.")
    parser.add_argument("--horizon", type=int, choices=tuple(range(1, 121)), default=60)
    parser.add_argument("--min-price", type=float, default=2)
    parser.add_argument("--max-examples", type=int, default=12)
    parser.add_argument("--pause", type=float, default=.35)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--vedic-astrology", action="store_true",
                        help="Offline Lahiri ephemeris on discovered event minute examples.")
    parser.add_argument("--vedic-transits", action="store_true",
                        help="Also compute event/control associations near modeled transitions.")
    parser.add_argument("--execute", action="store_true",
                        help="Authorize read-only Dhan API requests, never broker orders. "
                             "The full 5Y+full universe can involve thousands of requests.")
    args = parser.parse_args()
    try:
        result = run(args)
    except (ValueError, DhanAPIError, RuntimeError) as exc:
        raise SystemExit("Stopped safely: " + type(exc).__name__) from None
    print(json.dumps(result, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
