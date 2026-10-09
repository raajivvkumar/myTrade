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
from app.broker.dhan_history import backward_windows, five_year_start, parse_bars, VALIDATION_RULES

LEVELS = (2, 3, 5, 10)
RULES = ("momentum20", "volume2_momentum20", "volume2_oi10")
FEATURES = ("premium_5m_pct", "premium_15m_pct", "volume_5m_vs_prior30",
            "oi_5m_pct", "oi_15m_pct", "iv_change_5m", "iv_change_15m",
            "spot_move_5m_pct", "strike_to_spot_pct")
PAST_REASONS = ("insufficient_history", "missing_past_minutes", "past_strike_switch")
FUTURE_REASONS = ("session_boundary", "insufficient_future",
                  "missing_future_minutes", "future_strike_switch",
                  "invalid_entry", "below_min_entry")


def ist_minute(stamp):
    """One-minute candle label in IST; not the exact intrabar transaction second."""
    return pd.Timestamp(stamp).tz_localize("Asia/Kolkata").isoformat(timespec="minutes")


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
            "past_exclusion_reasons": {k: 0 for k in PAST_REASONS},
            "future_censor_reasons": {k: 0 for k in FUTURE_REASONS},
            "event_examples": {str(level): [] for level in LEVELS},
            "moon_rashi_cohorts": {
                str(level): {"event": {}, "other": {}} for level in LEVELS},
            "labeled": 0, "switches": 0,
            "feature_cohorts": {
                str(level): {feature: {key: 0 for key in (
                    "event_n", "other_n", "event_sum", "other_sum")}
                    for feature in FEATURES} for level in LEVELS},
            "positives": {str(x): 0 for x in LEVELS},
            "rules": {name: {"missing": 0,
                              "scores": {str(x): {k: 0 for k in ("tp", "fp", "fn", "tn")}
                                         for x in LEVELS}}
                      for name in RULES}}


def _continuous(f):
    return bool(f.timestamp.diff().iloc[1:].eq(pd.Timedelta(minutes=1)).all())


