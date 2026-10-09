"""Synthetic-only tests of preregistered option fingerprints and expiry holdout.

No real market claims, no broker API, files, trained thresholds or trading.
"""
from __future__ import annotations

from datetime import date, timedelta
import json

import numpy as np
import pandas as pd
import pytest

from app.research.gamma_fingerprint_validation import (
    evaluate_frozen_fingerprints, FROZEN_SCREENS,
)


def hypothetical_windows(expiries=6) -> pd.DataFrame:
    rows = []
    for i in range(expiries):
        expiry = date(2026, 10, 13) + timedelta(days=i * 7)
        day = expiry - timedelta(days=1)
        for k, minute in enumerate(("10:15", "10:16", "10:45", "11:15")):
            # 10:16 MUST never enter the frozen decision grid.
            success = (k == 0 and i % 2 == 0) or k == 2
            # Declared outcome labels are synthetic, not observed market data.
            multiple = 5.0 if success and k == 2 else 2.5 if success else 1.0
            rows.append({
                "instrument_key": f"NSE_FO|SYNTHETIC_{i}",
                "expiry": expiry.isoformat(),
                "strike_price": 25000,
                "option_type": "PE",
                "signal_ist": f"{day.isoformat()} {minute}:00",
                "observed_ge_2x": multiple >= 2,
                "observed_ge_3x": multiple >= 3,
                "observed_ge_5x": multiple >= 5,
                "observed_ge_10x": multiple >= 10,
                "volume_5m_vs_prev30": 2.5 if k == 0 else 1.0,
                "premium_return_5m_pct": 25.0 if k == 0 else -5.0,
                "oi_change_5m_pct": 12.0 if k == 0 else -2.0,
            })
    return pd.DataFrame(rows)


def test_insufficient_independent_expiries_suppresses_holdout_metrics():
    result = evaluate_frozen_fingerprints(hypothetical_windows(5), horizon=30)
    assert result["independent_expiries"] == 5
    assert result["status"] == "INSUFFICIENT_EXPIRY_DIVERSITY_NO_HOLDOUT_CLAIM"
    assert result["train"] is None
    assert result["holdout"] is None
    assert result["prediction_validated"] is False
    assert result["trading_signal"] is None


def test_frozen_presignal_rule_has_chronological_holdout_without_optimizing():
    candidates = hypothetical_windows(6)
    result = evaluate_frozen_fingerprints(candidates, horizon=30)
    assert result["candidate_windows"] == 24
    assert result["sampled_nonoverlapping_decision_windows"] == 18
    assert result["independent_expiries"] == 6
    assert len(result["train_expiries"]) == 4
    assert len(result["holdout_expiries"]) == 2
    assert result["train_expiries"][-1] < result["holdout_expiries"][0]
    assert result["historical_source_verified"] is False
    assert result["prediction_validated"] is False
    assert result["trading_signal"] is None
    assert result["status"] == "EXPLORATORY_EXPIRY_HOLDOUT_NO_REAL_TRADING_VALIDATION"

    hypothesis = "VOLUME_GE_2_AND_PREMIUM_MOMENTUM_GE_20PCT"
    test_info = result["holdout"]["frozen_screens"][hypothesis]
    assert test_info["frozen_pre_signal_thresholds"] == {
        "volume_5m_vs_prev30": 2.0, "premium_return_5m_pct": 20.0
    }
    stats = test_info["outcomes"]["2x"]
    assert stats["windows"] == 6
    assert stats["alerts"] == 2
    assert stats["tp"] + stats["fp"] == 2
    assert stats["tp"] + stats["fp"] + stats["fn"] + stats["tn"] == 6
    assert stats["false_alarm_fraction_of_alerts"] is not None
    assert set(result["holdout"]["frozen_screens"]) == set(FROZEN_SCREENS)
    json.dumps(result, allow_nan=False)


def test_frozen_grid_has_no_label_based_selection():
    base = hypothetical_windows()
    changed = base.copy()
    # Change ONLY future outcome labels at a specific 10:15 decision.
    idx = changed.loc[changed.signal_ist.str.contains("10:15:00")].index[0]
    for multiple in (2, 3, 5, 10):
        changed.loc[idx, f"observed_ge_{multiple}x"] = True
    before = evaluate_frozen_fingerprints(base)
    after = evaluate_frozen_fingerprints(changed)
    assert before["sampled_nonoverlapping_decision_windows"] == after[
        "sampled_nonoverlapping_decision_windows"]
    assert before["train_expiries"] == after["train_expiries"]
    assert before["holdout_expiries"] == after["holdout_expiries"]
    for partition in ("train", "holdout"):
        for rule in FROZEN_SCREENS:
            a = before[partition]["frozen_screens"][rule]
            b = after[partition]["frozen_screens"][rule]
            assert a["frozen_pre_signal_thresholds"] == b["frozen_pre_signal_thresholds"]
            assert a["complete_feature_windows"] == b["complete_feature_windows"]
            for mul in (2, 3, 5, 10):
                assert a["outcomes"][f"{mul}x"]["alerts"] == b["outcomes"][f"{mul}x"]["alerts"]


def test_missing_oi_and_iv_are_never_fabricated():
    windows = hypothetical_windows()
    windows["oi_change_5m_pct"] = np.nan
    result = evaluate_frozen_fingerprints(windows)
    for partition in ("train", "holdout"):
        items = result[partition]["frozen_screens"]["VOLUME_GE_2_AND_OI_RISE_GE_10PCT"]
        assert items["complete_feature_windows"] == 0
        assert items["missing_feature_windows"] == result[partition]["windows"]
        assert items["outcomes"]["2x"]["alerts"] == 0
        assert items["outcomes"]["2x"]["precision"] is None


def test_duplicate_contract_decision_windows_fail():
    df = hypothetical_windows(6)
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate_frozen_fingerprints(pd.concat([df, df.iloc[[0]]], ignore_index=True))


def test_bad_labels_and_expiry_fail_closed():
    df = hypothetical_windows(6)
    df.loc[0, "observed_ge_10x"] = True
    with pytest.raises(ValueError, match="Inconsistent"):
        evaluate_frozen_fingerprints(df)
    other = hypothetical_windows(6).drop(columns=["observed_ge_2x"])
    with pytest.raises(ValueError, match="missing"):
        evaluate_frozen_fingerprints(other)


def test_empty_input_and_horizon_are_rejected_or_null():
    result = evaluate_frozen_fingerprints(pd.DataFrame())
    assert result["train"] is None and result["holdout"] is None
    assert result["sampled_nonoverlapping_decision_windows"] == 0
    with pytest.raises(ValueError, match="Horizon"):
        evaluate_frozen_fingerprints(pd.DataFrame(), horizon=121)


def test_holdout_output_is_order_invariant():
    a = evaluate_frozen_fingerprints(hypothetical_windows(6))
    b = evaluate_frozen_fingerprints(
        hypothetical_windows(6).sample(frac=1.0, random_state=13)
    )
    assert a == b
