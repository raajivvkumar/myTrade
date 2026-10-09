"""Predeclared Gamma-multiplier candidate screens with expiry-cycle holdout.

Research-only. A screen is not a trading signal. Labels use future best minute
CLOSE / next-minute OPEN; this is NOT executable return or Gamma attribution.

Unlike selecting observations AFTER knowing which rows were positive, this
module samples decision times on a fixed clock grid, irrespective of outcome.
The future is read ONLY for retrospective evaluation of those decisions.
"""
from __future__ import annotations

from datetime import date
import math

import numpy as np
import pandas as pd

MULTIPLIERS = (2, 3, 5, 10)
# These values are deliberately frozen, NOT fitted on the user's history.
FROZEN_SCREENS = {
    "VOLUME_GE_2_AND_PREMIUM_MOMENTUM_GE_20PCT": (
        ("volume_5m_vs_prev30", 2.0),
        ("premium_return_5m_pct", 20.0),
    ),
    "VOLUME_GE_2_AND_OI_RISE_GE_10PCT": (
        ("volume_5m_vs_prev30", 2.0),
        ("oi_change_5m_pct", 10.0),
    ),
    "PREMIUM_MOMENTUM_GE_20PCT": (
        ("premium_return_5m_pct", 20.0),
    ),
}
IDENTITY = ("instrument_key", "expiry", "strike_price", "option_type", "signal_ist")
MIN_SPLIT_EXPIRIES = 6
MIN_HOLDOUT_EXPIRIES = 2


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 6) if b else None


def _scores(rows: pd.DataFrame, alert: pd.Series, *, label: str) -> dict:
    y = rows[label].astype(bool)
    true_positive = int((alert & y).sum())
    false_positive = int((alert & ~y).sum())
    false_negative = int((~alert & y).sum())
    true_negative = int((~alert & ~y).sum())
    total = len(y)
    alerts = true_positive + false_positive
    base_rate = _ratio(int(y.sum()), total)
    precision = _ratio(true_positive, alerts)
    return {
        "windows": total,
        "alerts": alerts,
        "tp": true_positive, "fp": false_positive,
        "fn": false_negative, "tn": true_negative,
        "precision": precision,
        "recall": _ratio(true_positive, true_positive + false_negative),
        "false_alarm_fraction_of_alerts": _ratio(false_positive, alerts),
        "false_positive_rate": _ratio(false_positive, false_positive + true_negative),
        "baseline_prevalence": base_rate,
        "precision_to_prevalence_ratio": (
            round(precision / base_rate, 5)
            if precision is not None and base_rate is not None and base_rate > 0
            else None
        ),
    }