def study_chunk(frame, horizon=30, min_price=2.0,
                volume_baseline_minutes=30, astrology_fn=None):
    """Rolling-option proxy, NOT certified fixed-contract Gamma.

    Normal mode: 35 prior bars. Exploratory short mode: 16 prior bars
    using 10 earlier minutes as volume baseline; 5m and 15m features.
    """
    if (not 1 <= horizon <= 120 or not math.isfinite(min_price)
            or min_price <= 0 or volume_baseline_minutes not in (10, 30)):
        raise ValueError("Invalid horizon, price, or baseline")
    past_bars = max(16, volume_baseline_minutes + 5)
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
            if i < past_bars - 1:
                state["excluded_past"] += 1
                state["past_exclusion_reasons"]["insufficient_history"] += 1
                continue
            pre = g.iloc[i - past_bars + 1:i + 1]
            if not _continuous(pre):
                state["excluded_past"] += 1
                state["past_exclusion_reasons"]["missing_past_minutes"] += 1
                continue
            if pre.actual_strike.nunique() != 1:
                state["excluded_past"] += 1
                state["past_exclusion_reasons"]["past_strike_switch"] += 1
                continue
            # Pre-decision features; no future bar used here.
            closes = pd.to_numeric(pre.close, errors="coerce")
            vols = pd.to_numeric(pre.volume, errors="coerce") if "volume" in pre else pd.Series(dtype=float)
            oi = pd.to_numeric(pre.oi, errors="coerce") if "oi" in pre else pd.Series(dtype=float)
            momentum = (closes.iloc[-1] / closes.iloc[-6] - 1) * 100 if (
                closes.iloc[-6] > 0) else np.nan
            v_old = vols.iloc[-(volume_baseline_minutes + 5):-5]
            v_now = vols.iloc[-5:]
            volratio = (v_now.mean() / v_old.mean()
                        if len(v_old) == volume_baseline_minutes
                        and len(v_now) == 5 and v_old.notna().all()
                        and v_now.notna().all() and v_old.mean() > 0 else np.nan)
            momentum15 = (100 * (closes.iloc[-1] / closes.iloc[-16] - 1)
                          if closes.iloc[-16] > 0 else np.nan)
            oi_pct = (100 * (oi.iloc[-1] / oi.iloc[-6] - 1)
                      if len(oi) >= 6 and pd.notna(oi.iloc[-1])
                      and pd.notna(oi.iloc[-6]) and oi.iloc[-6] > 0 else np.nan)
            oi_pct15 = (100 * (oi.iloc[-1] / oi.iloc[-16] - 1)
                        if len(oi) >= 16 and pd.notna(oi.iloc[-1])
                        and pd.notna(oi.iloc[-16]) and oi.iloc[-16] > 0 else np.nan)
            iv = pd.to_numeric(pre.iv, errors="coerce") if "iv" in pre else pd.Series(dtype=float)
            spot = pd.to_numeric(pre.spot, errors="coerce") if "spot" in pre else pd.Series(dtype=float)
            iv_delta = (float(iv.iloc[-1] - iv.iloc[-6])
                        if len(iv) >= 6 and pd.notna(iv.iloc[-1])
                        and pd.notna(iv.iloc[-6]) else np.nan)
            iv_delta15 = (float(iv.iloc[-1] - iv.iloc[-16])
                          if len(iv) >= 16 and pd.notna(iv.iloc[-1])
                          and pd.notna(iv.iloc[-16]) else np.nan)
            spot_move = (100 * (float(spot.iloc[-1]) / float(spot.iloc[-6]) - 1)
                         if len(spot) >= 6 and pd.notna(spot.iloc[-1])
                         and pd.notna(spot.iloc[-6]) and spot.iloc[-6] > 0 else np.nan)
            moneyness = (100 * (float(pre.actual_strike.iloc[-1]) / float(spot.iloc[-1]) - 1)
                         if len(spot) >= 1 and pd.notna(spot.iloc[-1])
                         and spot.iloc[-1] > 0 else np.nan)
            descriptive = dict(zip(FEATURES, (
                momentum, momentum15, volratio, oi_pct, oi_pct15,
                iv_delta, iv_delta15, spot_move, moneyness,
            )))
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
            reason = None
            if stamp + pd.Timedelta(minutes=horizon) > pd.Timestamp(
                    f"{day} 15:29"):
                reason = "session_boundary"
            elif len(seq) != horizon + 1:
                reason = "insufficient_future"
            elif not _continuous(seq):
                reason = "missing_future_minutes"
            elif seq.actual_strike.nunique() != 1:
                reason = "future_strike_switch"
            if reason:
                state["censored_future"] += 1
                state["future_censor_reasons"][reason] += 1
                continue
            forward = seq.iloc[1:]
            entry = float(forward.open.iloc[0])
            if not math.isfinite(entry):
                state["censored_future"] += 1
                state["future_censor_reasons"]["invalid_entry"] += 1
                continue
            if entry < min_price:
                state["censored_future"] += 1
                state["future_censor_reasons"]["below_min_entry"] += 1
                continue
            ratio = float(forward.close.max()) / entry
            state["labeled"] += 1
            astro_at_decision = astrology_fn(stamp) if astrology_fn else None
            moon_rashi = (astro_at_decision.get("planets", {}).get(
                "Moon", {}).get("rashi") if astro_at_decision else None)
            for threshold in LEVELS:
                key = str(threshold)
                truth = ratio >= threshold
                state["positives"][key] += int(truth)
                if moon_rashi:
                    cohort = state["moon_rashi_cohorts"][key][
                        "event" if truth else "other"]
                    cohort[moon_rashi] = cohort.get(moon_rashi, 0) + 1
                if truth and len(state["event_examples"][key]) < 3:
                    crossed = forward[forward.close >= entry * threshold].iloc[0]
                    event = {
                        "threshold": f"{threshold}x",
                        "series": str(g.series.iloc[0]) if "series" in g else "UNVERIFIED",
                        "actual_strike": float(pre.actual_strike.iloc[-1]),
                        "decision_time_ist": ist_minute(stamp),
                        "hypothetical_entry_time_ist": ist_minute(forward.timestamp.iloc[0]),
                        "first_observed_crossing_close_ist": ist_minute(crossed.timestamp),
                        "minutes_from_decision": int(
                            (crossed.timestamp - stamp).total_seconds() // 60),
                        "hypothetical_entry_open": round(entry, 4),
                        "first_observed_crossing_close": round(float(crossed.close), 4),
                        "best_future_close_ratio": round(ratio, 5),
                        "pre_signal_rules_true": [name for name, active in flags.items() if active],
                        "identity": "ROLLING_STRIKE_PROXY_UNVERIFIED_EXPIRY",
                        "price_note": "first 1m CLOSE, not intrabar crossing or fill",
                    }
                    if astrology_fn:
                        event["vedic_at_decision"] = astro_at_decision
                        event["vedic_at_first_crossing"] = astrology_fn(crossed.timestamp)
                    state["event_examples"][key].append(event)
                for feature, value in descriptive.items():
                    if pd.notna(value) and math.isfinite(float(value)):
                        slot = state["feature_cohorts"][str(threshold)][feature]
                        label = "event" if truth else "other"
                        slot[label + "_n"] += 1
                        slot[label + "_sum"] += float(value)
                for name in RULES:
                    if missing[name]:
                        if threshold == 2:
                            state["rules"][name]["missing"] += 1
                        continue  # Unknown feature is not a negative prediction.
                    alert = bool(flags[name])
                    cell = ("tp" if truth else "fp") if alert else ("fn" if truth else "tn")
                    state["rules"][name]["scores"][key][cell] += 1
        result[str(day)] = state
    return result


def merge_day(dst, dates):
    for name, value in dates.items():
        current = dst.setdefault(name, empty_day())
        for metric in ("decisions", "excluded_past", "censored_future",
                       "labeled", "switches"):
            current[metric] += value[metric]
        for field, reasons in (("past_exclusion_reasons", PAST_REASONS),
                               ("future_censor_reasons", FUTURE_REASONS)):
            for reason in reasons:
                current[field][reason] += value[field][reason]
        for level in LEVELS:
            key = str(level)
            for example in value["event_examples"][key]:
                if len(current["event_examples"][key]) < 12:
                    current["event_examples"][key].append(example)
            for cohort in ("event", "other"):
                for rashi, count in value["moon_rashi_cohorts"][key][cohort].items():
                    group = current["moon_rashi_cohorts"][key][cohort]
                    group[rashi] = group.get(rashi, 0) + count
        for level in LEVELS:
            current["positives"][str(level)] += value["positives"][str(level)]
        for level in LEVELS:
            for feature in FEATURES:
                for metric in ("event_n", "other_n", "event_sum", "other_sum"):
                    current["feature_cohorts"][str(level)][feature][metric] += (
                        value["feature_cohorts"][str(level)][feature][metric])
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
              "feature_means": {}, "rules": {}}
    for level in LEVELS:
        bucket = {}
        for feature in FEATURES:
            sums = {key: sum(dates[d]["feature_cohorts"][str(level)][feature][key]
                             for d in names)
                    for key in ("event_n", "other_n", "event_sum", "other_sum")}
            bucket[feature] = {
                "events_available": sums["event_n"],
                "non_events_available": sums["other_n"],
                "event_mean": _divide(sums["event_sum"], sums["event_n"]),
                "non_event_mean": _divide(sums["other_sum"], sums["other_n"]),
            }
        result["feature_means"][f"{level}x"] = bucket
    for rule in RULES:
        entry = {"missing_features": sum(dates[d]["rules"][rule]["missing"] for d in names),
                 "thresholds": {}}
        for level in LEVELS:
            c = {kind: sum(dates[d]["rules"][rule]["scores"][str(level)][kind]
                           for d in names) for kind in ("tp", "fp", "fn", "tn")}
            tp, fp, fn, tn = (c[k] for k in ("tp", "fp", "fn", "tn"))
            entry["thresholds"][f"{level}x"] = {
                **c, "tested_feature_complete_windows": tp + fp + fn + tn,
                "precision": _divide(tp, tp + fp),
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
              "past_exclusion_reasons": {
                  key: sum(d["past_exclusion_reasons"][key] for d in dates.values())
                  for key in PAST_REASONS},
              "future_censor_reasons": {
                  key: sum(d["future_censor_reasons"][key] for d in dates.values())
                  for key in FUTURE_REASONS},
              "first_crossing_examples": {
                  f"{level}x": sorted(
                      (ex for day in dates.values()
                       for ex in day["event_examples"][str(level)]),
                      key=lambda ex: (ex["first_observed_crossing_close_ist"], ex["series"]),
                  )[:30] for level in LEVELS},
              "vedic_moon_rashi_at_decision": {
                  f"{level}x": {
                      label: {
                          rashi: sum(day["moon_rashi_cohorts"][str(level)][label].get(
                              rashi, 0) for day in dates.values())
                          for rashi in sorted({
                              sign for day in dates.values()
                              for sign in day["moon_rashi_cohorts"][str(level)][label]})
                      } for label in ("event", "other")
                  } for level in LEVELS},
              "chronological_session_holdout": None}
    result["descriptive_5m_precursor_comparison"] = (
        _cohort(dates, eligible)["feature_means"] if eligible else {}
    )
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
    if (not 1 <= args.horizon <= 120 or args.min_price <= 0
            or getattr(args, "volume_baseline_minutes", 30) not in (10, 30)):
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
        "volume_baseline_minutes": getattr(args, "volume_baseline_minutes", 30),
        "precursor_features": "5m + 15m; original frozen 5m rules unchanged",
        "timestamp_precision": "1-minute CLOSE label, not exact intrabar transaction time",
        "vedic_astrology_enabled": bool(getattr(args, "vedic_astrology", False)),
        "astrology_evidence": "descriptive association, not evidence of Gamma causation",
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
    transit_groups = []
    astrology_fn = None
    if getattr(args, "vedic_astrology", False):
        from app.research.vedic_event_ephemeris import sidereal_positions
        from app.research.vedic_strike_transit_study import (
            transit_strike_observations, summarize_transit_impact,
        )
        astrology_fn = sidereal_positions
    for q in queries(args.from_date, args.through, args.full):
        if args.max_requests and report["completed_api_requests"] >= args.max_requests:
            break
        sleeper(args.pause)
        entry = {"series": f"{q.expiry_flag}_{q.expiry_code}_{q.strike}_{q.side}",
                 "start": str(q.start), "end": str(q.end),
                 "payload": q.payload(), "endpoint": "/charts/rollingoption"}
        stage = "REQUEST"
        try:
            raw = client._call("POST", "/charts/rollingoption", q.payload())
            stage = "VALIDATE_BARS"
            frame = parse_bars(raw, entry)
            stage = "PROXY_RESEARCH"
            if not frame.empty:
                merge_day(dates, study_chunk(
                    frame, args.horizon, args.min_price,
                    volume_baseline_minutes=getattr(args, "volume_baseline_minutes", 30),
                    astrology_fn=astrology_fn))
                if astrology_fn:
                    transit_groups.append(transit_strike_observations(
                        frame, series=entry["series"],
                        expiry_flag=q.expiry_flag, expiry_code=q.expiry_code))
        except (DhanAPIError, ValueError, KeyError, TypeError, RuntimeError) as error:
            report["status"] = "STOPPED_AT_FAILED_REQUEST"
            report["failure"] = {"start": str(q.start), "end_exclusive": str(q.end),
                                 "expiry_flag": q.expiry_flag, "expiry_code": q.expiry_code,
                                 "strike": q.strike, "side": q.side,
                                 "reason": type(error).__name__, "stage": stage}
            if isinstance(error, DhanAPIError):
                report["failure"]["dhan"] = error.diagnostic()
            elif stage == "VALIDATE_BARS" and str(error) in VALIDATION_RULES:
                report["failure"]["validation_rule"] = str(error)
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
    if astrology_fn:
        report["vedic_transit_strike_numerology"] = summarize_transit_impact(transit_groups)
        report["vedic_transit_strike_numerology"]["verified_market_gamma_effect"] = None
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from-date", type=date.fromisoformat, default=date(2026, 10, 2))
    p.add_argument("--through", type=date.fromisoformat, default=date(2026, 10, 9))
    p.add_argument("--full", action="store_true",
                   help="All supported weekly/monthly code 1/2/3 and ATM offsets, CALL/PUT")
    p.add_argument("--horizon", type=int, default=30)
    p.add_argument("--min-price", type=float, default=2.0)
    p.add_argument("--volume-baseline-minutes", type=int, choices=(10, 30),
                   default=30, help="30=original 35-bar mode; 10=exploratory 16-bar mode")
    p.add_argument("--vedic-astrology", action="store_true",
                   help="Offline Lahiri zodiac at decision/crossing minute; requires pyswisseph")
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
