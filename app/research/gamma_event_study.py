"""Retrospective Gamma Multiplier event/control study, memory-only.

Exactly one NIFTY option contract per input CSV. No model fitting, orders,
historical storage or market-data network calls. Window labels use FUTURE
option closes; fingerprints use ONLY contemporaneous and preceding data.
"""
from __future__ import annotations

from datetime import date, time
from typing import Iterable

import numpy as np
import pandas as pd
import re

from app.research.gamma_lab import exact_contract_observations

WINDOWS = (5, 15, 30, 60)
CONDITION_SPECS = {
    "volume_surge_2x": ("volume_5m_vs_prev30", 2.0, ">="),
    "premium_momentum_20pct": ("premium_return_5m_pct", 20.0, ">="),
    "oi_rise_10pct": ("oi_change_5m_pct", 10.0, ">="),
    "iv_rising": ("iv_change_5m", 0.0, ">"),
    "gamma_rising": ("gamma_change_5m", 0.0, ">"),
}


def _parse_time(values: pd.Series) -> pd.Series:
    result = pd.to_datetime(values, errors="coerce")
    if isinstance(result.dtype, pd.DatetimeTZDtype):
        return result.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    if not pd.api.types.is_datetime64_any_dtype(result):
        raise ValueError("Option timestamps need one consistent timezone")
    return result


def _safe_positive(x: pd.Series) -> pd.Series:
    return x.where(x > 0)


