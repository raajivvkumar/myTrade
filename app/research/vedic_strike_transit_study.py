"""Research-only strike numerology + Vedic planetary transitions.

No inference of causal influence on Gamma; rolling Dhan options lack fixed expiry.
Coordinates require optional pyswisseph only when requested. Returns small
aggregates and earliest examples, never saves market candles.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from math import ceil

import pandas as pd

from app.research.vedic_event_ephemeris import IST, sidereal_positions

TRANSIT_FIELDS = ("rashi", "nakshatra", "nakshatra_pada", "retrograde")
PLANET_NAMES = ("Sun", "Moon", "Mars", "Mercury", "Jupiter",
                "Venus", "Saturn", "Rahu", "Ketu")


def compound_number(number):
    """Digit sum and reduced root; numbers are NOT Chaldean name values."""
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise ValueError("Expected positive integer strike")
    if not float(number).is_integer() or number <= 0:
        raise ValueError("Strike must be a positive whole number")
    digits = [int(d) for d in str(int(number))]
    compound = sum(digits)
    root = compound
    while root > 9:
        root = sum(int(d) for d in str(root))
    return {"strike_price": int(number), "compound_total": compound,
            "root_number": root}


def date_numerology(value):
    day = date.fromisoformat(str(value))
    compound = sum(int(d) for d in day.strftime("%d%m%Y"))
    root = compound
    while root > 9:
        root = sum(int(d) for d in str(root))
    return {
        "date": day.isoformat(),
        "calendar_weekday": day.strftime("%A"),
        "day_number": day.day,
        "day_root": (day.day - 1) % 9 + 1,
        "month_number": day.month,
        "date_compound_total": compound,
        "date_root_number": root,
    }


def _iso(dt):
    return dt.isoformat(timespec="seconds")


@lru_cache(maxsize=128)
def session_transits(day_iso):
    """Calculate planetary sidereal transitions within regular NSE trading hours.

    Scan 10-minute brackets, then bisect to a modeled transition second.
    May miss multiple boundaries traversed and reversed within a 10-minute
    bracket; use exact fixed-contract data before any high-stakes inference.
    """
    d = date.fromisoformat(str(day_iso))
    begin = datetime.combine(d, time(9, 15), IST)
    end = datetime.combine(d, time(15, 29), IST)
    step = timedelta(minutes=10)
    output = []
    before = begin
    before_chart = sidereal_positions(before, precision="seconds")
    while before < end:
        after = min(before + step, end)
        after_chart = sidereal_positions(after, precision="seconds")
        for planet in PLANET_NAMES:
            a = before_chart["planets"][planet]
            b = after_chart["planets"][planet]
            for field in TRANSIT_FIELDS:
                if a[field] == b[field]:
                    continue
                lower, upper = before, after
                old_value = a[field]
                while (upper - lower).total_seconds() > 1:
                    middle = lower + timedelta(
                        seconds=int((upper - lower).total_seconds() // 2))
                    probe = sidereal_positions(middle, precision="seconds")
                    if probe["planets"][planet][field] == old_value:
                        lower = middle
                    else:
                        upper = middle
                output.append({
                    "planet": planet,
                    "transition_type": field,
                    "from": old_value, "to": b[field],
                    "calculated_transition_time_ist": _iso(upper),
                    "precision": "modeled to nearest second; not a market timestamp",
                    "zodiac": "Vedic sidereal Lahiri",
                    "node_policy": "Mean Rahu / opposite Ketu",
                })
        before, before_chart = after, after_chart
    return sorted(output, key=lambda r: (
        r["calculated_transition_time_ist"], r["planet"], r["transition_type"]))


def _exact_minute_at_or_after(stamp):
    """Convert modeled transition second to first available minute label."""
    minute = stamp.replace(second=0, microsecond=0)
    if stamp > minute:
        minute += timedelta(minutes=1)
    return pd.Timestamp(minute.replace(tzinfo=None))


def _window_effect(session, pivot, *, strike):
    """Compare 15 minutes before against the subsequent 30 minutes.

    Require every 1m bar, one same strike and no look-through at switches.
    The highest later CLOSE is an optimistic retrospective bound, not P&L.
    """
    start = pivot - pd.Timedelta(minutes=15)
    end = pivot + pd.Timedelta(minutes=30)
    part = session.loc[
        session.timestamp.between(start, end, inclusive="both")
    ].sort_values("timestamp")
    if len(part) != 46:
        return {"reason": "missing_or_outside_session"}
    if not part.timestamp.diff().iloc[1:].eq(pd.Timedelta(minutes=1)).all():
        return {"reason": "non_contiguous_minutes"}
    if part.actual_strike.nunique() != 1:
        return {"reason": "strike_switch_during_effect_window"}
    if float(part.actual_strike.iloc[0]) != float(strike):
        return {"reason": "strike_not_stable"}
    starting = float(part.loc[part.timestamp == pivot, "close"].iloc[0])
    prior = float(part.loc[part.timestamp == start, "close"].iloc[0])
    ending = float(part.loc[part.timestamp == end, "close"].iloc[0])
    future = part.loc[part.timestamp > pivot]
    if starting <= 0 or prior <= 0:
        return {"reason": "invalid_reference_price"}
    peak = float(future.close.max())
    return {
        "reason": None,
        "price_at_first_minute_label": round(starting, 4),
        "premium_change_pre15_pct": round((starting / prior - 1) * 100, 4),
        "premium_change_post30_pct": round((ending / starting - 1) * 100, 4),
        "peak_close_to_start_ratio": round(peak / starting, 6),
        "proxy_2x_peak_close": peak >= 2 * starting,
    }


def transit_strike_observations(frame, *, series, expiry_flag, expiry_code):
    """List compact per-transit/per-strike effects, no raw option candles retained.

    Event/control comparisons across multiple roll aliases are NOT independent.
    """
    if frame.empty:
        return {"observations": [], "transit_calendar": [], "censored": {}}
    observations = []
    censored = {}
    transit_calendar = []
    for day, g in frame.groupby(frame.timestamp.dt.date):
        g = g.sort_values("timestamp").reset_index(drop=True)
        for transit in session_transits(day.isoformat()):
            transit_calendar.append(transit)
            t = datetime.fromisoformat(transit["calculated_transition_time_ist"])
            pivot = _exact_minute_at_or_after(t)
            at_pivot = g.loc[g.timestamp == pivot]
            if at_pivot.empty:
                censored["transit_minute_missing"] = censored.get(
                    "transit_minute_missing", 0) + 1
                continue
            strike = float(at_pivot.actual_strike.iloc[0])
            info = compound_number(strike) if strike.is_integer() else None
            if info is None:
                censored["noninteger_strike"] = censored.get(
                    "noninteger_strike", 0) + 1
                continue
            effect = _window_effect(g, pivot, strike=strike)
            if effect["reason"]:
                reason = effect["reason"]
                censored[reason] = censored.get(reason, 0) + 1
                continue
            # Compare with predeclared same-strike, same-side control windows.
            # +/-60 minutes is descriptive only: not an independent holdout.
            controls = []
            for delta in (-60, 60):
                control_pivot = pivot + pd.Timedelta(minutes=delta)
                control_bar = g.loc[g.timestamp == control_pivot]
                if (control_bar.empty
                        or float(control_bar.actual_strike.iloc[0]) != strike):
                    continue
                control = _window_effect(g, control_pivot, strike=strike)
                if control["reason"] is None:
                    controls.append(control["premium_change_post30_pct"])
            control_mean = (round(sum(controls) / len(controls), 4)
                            if controls else None)
            chart = sidereal_positions(t, precision="seconds")
            observations.append({
                "vedic_chart_at_transition": chart,
                "controls_available": len(controls),
                "control_mean_post30_pct": control_mean,
                "excess_post30_vs_control_pct_points": (
                    round(effect["premium_change_post30_pct"] - control_mean, 4)
                    if control_mean is not None else None),
                "date": str(day),
                "planet": transit["planet"],
                "transition_type": transit["transition_type"],
                "from": transit["from"],
                "to": transit["to"],
                "calculated_transition_time_ist": transit[
                    "calculated_transition_time_ist"],
                "observed_first_minute_label_ist": pivot.tz_localize(
                    IST).isoformat(timespec="minutes"),
                "series": series,
                "expiry_flag": expiry_flag,
                "expiry_code": expiry_code,
                "option_side": "CALL" if series.endswith("_CALL") else "PUT",
                "strike": info,
                "date_numerology": date_numerology(day),
                **effect,
                "scope": "ROLLING_STRIKE_PROXY_UNVERIFIED_EXPIRY",
            })
    return {
        "transit_calendar": transit_calendar[:200],
        "observations": observations,
        "censored": censored,
        "total_transit_instances": len(transit_calendar),
        "eligible_transit_strike_observations": len(observations),
        "data_limit": "Dhan rolling series is NOT a fixed option contract; no causal attribution",
    }


def summarize_transit_impact(groups, *, max_samples=60):
    """Group descriptive transition effects by planet, type, side and root.

    Counts may be correlated aliases; this is NOT an inferential significance test.
    """
    merged = []
    transit_calendar = {}
    censored = {}
    instances = 0
    eligible = 0
    for group in groups:
        instances += group["total_transit_instances"]
        eligible += group["eligible_transit_strike_observations"]
        merged.extend(group["observations"])
        for transition in group["transit_calendar"]:
            key = (transition["calculated_transition_time_ist"],
                   transition["planet"], transition["transition_type"])
            transit_calendar.setdefault(key, transition)
        for reason, count in group["censored"].items():
            censored[reason] = censored.get(reason, 0) + count
    buckets = {}
    strike_buckets = {}
    for x in merged:
        key = (x["planet"], x["transition_type"], x["option_side"],
               x["strike"]["root_number"])
        item = buckets.setdefault(key, {
            "observations": 0, "proxy_2x": 0, "sum_post30_pct": 0.0,
            "sum_peak_ratio": 0.0, "strike_examples": set()})
        item["observations"] += 1
        item["proxy_2x"] += int(x["proxy_2x_peak_close"])
        item["sum_post30_pct"] += x["premium_change_post30_pct"]
        item["sum_peak_ratio"] += x["peak_close_to_start_ratio"]
        item["strike_examples"].add(x["strike"]["strike_price"])
        exact_key = (x["planet"], x["transition_type"], x["option_side"],
                     x["strike"]["strike_price"])
        exact = strike_buckets.setdefault(exact_key, {
            "count": 0, "proxy_2x": 0, "sum_post30_pct": 0.0,
            "controls": 0, "sum_excess": 0.0})
        exact["count"] += 1
        exact["proxy_2x"] += int(x["proxy_2x_peak_close"])
        exact["sum_post30_pct"] += x["premium_change_post30_pct"]
        if x["controls_available"]:
            exact["controls"] += 1
            exact["sum_excess"] += x["excess_post30_vs_control_pct_points"]
    result = []
    for (planet, change, side, root), stat in sorted(buckets.items()):
        n = stat["observations"]
        result.append({
            "planet": planet, "change": change, "side": side, "strike_root": root,
            "observations": n, "proxy_2x": stat["proxy_2x"],
            "mean_post30_pct": round(stat["sum_post30_pct"] / n, 4),
            "mean_peak_close_ratio": round(stat["sum_peak_ratio"] / n, 4),
            "example_strikes": sorted(stat["strike_examples"])[:12],
        })
    exact_groups = []
    for (planet, change, side, strike_price), counts in sorted(
            strike_buckets.items()):
        n = counts["count"]
        numerology = compound_number(strike_price)
        exact_groups.append({
            "planet": planet, "transition_type": change,
            "option_side": side, "strike_price": strike_price,
            "compound_total": numerology["compound_total"],
            "root_number": numerology["root_number"],
            "observations": n,
            "proxy_2x": counts["proxy_2x"],
            "mean_post30_pct": round(counts["sum_post30_pct"] / n, 4),
            "matched_control_observations": counts["controls"],
            "mean_excess_vs_controls_pct_points": (
                round(counts["sum_excess"] / counts["controls"], 4)
                if counts["controls"] else None),
        })
    exact_groups.sort(key=lambda x: (
        -x["observations"], x["planet"], x["strike_price"]))
    transitions = [transit_calendar[k] for k in sorted(transit_calendar)]
    return {
        "unique_session_planetary_transitions": len(transitions),
        "session_planetary_transition_examples": transitions[:100],
        "transit_instances_across_aliases": instances,
        "eligible_strike_effect_windows": eligible,
        "censor_reasons": censored,
        "by_planet_change_side_strike_root": result,
        "by_exact_strike": exact_groups[:100],
        "total_exact_strike_groups": len(exact_groups),
        "first_examples": merged[:max_samples],
        "independent_contracts_verified": False,
        "predictive_significance": None,
        "note": "Exploratory associations. Control windows are +/-60m within same day/strike/side, not independent holdout; adjust for moneyness, time, volatility and correlated aliases before significance.",
    }
