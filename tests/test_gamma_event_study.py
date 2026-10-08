"""Offline synthetic tests for event-vs-comparable-non-event hypotheses.

These tests deliberately do NOT imply the 3x or 5x patterns exist in real data.
"""
import pandas as pd
import numpy as np
import pytest

from app.research.gamma_event_study import (
    investigate_events, _features_for_contract, _count_conditions,
)


def contract(*, day="2026-10-08", expiry="2026-10-13",
             key="NSE_FO|TEST_1", side="CE", peak=135):
    t = pd.date_range(f"{day} 09:15:00", periods=230, freq="min")
    data = pd.DataFrame({
        "timestamp": t,
        "open": 4.0, "high": 5.0, "low": 3.0, "close": 4.0,
        "volume": 20.0, "oi": 1000.0,
        "instrument_key": key, "strike_price": 25000.0,
        "option_type": side, "expiry": expiry,
        "symbol": f"NIFTY {expiry} 25000 {side}",
    })
    if peak is not None:
        data.loc[peak, "close"] = 22.0
        data.loc[peak, "high"] = 23.0
    return data


def test_one_realistic_synthetic_event_and_one_matched_control():
    event, ctrl, report = investigate_events([contract()], horizon=30)
    assert report["observed_3x_events"] == 1
    assert report["observed_5x_events"] == 1
    assert report["observed_10x_events"] == 0
    assert report["eligible_windows"] > 100
    assert report["unique_event_dates"] == 1
    assert report["unique_event_expiries"] == 1
    assert report["status"] == "INSUFFICIENT_DIVERSE_EVENTS_OR_MATCHED_CONTROLS"
    assert len(event) == 1
    assert event.observed_close_multiple.iloc[0] == 5.5
    assert len(ctrl) == 1
    assert ctrl.observed_close_multiple.iloc[0] < 3
    assert ctrl.instrument_key.iloc[0] == event.instrument_key.iloc[0]
    assert ctrl.expiry.iloc[0] == event.expiry.iloc[0]
    assert "RETROSPECTIVE" in report["warning"] or "Event labels" in report["warning"]


def test_no_event_reports_zero_without_fabrication():
    ev, ctrl, r = investigate_events([contract(peak=None)])
    assert ev.empty
    assert ctrl.empty
    assert r["observed_3x_events"] == 0
    assert r["matched_controls"] == 0
    assert r["status"] == "INSUFFICIENT_DIVERSE_EVENTS_OR_MATCHED_CONTROLS"


def test_missing_actual_greeks_do_not_count_as_zero_negative_signal():
    ev, ctrl, report = investigate_events([contract()])
    gamma = report["repeated_patterns"]["gamma_rising"]
    assert gamma["events"]["total_available"] == 0
    assert gamma["events"]["rate"] is None
    assert report["historical_gamma_observed_events"] == 0


def test_actual_past_gamma_iv_oi_predecessors_recorded_without_lookahead():
    df = contract()
    df["gamma"] = np.linspace(0.002, 0.003, len(df))
    df["iv"] = np.linspace(15.0, 16.0, len(df))
    df["delta"] = np.linspace(0.15, 0.3, len(df))
    df["theta"] = -2.0
    df["oi"] = np.linspace(1000, 1400, len(df))
    case, ctrl, report = investigate_events([df])
    row = case.iloc[0]
    assert row.gamma_change_5m > 0
    assert row.iv_change_5m > 0
    assert row.oi_change_5m_pct > 0
    assert row.gamma_change_30m > row.gamma_change_5m
    assert report["historical_gamma_observed_events"] == 1
    assert report["repeated_patterns"]["iv_rising"]["events"]["total_available"] == 1


def test_bars_after_current_timestamp_do_not_modify_pre_event_features():
    original = contract()
    changed = original.copy()
    changed.loc[170:, ["close", "open", "high", "low"]] = [100, 100, 101, 99]
    features_a = _features_for_contract(original)
    features_b = _features_for_contract(changed)
    cols = ["premium_return_5m_pct", "oi_change_15m_pct",
            "volume_5m_vs_prev30", "gamma_change_5m"]
    pd.testing.assert_frame_equal(features_a.loc[:160, cols],
                                  features_b.loc[:160, cols])


