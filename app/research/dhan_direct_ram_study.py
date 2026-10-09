"""Read Dhan expired rolling options directly to RAM and evaluate proxy precursors.

NO market-data files, broker-response dumps, cloud writes, or orders.
A rolling ATM strike is NEVER a verified fixed-expiry option contract.
"""
from __future__ import annotations

import argparse
from datetime import date, time, timedelta
import json
import math
import sys
import time as clock

import numpy as np
import pandas as pd

from app.broker.dhan_cli import load_local_credentials
from app.broker.dhan_historical import DhanClient, DhanAPIError, RollingQuery
from app.broker.dhan_history import backward_windows, five_year_start, parse_bars

LEVELS = (2, 3, 5, 10)
RULES = ("momentum20", "volume2_momentum20", "volume2_oi10")


def queries(start, through, full=False):
    near = ["ATM"] + [f"ATM{sign}{i}" for i in range(1, 11) for sign in ("+", "-")]
    other = ["ATM"] + [f"ATM{sign}{i}" for i in range(1, 4) for sign in ("+", "-")]
    setups = ([(f, e, near if e == 1 else other)
               for f in ("WEEK", "MONTH") for e in (1, 2, 3)]
              if full else [("WEEK", 1, ["ATM"])])
    for a, b in backward_windows(start, through + timedelta(days=1)):
        for flag, code, offsets in setups:
            for strike in offsets:
                for side in ("CALL", "PUT"):
                    yield RollingQuery(a, b, expiry_flag=flag, expiry_code=code,
                                       strike=strike, side=side, interval=1)


def empty_day():
    return {"decisions": 0, "excluded_past": 0, "censored_future": 0,
            "labeled": 0, "switches": 0,
            "positives": {str(x): 0 for x in LEVELS},
            "rules": {name: {"missing": 0,
                              "scores": {str(x): {k: 0 for k in ("tp", "fp", "fn", "tn")}
                                         for x in LEVELS}}
                      for name in RULES}}


def _continuous(f):
    return bool(f.timestamp.diff().iloc[1:].eq(pd.Timedelta(minutes=1)).all())


def study_chunk(frame, horizon=30, min_price=2.0):
    """Time-grid study; strict preceding 35 minutes, future stability censored.

    A censored forward strike switch is NOT a negative observation, but
    future-dependent censoring creates selection bias: exploratory proxy only.
    """
    if not 1 <= horizon <= 120 or not math.isfinite(min_price) or min_price <= 0:
        raise ValueError("Invalid horizon or minimum price")
    if not {"timestamp", "actual_strike", "open", "close"}.issubset(frame.columns):
        raise ValueError("Missing Dhan normalized option fields")
    if frame.timestamp.duplicated().any():
        raise ValueError("Duplicate option timestamps")
    result = {}
    for day, g in frame.groupby(frame.timestamp.dt.date):
        g = g.sort_values("timestamp").reset_index(drop=True)
        state = empty_day()
        state["switches"] = int(g.actual_strike.ne(g.actual_strike.shift()).sum() - 1)
        for i, stamp in enumerate(g.timestamp):
            mins = stamp.hour * 60 + stamp.minute - (9 * 60 + 15)
            if not (time(9, 15) <= stamp.time() <= time(15, 29)
                    and mins % horizon == 0):
                continue
            state["decisions"] += 1
            if i < 34:
                state["excluded_past"] += 1
                continue
            pre = g.iloc[i - 34:i + 1]
            if not _continuous(pre) or pre.actual_strike.nunique() != 1:
                state["excluded_past"] += 1
                continue
            # Pre-decision features; no future bar used here.
            closes = pd.to_numeric(pre.close, errors="coerce")
            vols = pd.to_numeric(pre.volume, errors="coerce") if "volume" in pre else pd.Series(dtype=float)
            oi = pd.to_numeric(pre.oi, errors="coerce") if "oi" in pre else pd.Series(dtype=float)
            momentum = (closes.iloc[-1] / closes.iloc[-6] - 1) * 100 if (
                closes.iloc[-6] > 0) else np.nan
            v_old = vols.iloc[:30]
            v_now = vols.iloc[-5:]
            volratio = (v_now.mean() / v_old.mean() if len(v_old) == 30
                        and len(v_now) == 5 and v_old.notna().all()
                        and v_now.notna().all() and v_old.mean() > 0 else np.nan)
            oi_pct = (100 * (oi.iloc[-1] / oi.iloc[-6] - 1)
                      if len(oi) == 35 and pd.notna(oi.iloc[-1])
                      and pd.notna(oi.iloc[-6]) and oi.iloc[-6] > 0 else np.nan)
            flags = {
                "momentum20": pd.notna(momentum) and momentum >= 20,
                "volume2_momentum20": pd.notna(volratio) and pd.notna(momentum)
                                       and volratio >= 2 and momentum >= 20,
                "volume2_oi10": pd.notna(volratio) and pd.notna(oi_pct)
                                and volratio >= 2 and oi_pct >= 10,
            }
            missing = {
                "momentum20": pd.isna(momentum),
                "volume2_momentum20": pd.isna(momentum) or pd.isna(volratio),
                "volume2_oi10": pd.isna(volratio) or pd.isna(oi_pct),
            }
            seq = g.iloc[i:i + horizon + 1]
            if (len(seq) != horizon + 1 or not _continuous(seq)
                    or seq.actual_strike.nunique() != 1
                    or seq.timestamp.iloc[-1].date() != day):
                state["censored_future"] += 1
                continue
            forward = seq.iloc[1:]
            entry = float(forward.open.iloc[0])
            if not math.isfinite(entry) or entry < min_price:
                state["censored_future"] += 1
                continue
            ratio = float(forward.close.max()) / entry
            state["labeled"] += 1
            for threshold in LEVELS:
                truth = ratio >= threshold
                state["positives"][str(threshold)] += int(truth)
                for name in RULES:
                    state["rules"][name]["missing"] += int(missing[name]) if threshold == 2 else 0
                    alert = bool(flags[name])
                    cell = ("tp" if truth else "fp") if alert else ("fn" if truth else "tn")
                    state["rules"][name]["scores"][str(threshold)][cell] += 1
        result[str(day)] = state
    return result