def _features_for_contract(source: pd.DataFrame) -> pd.DataFrame:
    """Trailing features, not event labels: no feature reads a future bar."""
    df = source.copy()
    df["timestamp"] = _parse_time(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    numeric = ["open", "high", "low", "close", "volume", "oi"]
    optional = ["gamma", "delta", "iv", "theta", "spot", "bid", "ask"]
    for column in numeric:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in optional:
        df[column] = (
            pd.to_numeric(df[column], errors="coerce")
            if column in df.columns else np.nan
        )
    chunks = []
    for _, group in df.groupby(df.timestamp.dt.date, sort=True):
        g = group.copy()
        for minutes in WINDOWS:
            before = g.close.shift(minutes)
            g[f"premium_return_{minutes}m_pct"] = (
                (g.close / _safe_positive(before) - 1) * 100
            )
            g[f"oi_change_{minutes}m_pct"] = (
                (g.oi / _safe_positive(g.oi.shift(minutes)) - 1) * 100
            )
            for field in ("gamma", "delta", "iv", "theta", "spot"):
                g[f"{field}_change_{minutes}m"] = g[field] - g[field].shift(minutes)
        vol_prior30 = g.volume.shift(5).rolling(30, min_periods=30).mean()
        vol_recent5 = g.volume.rolling(5, min_periods=5).mean()
        g["volume_5m_vs_prev30"] = vol_recent5 / _safe_positive(vol_prior30)
        g["spread_pct_mid"] = (g.ask - g.bid) * 200 / (
            g.ask + g.bid
        ).where((g.ask > 0) & (g.bid > 0) & (g.ask >= g.bid))
        g["dte_calendar"] = (
            date.fromisoformat(str(g.expiry.iloc[0])) - g.timestamp.dt.date
        ).map(lambda td: td.days)
        if g.spot.notna().any():
            g["moneyness_pct"] = (g.strike_price.astype(float) /
                                  _safe_positive(g.spot) - 1) * 100
        else:
            g["moneyness_pct"] = np.nan
        chunks.append(g)
    return pd.concat(chunks, ignore_index=True)


def _timestamp_candidates(source: pd.DataFrame, *,
                          horizon: int, min_premium: float) -> pd.DataFrame:
    observations = exact_contract_observations(
        source, horizon=horizon, min_premium=min_premium
    )
    if observations.empty:
        return pd.DataFrame()
    f = _features_for_contract(source)
    keys = ["timestamp"]
    trailing_cols = (
        [f"premium_return_{w}m_pct" for w in WINDOWS]
        + [f"oi_change_{w}m_pct" for w in WINDOWS]
        + [f"{name}_change_{w}m"
           for name in ("gamma", "delta", "iv", "theta", "spot")
           for w in WINDOWS]
        + ["volume_5m_vs_prev30", "spread_pct_mid", "dte_calendar",
           "moneyness_pct"]
    )
    # Join on the actual signal-time bar; the forward outcome remains
    # explicitly isolated in the separate observation labels.
    f = f[keys + trailing_cols]
    observations["timestamp"] = pd.to_datetime(
        observations["signal_ist"], errors="raise"
    )
    result = observations.merge(f, on="timestamp", how="left",
                                validate="one_to_one", suffixes=("", "_feature"))
    result = result.drop(columns=["timestamp"])
    return result


def _select_non_overlapping(events: pd.DataFrame, *,
                            horizon: int,
                            blocked_times: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    """At most one selected label per overlapping time window / contract."""
    if events.empty:
        return events.copy()
    selected = []
    last_by_key = {}
    blocked_times = blocked_times or []
    for i, row in events.sort_values(["instrument_key", "signal_ist"]).iterrows():
        key = (row.instrument_key, str(row.expiry))
        t = pd.Timestamp(row.signal_ist)
        last = last_by_key.get(key)
        if last is not None and t - last < pd.Timedelta(minutes=horizon):
            continue
        if any(abs(t - b) < pd.Timedelta(minutes=horizon)
               for b in blocked_times):
            continue
        selected.append(i)
        last_by_key[key] = t
    return events.loc[selected].copy().reset_index(drop=True)


def _control_near(case: pd.Series, options: pd.DataFrame) -> pd.Series:
    # Only compare the SAME option contract, same day, matching broad premium
    # and time-of-day. No controls are made up when no true comparable exists.
    price = float(case.entry_next_open)
    times = pd.to_datetime(options.signal_ist)
    t = pd.Timestamp(case.signal_ist)
    candidate = options.loc[
        (options.instrument_key == case.instrument_key)
        & (options.expiry == case.expiry)
        & (times.dt.date == t.date())
        & (options.entry_next_open.between(price * .5, price * 2))
        & ((times - t).abs() <= pd.Timedelta(hours=2))
    ].copy()
    if candidate.empty:
        return pd.Series(dtype=object)
    candidate["sort_score"] = (
        (np.log(candidate.entry_next_open / price).abs() * 60)
        + (pd.to_datetime(candidate.signal_ist) - t).abs().dt.total_seconds() / 60
    )
    return candidate.sort_values(["sort_score", "signal_ist"]).iloc[0]


def _source_identity(frame: pd.DataFrame, *, file_number: int) -> str:
    if "symbol" in frame.columns:
        symbols = frame.symbol.dropna().astype(str).str.upper().unique()
        if len(symbols) != 1 or re.match(r"^NIFTY(?=\s|\d)", symbols[0]) is None:
            raise ValueError(f"Contract file {file_number}: symbol must identify NIFTY")
    for key in ("instrument_key", "strike_price", "option_type", "expiry"):
        if key not in frame or frame[key].isna().any() or frame[key].nunique(dropna=False) != 1:
            raise ValueError(f"Contract file {file_number}: exactly one {key} required")
    time_values = _parse_time(frame["timestamp"])
    if time_values.isna().any():
        raise ValueError(f"Contract file {file_number}: invalid timestamps")
    # Limit research to normal continuous 09:15..15:29 IST trading sessions.
    # Muhurat/special sessions need separate calendar-aware investigation.
    if ((time_values.dt.time < time(9, 15)) |
        (time_values.dt.time > time(15, 29))).any():
        raise ValueError(
            f"Contract file {file_number}: outside regular NIFTY session"
        )
    return "|".join(str(frame[k].iloc[0]) for k in (
        "instrument_key", "strike_price", "option_type", "expiry"
    ))


def _count_conditions(cohort: pd.DataFrame) -> dict:
    result = {}
    for label, (column, threshold, op) in CONDITION_SPECS.items():
        if cohort.empty or column not in cohort.columns:
            result[label] = {"observed": 0, "total_available": 0, "rate": None}
            continue
        values = pd.to_numeric(cohort[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        available = values.notna()
        matches = (values.ge(threshold) if op == ">=" else
                   values.gt(threshold)) & available
        denominator = int(available.sum())
        hits = int(matches.sum())
        result[label] = {
            "observed": hits, "total_available": denominator,
            "rate": round(hits / denominator, 4) if denominator else None,
        }
    return result


def _median_cohort_features(events: pd.DataFrame, controls: pd.DataFrame) -> dict:
    """Missing historical Greeks remain NULL, never zero."""
    metrics = {}
    for w in WINDOWS:
        for field in ("premium_return", "oi_change", "gamma_change",
                      "delta_change", "iv_change", "theta_change", "spot_change"):
            suffix = "pct" if field in ("premium_return", "oi_change") else "m"
            name = f"{field}_{w}m_{suffix}" if field in (
                "premium_return", "oi_change") else f"{field}_{w}m"
            def stats(data):
                if data.empty or name not in data:
                    return {"available": 0, "median": None}
                numbers = pd.to_numeric(data[name], errors="coerce").replace(
                    [np.inf, -np.inf], np.nan
                ).dropna()
                return {
                    "available": len(numbers),
                    "median": round(float(numbers.median()), 5)
                    if len(numbers) else None,
                }
            metrics[name] = {
                "event": stats(events),
                "matched_non_event": stats(controls),
            }
    return metrics


def investigate_events(
    contracts: Iterable[pd.DataFrame], *,
    horizon: int = 30,
    min_premium: float = 2.0,
    min_events_for_review: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Explore >=2x events + matched same-contract non-events (NO model fitting).

    Positive 2x windows are labeled retrospectively using future option
    closes; select earliest non-overlapping event per exact contract.
    Matched controls must have observed max forward close <2x, no nearby
    2x candidate, and same fixed contract/day within 2h, similar premium.
    Never claim to have verified broker source authenticity.
    """
    if not 1 <= horizon <= 120:
        raise ValueError("Horizon must be 1..120")
    if min_events_for_review < 1:
        raise ValueError("min_events_for_review must be positive")
    all_windows = []
    seen = set()
    instrument_registry: dict[str, tuple[str, str, str]] = {}
    supplied = 0
    for n, frame in enumerate(contracts, 1):
        supplied += 1
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise ValueError(f"Contract file {n} is empty/invalid")
        identity = _source_identity(frame, file_number=n)
        key = str(frame["instrument_key"].iloc[0])
        contract_signature = (
            str(frame["strike_price"].iloc[0]),
            str(frame["option_type"].iloc[0]),
            str(frame["expiry"].iloc[0]),
        )
        if key in instrument_registry and instrument_registry[key] != contract_signature:
            raise ValueError(f"Recycled instrument key across contracts: {key}")
        instrument_registry[key] = contract_signature
        if identity in seen:
            raise ValueError(f"Repeated contract input {identity}; combine its bars first")
        seen.add(identity)
        rows = _timestamp_candidates(frame, horizon=horizon,
                                     min_premium=min_premium)
        if not rows.empty:
            rows["source_contract_number"] = n
            all_windows.append(rows)
    if not supplied:
        raise ValueError("Upload at least one fixed-contract CSV")
    empty = pd.DataFrame()
    if not all_windows:
        return empty, empty, {
            "status": "NO_COMPLETE_60M_PLUS_FORWARD_WINDOWS",
            "files_supplied": supplied, "eligible_windows": 0,
            "event_threshold_multiple": 2.0,
            "observed_2x_events": 0, "observed_3x_events": 0,
            "observed_5x_events": 0,
            "observed_10x_events": 0, "matched_controls": 0,
            "repeated_patterns": {},
            "window_feature_medians": {},
            "validation": "UNVERIFIED_INPUT_NOT_PREDICTIVE",
        }
    windows = pd.concat(all_windows, ignore_index=True)
    positives = windows.loc[windows.observed_ge_2x].copy()
    # Independence is still limited across options moving on the same index day.
    events = _select_non_overlapping(positives, horizon=horizon)
    candidate_negatives = windows.loc[~windows.observed_ge_2x].copy()
    control_rows = []
    used_controls: dict[str, list[pd.Timestamp]] = {}
    for case in events.itertuples(index=False):
        same_contract_pos = positives.loc[
            (positives.instrument_key == case.instrument_key)
            & (positives.expiry == case.expiry)
        ]
        positive_times = pd.to_datetime(same_contract_pos.signal_ist).tolist()
        candidate = candidate_negatives.loc[
            (candidate_negatives.instrument_key == case.instrument_key)
            & (candidate_negatives.expiry == case.expiry)
        ].copy()
        # Censor negatives whose horizon lies in the neighborhood of ANY 2x window.
        keep = pd.to_datetime(candidate.signal_ist).map(
            lambda t: all(abs(t - pt) >= pd.Timedelta(minutes=horizon)
                          for pt in positive_times)
        )
        candidate = candidate.loc[keep].copy()
        key = str(case.instrument_key) + "|" + str(case.expiry)
        used = used_controls.get(key, [])
        candidate = candidate.loc[
            pd.to_datetime(candidate.signal_ist).map(
                lambda t: all(abs(t - u) >= pd.Timedelta(minutes=horizon)
                              for u in used)
            )
        ]
        control = _control_near(pd.Series(case._asdict()), candidate)
        if not control.empty:
            control_rows.append(control.drop(labels=["sort_score"]).to_dict())
            used_controls.setdefault(key, []).append(pd.Timestamp(control.signal_ist))
    controls = (
        pd.DataFrame(control_rows) if control_rows else windows.iloc[0:0].copy()
    )
    events["cohort"] = "OBSERVED_2X_PLUS"
    controls["cohort"] = "OBSERVED_BELOW_2X_MATCHED"
    event_counts = {
        f"observed_{m}x_events": int(events[f"observed_ge_{m}x"].sum())
        for m in (2, 3, 5, 10)
    }
    by_case = _count_conditions(events)
    by_control = _count_conditions(controls)
    comparison = {}
    for name in CONDITION_SPECS:
        a, b = by_case[name], by_control[name]
        comparison[name] = {
            "events": a, "matched_non_events": b,
            "rate_difference_descriptive": (
                round(a["rate"] - b["rate"], 4)
                if a["rate"] is not None and b["rate"] is not None else None
            ),
        }
    report = {
        "status": ("READY_FOR_PRELIMINARY_PATTERN_REVIEW_NOT_VALIDATED"
                   if (len(events) >= min_events_for_review
                       and len(controls) >= min_events_for_review
                       and len(pd.to_datetime(events.signal_ist).dt.date.unique()) >= 10
                       and events.expiry.nunique() >= 5)
                   else "INSUFFICIENT_DIVERSE_EVENTS_OR_MATCHED_CONTROLS"),
        "files_supplied": supplied,
        "eligible_windows": len(windows),
        "event_threshold_multiple": 2.0,
        **event_counts,
        "matched_controls": len(controls),
        "events_without_matched_controls": len(events) - len(controls),
        "unique_event_dates": len(pd.to_datetime(events.signal_ist).dt.date.unique())
        if not events.empty else 0,
        "unique_event_expiries": int(events.expiry.nunique()) if not events.empty else 0,
        "unique_event_contracts": int(events.instrument_key.nunique())
        if not events.empty else 0,
        "horizon_minutes": horizon,
        "min_premium": min_premium,
        "independent_source_verified": False,
        "historical_gamma_observed_events": int(
            pd.to_numeric(events.gamma_observed_t, errors="coerce").notna().sum()
        ) if not events.empty else 0,
        "repeated_patterns": comparison,
        "window_feature_medians": _median_cohort_features(events, controls),
        "validation": "UNVERIFIED_INPUT_NOT_PREDICTIVE",
        "warning": (
            "Event labels use future best MINUTE CLOSE; no execution or gamma causality "
            "proven. Features use only t and prior 60 minutes. Overlapping NIFTY "
            "strikes/expiries/dates share common shocks; events are NOT statistically "
            "independent. Controls are same-contract/time/premium matched when "
            "available; missing matches and missing Greeks are never fabricated. "
            "This is retrospective case-control exploration without trained thresholds."
        ),
    }
    return events.reset_index(drop=True), controls.reset_index(drop=True), report