def _safe_numeric(rows: pd.DataFrame, col: str) -> pd.Series:
    if col not in rows.columns:
        return pd.Series(np.nan, index=rows.index, dtype=float)
    return pd.to_numeric(rows[col], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _evaluate_partition(rows: pd.DataFrame) -> dict:
    results = {}
    for name, conditions in FROZEN_SCREENS.items():
        alert = pd.Series(True, index=rows.index, dtype=bool)
        feature_valid = pd.Series(True, index=rows.index, dtype=bool)
        for field, min_value in conditions:
            values = _safe_numeric(rows, field)
            feature_valid = feature_valid & values.notna()
            alert = alert & values.ge(min_value).fillna(False)
        outcomes = {}
        for multiple in MULTIPLIERS:
            outcomes[f"{multiple}x"] = _scores(
                rows, alert, label=f"observed_ge_{multiple}x"
            )
        results[name] = {
            "frozen_pre_signal_thresholds": {
                field: minimum for field, minimum in conditions
            },
            "complete_feature_windows": int(feature_valid.sum()),
            "missing_feature_windows": int((~feature_valid).sum()),
            "outcomes": outcomes,
        }
    return results


def evaluate_frozen_fingerprints(
    candidate_windows: pd.DataFrame, *, horizon: int = 30,
) -> dict:
    """Time-grid holdout; no threshold search and no label-dependent sampling.

    Require at least 6 different expiries to report any numerical holdout
    metrics; test set is always last 25% of expiries, at least 2 groups.
    No probabilities should be marketed as independently validated.
    """
    if not isinstance(horizon, int) or not 1 <= horizon <= 120:
        raise ValueError("Horizon must be between 1 and 120 minutes")
    report = {
        "status": "INSUFFICIENT_EXPIRY_DIVERSITY_NO_HOLDOUT_CLAIM",
        "screening_policy": "FROZEN_PRESIGNAL_HYPOTHESES_V1_NO_TUNING",
        "validation_policy": "CHRONOLOGICAL_EXPIRY_HOLDOUT_V1",
        "sampling_policy": "09:15_IST_CLOCK_GRID_PER_CONTRACT_DAY",
        "horizon_minutes": horizon,
        "candidate_windows": 0,
        "sampled_nonoverlapping_decision_windows": 0,
        "historical_source_verified": False,
        "prediction_validated": False,
        "trading_signal": None,
        "independent_expiries": 0,
        "train_expiries": [],
        "holdout_expiries": [],
        "train": None,
        "holdout": None,
        "warnings": [
            "Retrospective best forward MINUTE CLOSE is not executable P&L.",
            "Signals use no future columns, but observed labels do.",
            "All contracts in the same expiry can share the same market shock.",
            "No source provenance verification, Greeks causality, slippage or costs.",
            "The thresholds were not calibrated or proven profitable.",
        ],
    }
    if candidate_windows is None or candidate_windows.empty:
        return report
    missing = (set(IDENTITY) | {
        f"observed_ge_{m}x" for m in MULTIPLIERS
    }) - set(candidate_windows.columns)
    if missing:
        raise ValueError("Candidate windows missing identity/labels: " + ", ".join(sorted(missing)))
    if candidate_windows.duplicated(list(IDENTITY)).any():
        raise ValueError("Duplicate fixed-contract decision window")
    rows = candidate_windows.copy()
    try:
        rows["_signal_dt"] = pd.to_datetime(rows.signal_ist, errors="raise")
        if rows["_signal_dt"].isna().any() or getattr(rows["_signal_dt"].dt, "tz", None) is not None:
            # The upstream pipeline normalizes signal_ist to IST local naive.
            raise ValueError("Expected IST local naive signal timestamps")
        rows["_expiry_day"] = pd.to_datetime(rows.expiry, format="%Y-%m-%d", errors="raise")
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Invalid timestamps or expiry identity") from exc
    if (rows["_signal_dt"].dt.date > rows["_expiry_day"].dt.date).any():
        raise ValueError("Signal after contract expiry")
    for multiple in MULTIPLIERS:
        label = rows[f"observed_ge_{multiple}x"]
        if label.isna().any() or not label.isin((True, False)).all():
            raise ValueError("Event labels must be non-missing booleans")
    # Consistency: >10x implies >=5x, >=3x and >=2x.
    for lower, upper in zip(MULTIPLIERS, MULTIPLIERS[1:]):
        if (rows[f"observed_ge_{upper}x"] & ~rows[f"observed_ge_{lower}x"]).any():
            raise ValueError("Inconsistent nested premium multiplier labels")
    report["candidate_windows"] = len(rows)
    # Predeclared observation grid does not inspect future outcomes.
    session_minute = (
        (rows["_signal_dt"].dt.hour - 9) * 60
        + rows["_signal_dt"].dt.minute - 15
    )
    grid = rows.loc[
        session_minute.ge(0)
        & session_minute.mod(horizon).eq(0)
        & rows["_signal_dt"].dt.second.eq(0)
        & rows["_signal_dt"].dt.hour.le(15)
    ].copy()
    grid = grid.sort_values(["_expiry_day", "instrument_key", "_signal_dt"])
    report["sampled_nonoverlapping_decision_windows"] = len(grid)
    expiry_values = sorted(pd.Timestamp(x).date().isoformat() for x in grid["_expiry_day"].unique())
    report["independent_expiries"] = len(expiry_values)
    if len(expiry_values) < MIN_SPLIT_EXPIRIES:
        return report

    holdout_n = max(MIN_HOLDOUT_EXPIRIES, math.ceil(len(expiry_values) * 0.25))
    holdout = expiry_values[-holdout_n:]
    train = expiry_values[:-holdout_n]
    report["train_expiries"] = train
    report["holdout_expiries"] = holdout
    training = grid.loc[grid.expiry.isin(train)].copy()
    testing = grid.loc[grid.expiry.isin(holdout)].copy()
    if training.empty or testing.empty:
        return report
    report["train"] = {
        "windows": len(training), "frozen_screens": _evaluate_partition(training)
    }
    report["holdout"] = {
        "windows": len(testing), "frozen_screens": _evaluate_partition(testing)
    }
    report["status"] = "EXPLORATORY_EXPIRY_HOLDOUT_NO_REAL_TRADING_VALIDATION"
    return report