def test_mixed_strikes_and_recycled_contracts_are_rejected():
    bad = contract()
    bad.loc[100, "strike_price"] = 25100
    with pytest.raises(ValueError, match="exactly one"):
        investigate_events([bad])
    a = contract()
    with pytest.raises(ValueError, match="Repeated contract"):
        investigate_events([a, a.copy()])


def test_non_nifty_symbol_rejected():
    data = contract()
    data["symbol"] = "BANKNIFTY 25000 CE"
    with pytest.raises(ValueError, match="NIFTY"):
        investigate_events([data])


def test_niftybank_symbol_with_nifty_prefix_is_not_misclassified():
    data = contract()
    data["symbol"] = "NIFTYBANK 2026-10-13 25000 CE"
    with pytest.raises(ValueError, match="NIFTY"):
        investigate_events([data])


def test_same_instrument_key_reused_for_another_strike_is_rejected():
    first = contract()
    second = contract(peak=None)
    second["strike_price"] = 25100.0
    with pytest.raises(ValueError, match="Recycled instrument key"):
        investigate_events([first, second])


def test_missing_forward_bar_censors_case_instead_of_pretending_full_path():
    data = contract().drop(index=135)
    ev, ctrl, report = investigate_events([data])
    assert report["observed_5x_events"] == 0


def test_two_different_contracts_same_expiry_not_counted_as_independent_dates():
    a = contract()
    b = contract(key="NSE_FO|TEST_2", side="PE", peak=145)
    ev, ctrl, report = investigate_events([a, b])
    assert report["observed_3x_events"] == 2
    assert report["unique_event_dates"] == 1
    assert report["unique_event_expiries"] == 1
    assert report["unique_event_contracts"] == 2


def test_one_invalid_csv_halts_entire_study():
    good = contract()
    bad = contract(key="NSE_FO|TEST_2", expiry="2026-10-07")
    with pytest.raises(ValueError, match="after contract expiry"):
        investigate_events([good, bad])


def test_predeclared_fingerprints_have_available_denominators():
    ev, controls, report = investigate_events([contract()])
    values = report["repeated_patterns"]
    assert "volume_surge_2x" in values
    assert "premium_momentum_20pct" in values
    for item in values.values():
        assert "events" in item
        assert "matched_non_events" in item
        assert "total_available" in item["events"]


def test_empty_and_invalid_horizon_fail_safely():
    with pytest.raises(ValueError, match="Upload at least"):
        investigate_events([])
    with pytest.raises(ValueError, match="Horizon"):
        investigate_events([contract()], horizon=121)


def test_does_not_require_or_write_any_market_archive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    investigate_events([contract()])
    assert not list(tmp_path.iterdir())


def test_nonoverlapping_cases_are_separated_by_at_least_horizon():
    data = contract(peak=105)
    data.loc[180, "close"] = 22.0
    data.loc[180, "high"] = 23.0
    cases, _, report = investigate_events([data], horizon=20)
    assert len(cases) == 2
    gap = pd.to_datetime(cases.signal_ist).diff().dropna()
    assert gap.ge(pd.Timedelta(minutes=20)).all()


def test_60_30_15_5_minute_fingerprint_medians_and_greek_missing():
    a = contract()
    pos, ctrl, report = investigate_events([a])
    summary = report["window_feature_medians"]
    for w in (5, 15, 30, 60):
        assert summary[f"premium_return_{w}m_pct"]["event"]["available"] == 1
        assert summary[f"gamma_change_{w}m"]["event"]["median"] is None
        assert summary[f"oi_change_{w}m_pct"]["event"]["available"] == 1
    assert summary["iv_change_15m"]["matched_non_event"]["median"] is None


def test_outside_normal_session_rejected_not_mixed_into_fingerprints():
    df = contract()
    df.loc[12, "timestamp"] = pd.Timestamp("2026-10-08 15:31:00")
    with pytest.raises(ValueError, match="outside regular"):
        investigate_events([df])