def merge_day(dst, dates):
    for name, value in dates.items():
        current = dst.setdefault(name, empty_day())
        for metric in ("decisions", "excluded_past", "censored_future",
                       "labeled", "switches"):
            current[metric] += value[metric]
        for level in LEVELS:
            current["positives"][str(level)] += value["positives"][str(level)]
        for rule in RULES:
            current["rules"][rule]["missing"] += value["rules"][rule]["missing"]
            for level in LEVELS:
                for kind in ("tp", "fp", "fn", "tn"):
                    current["rules"][rule]["scores"][str(level)][kind] += (
                        value["rules"][rule]["scores"][str(level)][kind])


def _divide(a, b):
    return round(a / b, 6) if b else None


def _cohort(dates, names):
    result = {"dates": len(names), "labeled": sum(dates[d]["labeled"] for d in names),
              "rules": {}}
    for rule in RULES:
        entry = {"missing_features": sum(dates[d]["rules"][rule]["missing"] for d in names),
                 "thresholds": {}}
        for level in LEVELS:
            c = {kind: sum(dates[d]["rules"][rule]["scores"][str(level)][kind]
                           for d in names) for kind in ("tp", "fp", "fn", "tn")}
            tp, fp, fn, tn = (c[k] for k in ("tp", "fp", "fn", "tn"))
            entry["thresholds"][f"{level}x"] = {
                **c, "precision": _divide(tp, tp + fp),
                "recall": _divide(tp, tp + fn),
                "false_positive_rate": _divide(fp, fp + tn),
                "baseline_prevalence": _divide(tp + fn, tp + fp + fn + tn),
            }
        result["rules"][rule] = entry
    return result


def aggregate(dates):
    eligible = sorted(d for d in dates if dates[d]["labeled"])
    result = {"observed_days": len(eligible),
              "candidate_decisions": sum(d["decisions"] for d in dates.values()),
              "past_excluded": sum(d["excluded_past"] for d in dates.values()),
              "future_censored": sum(d["censored_future"] for d in dates.values()),
              "labeled_rolling_proxy_windows": sum(d["labeled"] for d in dates.values()),
              "strike_switches": sum(d["switches"] for d in dates.values()),
              "proxy_positive_windows": {
                  str(level): sum(d["positives"][str(level)] for d in dates.values())
                  for level in LEVELS},
              "chronological_session_holdout": None}
    if len(eligible) >= 10:
        split = max(2, math.ceil(len(eligible) * .25))
        result["chronological_session_holdout"] = {
            "earlier": _cohort(dates, eligible[:-split]),
            "later": _cohort(dates, eligible[-split:]),
            "earlier_last": eligible[-split - 1],
            "later_first": eligible[-split],
        }
    else:
        result["holdout_limitation"] = "At least 10 observed session dates required"
    return result


