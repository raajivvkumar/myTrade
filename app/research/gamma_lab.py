"""In-memory Gamma Investigation Lab: cross-sectional chain and exact-contract outcomes.

Not a gamma forecast, trading signal, or proof of causal gamma effect.
"""
from __future__ import annotations

import math
from datetime import date, time

import numpy as np
import pandas as pd

INDEX_KEY = "NSE_INDEX|Nifty 50"
DAY_END = time(15, 29)
REQUIRED_OPTION = {
    "timestamp", "open", "high", "low", "close",
    "volume", "oi", "instrument_key", "strike_price", "option_type", "expiry",
}


def _number(x):
    try:
        value = float(x)
        return value if math.isfinite(value) else np.nan
    except (TypeError, ValueError):
        return np.nan


def normalize_chain(payload: dict) -> pd.DataFrame:
    """One row per exact option key, side, strike, expiry. Missing is never zero."""
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("Option chain response must have success status")
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("Option chain expected a data list")
    out = []
    for item in data:
        if not isinstance(item, dict) or item.get("underlying_key") != INDEX_KEY:
            raise ValueError("Unexpected or mixed underlying in option chain")
        expiry = str(item.get("expiry") or "")
        try:
            expiry = date.fromisoformat(expiry).isoformat()
        except ValueError as exc:
            raise ValueError("Unknown option expiry") from exc
        spot, strike = _number(item.get("underlying_spot_price")), _number(item.get("strike_price"))
        if not np.isfinite(spot) or spot <= 0 or not np.isfinite(strike) or strike <= 0:
            raise ValueError("Invalid spot/strike in live option chain")
        for side, field in (("CE", "call_options"), ("PE", "put_options")):
            option = item.get(field)
            if not isinstance(option, dict):
                continue
            instrument_key = str(option.get("instrument_key") or "")
            if not instrument_key.startswith("NSE_FO|"):
                raise ValueError("Invalid fixed option instrument key")
            market = option.get("market_data") or {}
            greeks = option.get("option_greeks") or {}
            if not isinstance(market, dict) or not isinstance(greeks, dict):
                raise ValueError("Invalid Upstox option data payload")
            row = {
                "instrument_key": instrument_key,
                "expiry": expiry, "strike_price": strike, "side": side,
                "spot": spot, "pcr": _number(item.get("pcr")),
            }
            for dest, key in (
                ("ltp", "ltp"), ("volume", "volume"), ("oi", "oi"),
                ("prev_oi", "prev_oi"), ("bid", "bid_price"),
                ("ask", "ask_price"), ("bid_qty", "bid_qty"), ("ask_qty", "ask_qty"),
            ):
                row[dest] = _number(market.get(key))
            for key in ("delta", "gamma", "theta", "vega", "iv", "pop"):
                row[key] = _number(greeks.get(key))
            out.append(row)
    frame = pd.DataFrame(out)
    if frame.empty:
        return frame
    identity = ["instrument_key", "expiry", "side"]
    if frame.duplicated(identity).any():
        raise ValueError("Duplicate contract key in option chain")
    if frame.groupby("instrument_key")[["expiry", "side", "strike_price"]].nunique().gt(1).any().any():
        raise ValueError("Recycled/mixed option identity")
    frame["dte_calendar"] = (pd.to_datetime(frame["expiry"]).dt.date -
                             pd.Timestamp.now(tz="Asia/Kolkata").date()).map(lambda x: x.days)
    frame["moneyness_pct"] = (frame.strike_price / frame.spot - 1) * 100
    # Gamma = d(delta)/d(underlying point). Linear estimate of change
    # in delta for a 1% underlying move, NOT expected premium return.
    frame["delta_shift_for_1pct_spot"] = frame.gamma.abs() * frame.spot * 0.01
    frame["mid"] = (frame.bid + frame.ask) / 2
    valid_bidask = (frame.bid > 0) & (frame.ask >= frame.bid) & (frame.mid > 0)
    frame["spread_pct_mid"] = np.where(
        valid_bidask, (frame.ask - frame.bid) * 100 / frame.mid, np.nan
    )
    frame["quote_valid"] = valid_bidask
    frame["liquid_screen"] = (
        frame.quote_valid
        & frame.spread_pct_mid.le(10.0)
        & frame.volume.gt(0)
        & frame.oi.gt(0)
        & frame.delta.abs().between(0.01, 0.75)
        & frame.gamma.gt(0)
        & frame.ltp.gt(0)
    )
    frame["relative_gamma_percentile"] = frame.groupby(
        ["expiry", "side"]
    )["delta_shift_for_1pct_spot"].rank(pct=True, method="average")
    frame["investigate_only"] = (
        frame.liquid_screen & frame.relative_gamma_percentile.ge(0.75)
    )
    return frame.sort_values(
        ["investigate_only", "delta_shift_for_1pct_spot"],
        ascending=[False, False],
    ).reset_index(drop=True)


