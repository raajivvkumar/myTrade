"""Minute-resolution retrospective premium discovery on Dhan rolling options.

RESEARCH ONLY. Rolling ATM aliases do not certify an option security ID,
expiry or historical Gamma. No disk writes, market data cache, or orders.
An observed multiplier crossing is a minute CLOSE relative to a historical
same-segment entry OPEN; it is not an executable fill or profit estimate.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import time
import math

import numpy as np
import pandas as pd

from app.research.vedic_strike_transit_study import compound_number, date_numerology

LEVELS = (2, 3, 5, 10)
RULES = ("premium20", "volume2_premium20", "volume2_oi10")
IST = "Asia/Kolkata"


def _minute(stamp):
    ts = pd.Timestamp(stamp)
    if ts.tzinfo is None:
        ts = ts.tz_localize(IST)
    else:
        ts = ts.tz_convert(IST)
    return ts.isoformat(timespec="minutes")


def _empty(day, series, side):
    return {
        "date": str(day), "series": series, "side": side,
        "bars": 0, "segments": 0, "strike_switches": 0,
        "missing_minute_splits": 0,
        "entry_below_min_price": 0,
        "possible_decision_entries": 0, "past_history_unavailable": 0,
        "scored": {str(level): {
            "positive_windows": 0, "known_negative_windows": 0,
            "censored": {},
            "rule_confusion": {name: {"tp": 0, "fp": 0, "fn": 0,
                                      "tn": 0, "missing": 0}
                               for name in RULES},
            "strike_root": {},
        } for level in LEVELS},
        "episodes": {str(level): 0 for level in LEVELS},
        "event_examples": {str(level): [] for level in LEVELS},
    }


def _segments(group):
    """Split on actual strike changes and discontinuous minute timestamps.

    Does NOT equate same strike to a single expiry or option contract.
    """
    group = group.sort_values("timestamp").reset_index(drop=True)
    if group.timestamp.duplicated().any():
        raise ValueError("Duplicate minute timestamps in rolling query")
    if group.empty:
        return []
    splits = []
    start = 0
    for i in range(1, len(group)):
        time_delta = group.timestamp.iloc[i] - group.timestamp.iloc[i - 1]
        same_strike = group.actual_strike.iloc[i] == group.actual_strike.iloc[i - 1]
        if time_delta != pd.Timedelta(minutes=1) or not same_strike:
            reason = "strike_switch" if not same_strike else "missing_minutes"
            splits.append((group.iloc[start:i].reset_index(drop=True), reason))
            start = i
    last = group.iloc[start:].reset_index(drop=True)
    last_stamp = last.timestamp.iloc[-1]
    reason = ("session_boundary" if last_stamp.time() >= time(15, 29)
              else "response_or_session_end")
    splits.append((last, reason))
    return splits


def _features(bars, index):
    """Past-only 5m momentum, 5m-vs-10m volume, and 5m OI change."""
    if index < 15:
        return None
    prices = bars["close"]
    volumes = bars["volume"]
    oi = bars["oi"]
    mom = None
    if (math.isfinite(prices[index]) and
            math.isfinite(prices[index - 5]) and prices[index - 5] > 0):
        mom = 100 * (prices[index] / prices[index - 5] - 1)
    before = volumes[index - 14:index - 4]  # exactly 10 past volume bars
    recent = volumes[index - 4:index + 1]  # five most recent bars
    vol = None
    if (len(before) == 10 and len(recent) == 5 and
            np.isfinite(before).all() and np.isfinite(recent).all()
            and before.mean() > 0):
        vol = float(recent.mean() / before.mean())
    oi_change = None
    if (math.isfinite(oi[index]) and math.isfinite(oi[index - 5])
            and oi[index - 5] > 0):
        oi_change = 100 * (oi[index] / oi[index - 5] - 1)
    return {
        "premium5m_pct": mom,
        "volume5_vs_previous10": vol,
        "oi5m_pct": oi_change,
        "signals": {
            "premium20": None if mom is None else bool(mom >= 20),
            "volume2_premium20": (None if mom is None or vol is None
                                   else bool(mom >= 20 and vol >= 2)),
            "volume2_oi10": (None if vol is None or oi_change is None
                              else bool(vol >= 2 and oi_change >= 10)),
        },
    }


def _first_prior_signal(bars, index, rule, lookback=15):
    # Entry is the OPEN of minute index. Signals may use only earlier closes.
    for i in range(max(15, index - lookback), index):
        record = _features(bars, i)
        if record is not None and record["signals"][rule] is True:
            return _minute(bars["timestamp"][i])
    return None


def _first_pre_crossing_sign(bars, index, crossing_index, rule):
    """First observed signal strictly BEFORE crossing; retrospective only.

    Signal may occur after the hypothetical entry, in which case it is
    not an advance entry alert. Never label a crossing bar itself as a
    predictive 'start sign'.
    """
    for i in range(max(15, index), crossing_index):
        record = _features(bars, i)
        if record is not None and record["signals"][rule] is True:
            return i
    return None


def _safe_number(value):
    return round(float(value), 6) if np.isfinite(value) else None


def scan_rolling_frame(frame, *, series, side, horizon=60, min_price=2.0,
                       astrology_fn=None, max_examples=12):
    """Analyze every observed 1m starting price, not just 30m clock slots.

    A threshold attained before a strike switch is counted as an observed
    positive. If a threshold was NOT hit but no full future horizon exists,
    mark the outcome censored, NOT negative. Independent episodes skip
    entry anchors until after the prior threshold crossing, separately
    for each threshold. All episode counts are still correlated.
    """
    if side not in ("CALL", "PUT") or not 1 <= horizon <= 120:
        raise ValueError("Invalid side/horizon")
    if not math.isfinite(min_price) or min_price <= 0:
        raise ValueError("Invalid minimum premium")
    if type(max_examples) is not int or not 0 <= max_examples <= 200:
        raise ValueError("Invalid max_examples")
    required = {"timestamp", "actual_strike", "open", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("Missing rolling candle fields")
    if frame.empty:
        return {}
    if frame[["timestamp", "actual_strike", "open", "close"]].isna().any().any():
        raise ValueError("Null mandatory market fields")
    outcome = {}
    for day, raw_day in frame.groupby(frame.timestamp.dt.date, sort=True):
        stats = _empty(day, series, side)
        stats["bars"] = int(len(raw_day))
        for segment, end_reason in _segments(raw_day):
            stats["segments"] += 1
            stats["strike_switches"] += int(end_reason == "strike_switch")
            stats["missing_minute_splits"] += int(end_reason == "missing_minutes")
            strike = float(segment.actual_strike.iloc[0])
            strike_nums = (compound_number(strike)
                           if strike.is_integer() and strike > 0 else None)
            root = str(strike_nums["root_number"]) if strike_nums else None
            date_nums = date_numerology(day)
            values = {
                key: pd.to_numeric(segment[key], errors="coerce").to_numpy(
                    dtype=float)
                if key in segment else np.full(len(segment), np.nan)
                for key in ("open", "close", "volume", "oi")
            }
            values["timestamp"] = list(segment.timestamp)
            n = len(segment)
            last_episode_crossing = {level: -1 for level in LEVELS}
            for i in range(n):
                entry = float(values["open"][i])
                if not math.isfinite(entry) or entry < min_price:
                    stats["entry_below_min_price"] += 1
                    continue
                stats["possible_decision_entries"] += 1
                # Entry is the OPEN of minute i: last completed inputs are
                # at minute i-1, never at the entry-minute CLOSE.
                features = _features(values, i - 1)
                if features is None:
                    stats["past_history_unavailable"] += 1
                end = min(n, i + horizon + 1)
                future_closes = values["close"][i + 1:end]
                complete = i + horizon < n
                for level in LEVELS:
                    level_key = str(level)
                    scored = stats["scored"][level_key]
                    observed = np.flatnonzero(future_closes >= entry * level)
                    success = bool(observed.size)
                    if not success and not complete:
                        censored = scored["censored"]
                        censored[end_reason] = censored.get(end_reason, 0) + 1
                        continue
                    if success:
                        scored["positive_windows"] += 1
                    else:
                        scored["known_negative_windows"] += 1
                    if root is not None:
                        group = scored["strike_root"].setdefault(
                            root, {"positive": 0, "negative": 0})
                        group["positive" if success else "negative"] += 1
                    if features is not None:
                        for rule, signal in features["signals"].items():
                            cells = scored["rule_confusion"][rule]
                            if signal is None:
                                cells["missing"] += 1
                                continue
                            cell = ("tp" if success else "fp") if signal else (
                                "fn" if success else "tn")
                            cells[cell] += 1
                    if not success:
                        continue
                    cross_idx = i + 1 + int(observed[0])
                    if i <= last_episode_crossing[level]:
                        continue
                    last_episode_crossing[level] = cross_idx
                    stats["episodes"][level_key] += 1
                    if len(stats["event_examples"][level_key]) >= max_examples:
                        continue
                    pre_cross_index = _first_pre_crossing_sign(
                        values, i, cross_idx, "premium20")
                    event = {
                        "day": str(day), "series": series, "side": side,
                        "rolling_strike": strike,
                        "strike_numerology": strike_nums,
                        "date_numerology": date_nums,
                        "threshold": f"{level}x",
                        "first_eligible_entry_bar_ist": _minute(values["timestamp"][i]),
                        "first_observed_crossing_close_ist": _minute(
                            values["timestamp"][cross_idx]),
                        "minutes_from_entry_bar": cross_idx - i,
                        "hypothetical_entry_open": _safe_number(entry),
                        "observed_crossing_close": _safe_number(
                            values["close"][cross_idx]),
                        "first_prior_momentum20_sign_ist": _first_prior_signal(
                            values, i, "premium20"),
                        "first_momentum20_sign_before_crossing_ist": (
                            _minute(values["timestamp"][pre_cross_index])
                            if pre_cross_index is not None else None),
                        "minutes_from_momentum_sign_to_crossing": (
                            cross_idx - pre_cross_index
                            if pre_cross_index is not None else None),
                        "first_prior_volume2_momentum20_sign_ist":
                            _first_prior_signal(values, i, "volume2_premium20"),
                        "first_prior_volume2_oi10_sign_ist": _first_prior_signal(
                            values, i, "volume2_oi10"),
                        "past_features_at_entry": features,
                        "source_identity": "ROLLING_ATM_ALIAS_STRIKE_STABLE_ONLY",
                        "verified_fixed_contract": False,
                        "verified_historical_gamma": False,
                        "verified_executable_pnl": False,
                        "minute_label_convention": "UNVERIFIED_OPEN_OR_CLOSE",
                    }
                    if astrology_fn is not None:
                        event["vedic_at_entry_bar"] = astrology_fn(
                            values["timestamp"][i])
                        event["vedic_at_first_crossing_bar"] = astrology_fn(
                            values["timestamp"][cross_idx])
                        if pre_cross_index is not None:
                            event["vedic_at_first_momentum20_sign"] = astrology_fn(
                                values["timestamp"][pre_cross_index])
                    stats["event_examples"][level_key].append(event)
        outcome[str(day)] = stats
    return outcome


def summarize_scans(chunks, *, max_examples=24):
    """Chronological separate descriptive metrics and alias-duplication hints."""
    if not 0 <= max_examples <= 200:
        raise ValueError("Invalid event example cap")
    records = [rec for batch in chunks for rec in batch.values()]
    dates = sorted({r["date"] for r in records})
    samples = []
    summary = {
        "sampled_session_dates": len(dates),
        "rolling_series_day_groups": len(records),
        "bars_analysed": sum(x["bars"] for x in records),
        "segments": sum(x["segments"] for x in records),
        "strike_switches": sum(x["strike_switches"] for x in records),
        "missing_minute_splits": sum(x["missing_minute_splits"] for x in records),
        "rolling_identity_verified_fixed_contract": False,
        "historical_gamma_verified": False,
        "executable_trading_accuracy": None,
        "thresholds": {},
        "examples": {},
        "chronological_holdout": None,
    }
    for level in LEVELS:
        key = str(level)
        episodes = sum(r["episodes"][key] for r in records)
        positives = sum(r["scored"][key]["positive_windows"] for r in records)
        negatives = sum(r["scored"][key]["known_negative_windows"] for r in records)
        censored = {}
        rules = {name: {k: 0 for k in ("tp", "fp", "fn", "tn", "missing")}
                 for name in RULES}
        roots = {}
        for r in records:
            d = r["scored"][key]
            for why, n in d["censored"].items():
                censored[why] = censored.get(why, 0) + n
            for name in RULES:
                for k in rules[name]:
                    rules[name][k] += d["rule_confusion"][name][k]
            for root, outcome in d["strike_root"].items():
                target = roots.setdefault(root, {"positive": 0, "negative": 0})
                for label in ("positive", "negative"):
                    target[label] += outcome[label]
            samples.extend(r["event_examples"][key])
        for name, c in rules.items():
            tp, fp, fn, tn = (c[k] for k in ("tp", "fp", "fn", "tn"))
            c["precision"] = round(tp / (tp + fp), 6) if tp + fp else None
            c["recall"] = round(tp / (tp + fn), 6) if tp + fn else None
            c["tested_feature_complete_windows"] = tp + fp + fn + tn
        summary["thresholds"][f"{level}x"] = {
            "observed_episode_count_not_deduped_across_aliases": episodes,
            "positive_anchor_windows_overlapping": positives,
            "known_negative_anchor_windows": negatives,
            "censored_unobserved_outcomes": censored,
            "positive_anchor_base_rate": (
                round(positives / (positives + negatives), 6)
                if positives + negatives else None),
            "past_only_rule_scores": rules,
            "strike_root_counts": roots,
        }
        same_level = [s for s in samples if s["threshold"] == f"{level}x"]
        same_level.sort(key=lambda s: (
            s["first_observed_crossing_close_ist"], s["series"]))
        seen = set()
        duplicates = 0
        uniques = []
        for s in same_level:
            # Same observed crossing/side/strike may be multiple real
            # contract expiries. Group as possible alias coincidences only.
            identity = (s["day"], s["side"], s["rolling_strike"],
                        s["first_observed_crossing_close_ist"])
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            if len(uniques) < max_examples:
                uniques.append(s)
        summary["examples"][f"{level}x"] = {
            "records": uniques,
            "potential_alias_coincidences_in_sample": duplicates,
            "examples_truncated": episodes > len(same_level),
        }
        samples.clear()
    # No holdout claim without independent fixed-contract expiries; date-only
    # split cannot rule out expiry/alias leakage.
    summary["chronological_holdout"] = {
        "available_dates": len(dates),
        "contract_expiry_independent_holdout": False,
        "status": "BLOCKED_NO_VERIFIED_FIXED_CONTRACT_IDENTITIES",
    }
    summary["cautions"] = [
        "Observed 1m CLOSE crossing vs same-segment OPEN is retrospective, not a fill.",
        "Positive before a future strike change is observable; unobserved negatives are censored.",
        "Rolling strike stability does not authenticate fixed expiry/security ID.",
        "One-minute relative-strike aliases may repeat an underlying trade or different expiries.",
        "Multiple overlapping entry anchors are correlated; episode counts are not independent.",
        "Astrology/numerology associations are descriptive, not Gamma mechanisms or predictions.",
    ]
    return summary