def run(args, client=None, sleeper=clock.sleep):
    if args.from_date > args.through or args.from_date < five_year_start(args.through):
        raise ValueError("Invalid date range or older than five years")
    if not 1 <= args.horizon <= 120 or args.min_price <= 0:
        raise ValueError("Invalid research parameters")
    if not math.isfinite(args.pause) or args.pause < .25:
        raise ValueError("Minimum API delay is 0.25 seconds")
    if args.max_requests is not None and args.max_requests <= 0:
        raise ValueError("max-requests must be positive")
    planned = sum(1 for _ in queries(args.from_date, args.through, args.full))
    report = {
        "status": "PREVIEW_NO_API_CALLS", "planned_api_requests": planned,
        "completed_api_requests": 0, "empty_responses": 0,
        "rows_observed": 0, "market_files_saved": 0, "orders_sent": 0,
        "missing_cells": {"volume": 0, "oi": 0, "iv": 0, "spot": 0},
        "invalid_negative_source_cells": {"volume": 0, "oi": 0, "iv": 0},
        "history_verified_exact_contract": False,
        "real_2x_3x_5x_10x_multiplier_events": None,
        "historical_gamma_verified": False,
        "real_trading_accuracy": None,
        "critical_limits": [
            "Dhan rolling strike and expiry bucket are not a verified fixed contract.",
            "Observed proxy ratios use future BEST close, not executable exits.",
            "Future strike-switch censoring biases proxy event estimates.",
            "Multiple ATM offsets can duplicate observations; trials are correlated.",
            "Historical Gamma, bid/ask spreads, and executable fees are unavailable.",
        ]}
    if not args.execute:
        return report
    if client is None:
        load_local_credentials()
        client = DhanClient()
    if str(client.profile().get("dataPlan", "")).lower() != "active":
        report["status"] = "BLOCKED_DATA_PLAN_NOT_ACTIVE"
        return report
    dates = {}
    for q in queries(args.from_date, args.through, args.full):
        if args.max_requests and report["completed_api_requests"] >= args.max_requests:
            break
        sleeper(args.pause)
        entry = {"series": f"{q.expiry_flag}_{q.expiry_code}_{q.strike}_{q.side}",
                 "start": str(q.start), "end": str(q.end),
                 "payload": q.payload(), "endpoint": "/charts/rollingoption"}
        try:
            raw = client._call("POST", "/charts/rollingoption", q.payload())
            frame = parse_bars(raw, entry)
            if not frame.empty:
                merge_day(dates, study_chunk(frame, args.horizon, args.min_price))
        except (DhanAPIError, ValueError, KeyError, TypeError) as error:
            report["status"] = "STOPPED_AT_FAILED_REQUEST"
            report["failure"] = {"start": str(q.start), "end_exclusive": str(q.end),
                                 "expiry_flag": q.expiry_flag, "expiry_code": q.expiry_code,
                                 "strike": q.strike, "side": q.side,
                                 "reason": type(error).__name__}
            if isinstance(error, DhanAPIError):
                report["failure"]["dhan"] = error.diagnostic()
            break
        report["completed_api_requests"] += 1
        report["empty_responses"] += int(frame.empty)
        report["rows_observed"] += int(len(frame))
        for field in report["missing_cells"]:
            report["missing_cells"][field] += (
                int(frame[field].isna().sum()) if field in frame else int(len(frame))
            )
        for field in report["invalid_negative_source_cells"]:
            flag = field + "_invalid_negative"
            if flag in frame:
                report["invalid_negative_source_cells"][field] += int(frame[flag].sum())
        del raw, frame
        if args.progress and report["completed_api_requests"] % 25 == 0:
            print(f'Processed {report["completed_api_requests"]}/{planned} responses in RAM',
                  file=sys.stderr)
    else:
        report["status"] = ("COMPLETE_WITH_EMPTY_DATA" if report["empty_responses"]
                            else "COMPLETE_PROXY_ONLY")
    if report["status"] == "PREVIEW_NO_API_CALLS":
        report["status"] = "PARTIAL_MAX_REQUESTS"
    report["proxy_analysis"] = aggregate(dates)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-date", type=date.fromisoformat, default=date(2026, 10, 2))
    p.add_argument("--through", type=date.fromisoformat, default=date(2026, 10, 9))
    p.add_argument("--full", action="store_true",
                   help="All supported weekly/monthly code 1/2/3 and ATM offsets, CALL/PUT")
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--min-price", type=float, default=2.0)
    p.add_argument("--pause", type=float, default=.35)
    p.add_argument("--max-requests", type=int)
    p.add_argument("--progress", action="store_true")
    p.add_argument("--execute", action="store_true",
                   help="Read from Dhan into RAM; never persist market data")
    args = p.parse_args()
    try:
        output = run(args)
    except (ValueError, RuntimeError, DhanAPIError) as exc:
        raise SystemExit("Study stopped safely: " + type(exc).__name__) from None
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