def compare_chain_snapshots(earlier: pd.DataFrame, later: pd.DataFrame) -> pd.DataFrame:
    """No merge between different expiries/strikes; no OI sign interpretation."""
    if earlier.empty or later.empty:
        return pd.DataFrame()
    identity = ["instrument_key", "expiry", "strike_price", "side"]
    for frame in (earlier, later):
        if not set(identity).issubset(frame.columns) or frame.duplicated(identity).any():
            raise ValueError("Missing or ambiguous option identity")
    values = ["ltp", "oi", "volume", "iv", "gamma", "delta", "spread_pct_mid"]
    old = earlier.loc[:, identity + values]
    now = later.loc[:, identity + values]
    compared = now.merge(old, on=identity, suffixes=("_now", "_prev"), how="inner")
    for name in values:
        compared[f"{name}_change"] = compared[f"{name}_now"] - compared[f"{name}_prev"]
    return compared


def exact_contract_observations(option: pd.DataFrame, *,
                                horizon: int = 30,
                                min_premium: float = 2.0) -> pd.DataFrame:
    """Retrospective 2x/3x/5x/10x labels from one fixed option contract.

    Fingerprints at minute t use t and past ONLY. Outcome uses next minute OPEN
    and maximal NEXT-horizon minute CLOSE. Never treat outcome as a fill.
    """
    if not isinstance(horizon, int) or not 1 <= horizon <= 120:
        raise ValueError("Horizon must be between 1 and 120 minutes")
    if min_premium <= 0:
        raise ValueError("Minimum premium must be positive")
    missing = REQUIRED_OPTION - set(option.columns)
    if missing:
        raise ValueError("Missing exact-option columns: " + ", ".join(sorted(missing)))
    if option.empty:
        raise ValueError("Option dataset empty")
    f = option.copy()
    parsed = pd.to_datetime(f.timestamp, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        parsed = parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    elif not pd.api.types.is_datetime64_any_dtype(parsed):
        raise ValueError("Timestamps must have consistent timezone")
    f["timestamp"] = parsed
    if f.timestamp.isna().any() or f.timestamp.duplicated().any():
        raise ValueError("Missing/duplicate option candle timestamps")
    for key in ("instrument_key", "strike_price", "option_type", "expiry"):
        if f[key].isna().any() or f[key].nunique(dropna=False) != 1:
            raise ValueError(f"Mixed or missing contract identity: {key}")
    instrument = str(f.instrument_key.iloc[0])
    if not instrument.startswith("NSE_FO|") or f.option_type.iloc[0] not in ("CE", "PE"):
        raise ValueError("Only one actual NSE NIFTY CE/PE contract supported")
    expiry = date.fromisoformat(str(f.expiry.iloc[0]))
    if (f.timestamp.dt.date > expiry).any():
        raise ValueError("Data extends after contract expiry")
    for key in ("open", "high", "low", "close", "volume", "oi", "strike_price"):
        f[key] = pd.to_numeric(f[key], errors="coerce")
    if f[["open", "high", "low", "close", "volume", "oi", "strike_price"]].isna().any().any():
        raise ValueError("Null/non-numeric option fields")
    numeric = f[["open", "high", "low", "close", "volume", "oi", "strike_price"]].to_numpy(dtype=float)
    if not np.isfinite(numeric).all():
        raise ValueError("Nonfinite data")
    if (f[["open", "high", "low", "close", "strike_price"]] <= 0).any().any():
        raise ValueError("Zero/negative option price or strike")
    if (f[["volume", "oi"]] < 0).any().any():
        raise ValueError("Negative volume or OI")
    if ((f.low > f[["open", "close"]].min(axis=1)) |
        (f.high < f[["open", "close"]].max(axis=1)) | (f.high < f.low)).any():
        raise ValueError("Inconsistent option OHLC")
    f = f.sort_values("timestamp").reset_index(drop=True)
    f["session"] = f.timestamp.dt.date
    # Greeks are optional; prohibit inventing historical Greeks when missing.
    greek_columns = ("gamma", "delta", "iv", "theta")
    for name in greek_columns:
        if name not in f.columns:
            f[name] = np.nan
        else:
            f[name] = pd.to_numeric(f[name], errors="coerce")
    results = []
    for session_date, g in f.groupby("session", sort=True):
        g = g.reset_index(drop=True)
        # 60 trailing minutes and full forward horizon must be contiguous.
        continuous = g.timestamp.diff().eq(pd.Timedelta(minutes=1))
        vol_base = g.volume.shift(5).rolling(30, min_periods=30).mean()
        current_vol = g.volume.rolling(5, min_periods=5).mean()
        for i in range(60, len(g) - horizon):
            if g.timestamp.iat[i].time() > DAY_END:
                continue
            if not continuous.iloc[i - 59:i + horizon + 1].all():
                continue
            if g.timestamp.iat[i + horizon].date() != session_date:
                continue
            first = g.iloc[i + 1]
            price = float(first.open)
            if price < min_premium:
                continue
            future = g.iloc[i + 1:i + horizon + 1]
            best = float(future.close.max())
            gamma_now = _number(g.gamma.iat[i])
            gamma_prev = _number(g.gamma.iat[i - 5])
            volume_ratio = (_number(current_vol.iat[i] / vol_base.iat[i])
                            if vol_base.iat[i] > 0 else np.nan)
            oi_now = _number(g.oi.iat[i])
            oi_5m_prior = _number(g.oi.iat[i - 5])
            oi_pct = (100 * (oi_now / oi_5m_prior - 1)
                      if oi_5m_prior > 0 else np.nan)
            multiple = best / price
            results.append({
                "instrument_key": instrument,
                "expiry": expiry.isoformat(),
                "strike_price": float(g.strike_price.iat[i]),
                "option_type": str(g.option_type.iat[i]),
                "signal_ist": str(g.timestamp.iat[i]),
                "entry_next_minute_ist": str(first.timestamp),
                "entry_next_open": price,
                "max_forward_minute_close": best,
                "observed_close_multiple": round(multiple, 4),
                "observed_ge_2x": bool(multiple >= 2),
                "observed_ge_3x": bool(multiple >= 3),
                "observed_ge_5x": bool(multiple >= 5),
                "observed_ge_10x": bool(multiple >= 10),
                "volume_5m_vs_prev30": volume_ratio,
                "oi_change_5m_pct": oi_pct,
                "gamma_observed_t": gamma_now,
                "gamma_change_5m": (gamma_now - gamma_prev
                                    if np.isfinite(gamma_now) and np.isfinite(gamma_prev)
                                    else np.nan),
                "delta_observed_t": _number(g.delta.iat[i]),
                "iv_observed_t": _number(g.iv.iat[i]),
                "theta_observed_t": _number(g.theta.iat[i]),
                "premium_move_prior5_pct": 100 * (
                    g.close.iat[i] / g.close.iat[i - 5] - 1
                ),
                "watch_volume_2x": bool(np.isfinite(volume_ratio) and volume_ratio >= 2),
                "result_type": "RETROSPECTIVE_MAX_FUTURE_CLOSE_NOT_EXECUTABLE_PNL",
            })
    return pd.DataFrame(results)


def compare_fingerprint_cohorts(observations: pd.DataFrame) -> dict:
    """Descriptive only; overlapping forward windows are NOT independent trials."""
    if observations.empty:
        return {
            "eligible_windows": 0,
            "volume_2x_windows": 0,
            "observed_2x": 0, "observed_3x": 0,
            "observed_5x": 0, "observed_10x": 0,
            "status": "INSUFFICIENT_CONTIGUOUS_DATA",
        }
    selected = observations.loc[observations.watch_volume_2x]
    other = observations.loc[~observations.watch_volume_2x]
    answer = {
        "eligible_windows": len(observations),
        "volume_2x_windows": len(selected),
        "unflagged_windows": len(other),
        "status": "RETROSPECTIVE_DESCRIPTIVE_NOT_INDEPENDENT_NOT_PREDICTIVE",
        "caveat": (
            "Overlapping horizons; premium max future minute-close does not "
            "represent a realizable fill or Gamma causal attribution."
        ),
    }
    for mul in (2, 3, 5, 10):
        column = f"observed_ge_{mul}x"
        answer[f"observed_{mul}x"] = int(observations[column].sum())
        answer[f"flagged_{mul}x_rate"] = (
            float(selected[column].mean()) if len(selected) else None
        )
        answer[f"unflagged_{mul}x_rate"] = (
            float(other[column].mean()) if len(other) else None
        )
    answer["gamma_history_present"] = bool(
        observations.gamma_observed_t.notna().any()
    )
    return answer
