"""Exploratory, no-lookahead gamma precursor research.

The NIFTY index precursor is NOT an option gamma prediction. A separate
exact fixed-strike CE/PE contract must be supplied before any 3x/5x/10x
option PREMIUM outcome can be studied. No broker calls or trading orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

INDEX_KEY = "NSE_INDEX|Nifty 50"
REQUIRED_OHLC = ("timestamp", "open", "high", "low", "close")
OPTION_FIELDS = ("instrument_key", "symbol", "strike_price", "option_type",
                 "expiry", "lot_size", "volume", "oi")
MARKET_CLOSE_BAR = time(15, 29)


@dataclass(frozen=True)
class ResearchRules:
    """Pre-registered illustrative thresholds, NOT optimized/proven."""
    move_5m_bps: float = 12.0
    range_ratio: float = 1.35
    abs_return_ratio: float = 1.3
    consistent_bars: int = 4
    cooldown_minutes: int = 15
    horizon_minutes: int = 30
    min_entry_premium: float = 2.0
    option_volume_ratio: float = 2.0

    def validate(self):
        if (self.move_5m_bps <= 0 or self.range_ratio <= 0
                or self.abs_return_ratio <= 0 or self.consistent_bars not in (3, 4, 5)
                or self.cooldown_minutes < 1 or self.horizon_minutes < 1
                or self.horizon_minutes > 120 or self.min_entry_premium <= 0
                or self.option_volume_ratio <= 0):
            raise ValueError("Invalid gamma research rule configuration")


def _local_times(values: pd.Series) -> pd.Series:
    times = pd.to_datetime(values, errors="coerce", utc=True)
    # Broker archive uses timezone-naive IST. pandas utc=True would interpret
    # naive timestamps as UTC, so branch explicitly on original tz awareness.
    first = values.dropna().iloc[0] if len(values.dropna()) else None
    if first is None:
        raise ValueError("Missing timestamps")
    aware = pd.Timestamp(first).tzinfo is not None
    if aware:
        return times.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return pd.to_datetime(values, errors="coerce")


def _ohlc(frame: pd.DataFrame, *, check_identity: bool = False) -> pd.DataFrame:
    if not set(REQUIRED_OHLC).issubset(frame.columns):
        raise ValueError("Missing timestamp or OHLC")
    if frame.empty:
        raise ValueError("No candles to analyze")
    f = frame.copy()
    f["timestamp"] = _local_times(f["timestamp"])
    for col in ("open", "high", "low", "close"):
        f[col] = pd.to_numeric(f[col], errors="coerce")
    if f[list(REQUIRED_OHLC)].isna().any().any():
        raise ValueError("Null/non-numeric OHLC or timestamp")
    if (f[list(REQUIRED_OHLC[1:])] <= 0).any().any():
        raise ValueError("Nonpositive OHLC")
    if ((f.low > f[["open", "close"]].min(axis=1)) |
            (f.high < f[["open", "close"]].max(axis=1)) |
            (f.high < f.low)).any():
        raise ValueError("Invalid OHLC bounds")
    if f.timestamp.duplicated().any():
        raise ValueError("Duplicate timestamp: cannot silently choose a price")
    if "instrument_key" in f.columns and not check_identity:
        if set(f.instrument_key.dropna().unique()) != {INDEX_KEY}:
            raise ValueError("Index input must be NIFTY 50, not option contracts")
    f = f.sort_values("timestamp").reset_index(drop=True)
    if (f.timestamp.diff().dropna() < pd.Timedelta(0)).any():
        raise ValueError("Decreasing timestamps")
    f["session"] = f.timestamp.dt.date
    return f


def index_precursors(index_bars: pd.DataFrame,
                     rules: ResearchRules = ResearchRules()) -> pd.DataFrame:
    """Features available after each completed NIFTY minute t, never after t."""
    rules.validate()
    f = _ohlc(index_bars)
    pieces = []
    for _, group in f.groupby("session", sort=True):
        g = group.copy()
        ret1 = g.close.pct_change() * 10000
        ret5 = (g.close / g.close.shift(5) - 1.0) * 10000
        span = (g.high - g.low) / g.close * 10000
        prior_span = span.shift(5).rolling(30, min_periods=30).median()
        prior_abs_return = ret1.abs().shift(5).rolling(30, min_periods=30).mean()
        recent_abs_return = ret1.abs().rolling(5, min_periods=5).mean()
        up = ret1.gt(0).astype(int).rolling(5, min_periods=5).sum()
        down = ret1.lt(0).astype(int).rolling(5, min_periods=5).sum()
        bars_aligned = pd.Series(np.where(ret5 > 0, up, down), index=g.index)
        # A discontinuity in a trailing feature window invalidates that row:
        # otherwise overnight gaps or missing minutes make false "explosions".
        continuous = (g.timestamp.diff().dt.total_seconds()
                      .div(60).fillna(1).eq(1))
        continuous_lookback = continuous.rolling(36, min_periods=36).sum().eq(36)
        g["move_5m_bps"] = ret5.round(3)
        g["range_ratio"] = (
            span.rolling(5, min_periods=5).mean() / prior_span.replace(0, np.nan)
        ).round(3)
        g["abs_return_ratio"] = (
            recent_abs_return / prior_abs_return.replace(0, np.nan)
        ).round(3)
        g["directional_bars_5m"] = bars_aligned.fillna(0).astype(int)
        g["direction"] = np.where(ret5 > 0, "UP", "DOWN")
        g["eligible"] = continuous_lookback & g[[
            "move_5m_bps", "range_ratio", "abs_return_ratio"
        ]].notna().all(axis=1)
        g["condition_move"] = g.move_5m_bps.abs().ge(rules.move_5m_bps)
        g["condition_range"] = g.range_ratio.ge(rules.range_ratio)
        g["condition_volatility"] = g.abs_return_ratio.ge(rules.abs_return_ratio)
        g["condition_direction"] = g.directional_bars_5m.ge(rules.consistent_bars)
        g["conditions_met"] = (
            g[["condition_move", "condition_range", "condition_volatility",
               "condition_direction"]].sum(axis=1).astype(int)
        )
        g["raw_precursor"] = g.eligible & g.conditions_met.eq(4)
        pieces.append(g)
    result = pd.concat(pieces, ignore_index=True)
    result["precursor"] = False
    # Deduplicate alerts per trading day; cooldown is not a predictive feature.
    for _, session in result.groupby("session", sort=True):
        last = None
        for i in session.index:
            if not bool(result.at[i, "raw_precursor"]):
                continue
            current = result.at[i, "timestamp"]
            if last is None or current - last >= pd.Timedelta(minutes=rules.cooldown_minutes):
                result.at[i, "precursor"] = True
                last = current
    result["available_after_ist"] = result.timestamp + pd.Timedelta(minutes=1)
    return result


def _exact_option(option_bars: pd.DataFrame) -> pd.DataFrame:
    f = _ohlc(option_bars, check_identity=True)
    if not set(OPTION_FIELDS).issubset(f.columns):
        raise ValueError("Exact option source requires key, symbol, strike, CE/PE, expiry, lot and OI/volume")
    for name in ("instrument_key", "symbol", "strike_price", "option_type", "expiry", "lot_size"):
        if f[name].isna().any() or f[name].nunique(dropna=False) != 1:
            raise ValueError(f"Mixed or missing contract identity: {name}")
    key = str(f.instrument_key.iloc[0])
    if not key.startswith("NSE_FO|") or str(f.symbol.iloc[0]).upper().startswith("NIFTY") is False:
        raise ValueError("Only actual NIFTY F&O contracts supported")
    side = f.option_type.iloc[0]
    if side not in ("CE", "PE"):
        raise ValueError("Only CE or PE allowed")
    expiry = pd.Timestamp(f.expiry.iloc[0]).date()
    if (f.timestamp.dt.date > expiry).any():
        raise ValueError("Contract has candles after expiry")
    strike = pd.to_numeric(f.strike_price, errors="coerce")
    lots = pd.to_numeric(f.lot_size, errors="coerce")
    if strike.isna().any() or (strike <= 0).any() or lots.isna().any() or (lots < 1).any():
        raise ValueError("Invalid strike or lot size")
    for name in ("volume", "oi"):
        f[name] = pd.to_numeric(f[name], errors="coerce")
    if f[["volume", "oi"]].isna().any().any() or (f[["volume", "oi"]] < 0).any().any():
        raise ValueError("Missing/negative option volume or OI")
    if "source_quality" in f and not f.source_quality.eq(
            "UPSTOX_UNVALIDATED_NEEDS_NSE_CHECK").all():
        raise ValueError("Unexpected option data provenance")
    f["expiry_date"] = expiry
    pieces = []
    for _, g in f.groupby("session", sort=True):
        g = g.copy()
        g["volume_ratio"] = (
            g.volume.rolling(5, min_periods=5).mean()
            / g.volume.shift(5).rolling(30, min_periods=30).mean().replace(0, np.nan)
        )
        g["oi_change_5m_pct"] = (
            (g.oi / g.oi.shift(5).replace(0, np.nan)) - 1
        ) * 100.0
        pieces.append(g)
    return pd.concat(pieces, ignore_index=True)


def evaluate_contract(features: pd.DataFrame, option_bars: pd.DataFrame,
                      rules: ResearchRules = ResearchRules()) -> pd.DataFrame:
    """Label closed-bar option upside using next-minute OPEN as entry benchmark.

    WARNING: future max *minute closes* are an observational upper bound, not
    executable P&L. Uninterrupted entire horizon required, never cross expiry
    or session. One exact contract per run; no ATM rollover.
    """
    rules.validate()
    f = _exact_option(option_bars)
    side = f.option_type.iloc[0]
    wanted = "UP" if side == "CE" else "DOWN"
    option_map = f.set_index("timestamp", verify_integrity=True)
    labels = []
    for row in features.loc[features.eligible & features.direction.eq(wanted)].itertuples():
        t = row.timestamp
        start = t + pd.Timedelta(minutes=1)
        finish = t + pd.Timedelta(minutes=rules.horizon_minutes)
        if finish.date() != t.date() or finish.time() > MARKET_CLOSE_BAR:
            continue
        interval = pd.date_range(start, finish, freq="min")
        if not interval.isin(option_map.index).all():
            continue  # no invented forward bars and no hindsight selection
        option_forward = option_map.loc[interval]
        entry = float(option_forward.open.iloc[0])
        if not np.isfinite(entry) or entry < rules.min_entry_premium:
            continue
        current = option_map.loc[t] if t in option_map.index else None
        # Require actual option bar at the completed index signal minute t.
        if current is None:
            continue
        high_close = float(option_forward.close.max())
        worst_close = float(option_forward.close.min())
        volume_ratio = float(current.volume_ratio) if pd.notna(current.volume_ratio) else None
        oi_change = float(current.oi_change_5m_pct) if pd.notna(current.oi_change_5m_pct) else None
        joint = bool(row.precursor and volume_ratio is not None
                     and volume_ratio >= rules.option_volume_ratio)
        multiples = high_close / entry
        labels.append({
            "signal_at_ist": str(t),
            "earliest_entry_ist": str(start),
            "expiry": str(current.expiry),
            "instrument_key": str(current.instrument_key),
            "option_type": side,
            "strike_price": float(current.strike_price),
            "lot_size": int(current.lot_size),
            "days_to_expiry_calendar": (current.expiry_date - t.date()).days,
            "index_direction": wanted,
            "index_move_5m_bps": float(row.move_5m_bps),
            "index_range_ratio": float(row.range_ratio),
            "index_abs_return_ratio": float(row.abs_return_ratio),
            "index_precursor": bool(row.precursor),
            "option_volume_ratio_5m": volume_ratio,
            "option_oi_change_5m_pct": oi_change,
            "joint_watch": joint,
            "entry_next_open": entry,
            "best_future_minute_close": high_close,
            "worst_future_minute_close": worst_close,
            "observational_max_close_multiple": round(multiples, 4),
            "observed_ge_3x": multiples >= 3.0,
            "observed_ge_5x": multiples >= 5.0,
            "observed_ge_10x": multiples >= 10.0,
            "forward_complete_minutes": rules.horizon_minutes,
            "result_type": "OBSERVATIONAL_UPPER_BOUND_NOT_EXECUTABLE_PNL",
            "quality": "UNVALIDATED_NEEDS_INDEPENDENT_NSE_CHECK",
        })
    return pd.DataFrame(labels)


def summarize(fingerprints: pd.DataFrame, contract_outcomes: pd.DataFrame | None,
              horizon_minutes: int) -> dict:
    count = int(fingerprints.precursor.sum())
    result = {
        "research_status": "EXPLORATORY_NOT_A_VALIDATED_TRADING_EDGE",
        "index_rows": len(fingerprints),
        "index_precursor_events": count,
        "horizon_minutes": horizon_minutes,
        "index_source": "NIFTY_1MIN_UNVALIDATED",
        "outcomes_status": "NOT_AVAILABLE_NO_EXACT_OPTION_CONTRACT",
        "no_lookahead": "Index conditions use bars ending at t+1; entry candidate is next option bar open",
        "warning": (
            "Only one/two index sessions cannot validate option Gamma Multiplier. "
            "All future option outcomes require exact-contract data and separate NSE verification."
        ),
    }
    if contract_outcomes is not None:
        result["outcomes_status"] = "EXPLORATORY_OPTION_HINDSIGHT_LABELS"
        result["contract_rows_evaluated"] = len(contract_outcomes)
        selected = contract_outcomes.loc[contract_outcomes.joint_watch] if not contract_outcomes.empty else contract_outcomes
        result["joint_watch_events"] = len(selected)
        for multiple in (3, 5, 10):
            key = f"observed_ge_{multiple}x"
            successes = int(selected[key].sum()) if not selected.empty else 0
            result[f"joint_watch_{multiple}x_observations"] = successes
            result[f"joint_watch_{multiple}x_rate"] = (
                round(successes / len(selected), 4) if len(selected) else None
            )
            result[f"all_eligible_{multiple}x_rate"] = (
                round(float(contract_outcomes[key].mean()), 4)
                if not contract_outcomes.empty else None
            )
        result["statistical_warning"] = (
            "Overlapping windows, sample selection, optional data gaps and price "
            "execution prevent significance or tradable profit claims. "
            "Future max CLOSE is not a fill; include slippage, spread, fees and holdout expiries."
        )
    return result
