"""Offline regression coverage; no Dhan token, ephemeris or market-data persistence."""
from datetime import date

import pandas as pd
import pytest

from app.research import vedic_strike_transit_study as astro
from app.research.dhan_direct_ram_study import (
    study_chunk, aggregate,
)


def stable(start="2026-10-06 09:15", strike=22400):
    stamps = pd.date_range(start, periods=360, freq="min")
    frame = pd.DataFrame({
        "timestamp": stamps, "actual_strike": float(strike),
        "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
        "volume": 10.0, "oi": 1000.0, "iv": 0.25, "spot": 22400.0,
        "series": "WEEK_1_ATM_CALL",
    })
    return frame


def fake_transition(day):
    assert day == "2026-10-06"
    return [{
        "planet": "Moon", "transition_type": "nakshatra",
        "from": "Rohini", "to": "Mrigashira",
        "calculated_transition_time_ist": "2026-10-06T11:00:07+05:30",
        "precision": "modeled to nearest second",
    }]


def test_22400_compound_total_and_date_08082026_is_saturday():
    assert astro.compound_number(22400) == {
        "strike_price": 22400, "compound_total": 8, "root_number": 8
    }
    assert astro.compound_number(22450)["compound_total"] == 13
    assert astro.compound_number(22450)["root_number"] == 4
    info = astro.date_numerology(date(2026, 8, 8))
    assert info["calendar_weekday"] == "Saturday"
    assert info["date_compound_total"] == 26
    assert info["date_root_number"] == 8
    assert info["day_root"] == 8


def test_invalid_strikes_are_rejected():
    for value in (0, -3, 22400.5, True):
        with pytest.raises(ValueError):
            astro.compound_number(value)


def test_planet_star_change_maps_to_correct_22400_strike(monkeypatch):
    monkeypatch.setattr(astro, "session_transits", fake_transition)
    frame = stable()
    result = astro.transit_strike_observations(
        frame, series="WEEK_1_ATM_CALL", expiry_flag="WEEK", expiry_code=1)
    assert result["eligible_transit_strike_observations"] == 1
    effect = result["observations"][0]
    assert effect["strike"]["compound_total"] == 8
    assert effect["date_numerology"]["date_root_number"] == 8
    assert effect["calculated_transition_time_ist"] == "2026-10-06T11:00:07+05:30"
    assert effect["observed_first_minute_label_ist"] == "2026-10-06T11:01+05:30"
    assert effect["premium_change_post30_pct"] == 0
    assert effect["proxy_2x_peak_close"] is False
    summary = astro.summarize_transit_impact([result])
    assert summary["eligible_strike_effect_windows"] == 1
    assert summary["by_planet_change_side_strike_root"][0]["strike_root"] == 8
    assert summary["independent_contracts_verified"] is False


def test_strike_change_censors_transit_effect(monkeypatch):
    monkeypatch.setattr(astro, "session_transits", fake_transition)
    frame = stable()
    frame.loc[frame.timestamp >= pd.Timestamp("2026-10-06 11:20"), "actual_strike"] = 22450.
    result = astro.transit_strike_observations(
        frame, series="WEEK_1_ATM_CALL", expiry_flag="WEEK", expiry_code=1)
    assert result["eligible_transit_strike_observations"] == 0
    assert result["censored"]["strike_switch_during_effect_window"] == 1


def test_missing_minutes_censor_transit_effect(monkeypatch):
    monkeypatch.setattr(astro, "session_transits", fake_transition)
    frame = stable()
    frame = frame.loc[frame.timestamp != pd.Timestamp("2026-10-06 11:05")]
    result = astro.transit_strike_observations(
        frame, series="WEEK_1_ATM_CALL", expiry_flag="WEEK", expiry_code=1)
    assert result["eligible_transit_strike_observations"] == 0
    assert result["censored"]["missing_or_outside_session"] == 1


def test_first_crossing_time_and_planets_are_descriptive_not_fill():
    frame = stable()
    frame.loc[155, "close"] = 200.
    frame.loc[155, "high"] = 200.
    stub = lambda t: {
        "timestamp_ist": str(t),
        "planets": {"Moon": {"rashi": "Mesha", "nakshatra": "Ashwini"}},
    }
    reported = aggregate(study_chunk(frame, astrology_fn=stub))
    events = reported["first_crossing_examples"]["10x"]
    assert events
    first = next(x for x in events if x["first_observed_crossing_close_ist"].startswith("2026-10-06T11:50"))
    assert first["pre_signal_rules_true"] == []
    assert first["vedic_at_decision"]["planets"]["Moon"]["rashi"] == "Mesha"
    assert first["vedic_at_first_crossing"]["planets"]["Moon"]["rashi"] == "Mesha"
    assert first["identity"] == "ROLLING_STRIKE_PROXY_UNVERIFIED_EXPIRY"


def test_short_lookback_has_more_eligibility_but_not_gamma_proof():
    f = stable()
    standard = aggregate(study_chunk(f))["labeled_rolling_proxy_windows"]
    shorter = aggregate(study_chunk(
        f, volume_baseline_minutes=10))["labeled_rolling_proxy_windows"]
    assert shorter >= standard
    assert standard > 0


def test_each_exclusion_reason_reconciles_with_totals():
    f = stable()
    f.loc[40:55, "actual_strike"] = 22450.
    report = aggregate(study_chunk(f))
    assert sum(report["past_exclusion_reasons"].values()) == report["past_excluded"]
    assert sum(report["future_censor_reasons"].values()) == report["future_censored"]


def test_missing_oi_not_misclassified_as_negative():
    f = stable()
    f["oi"] = float("nan")
    report = aggregate(study_chunk(f))
    counts = report["descriptive_5m_precursor_comparison"]["2x"]
    assert counts["oi_5m_pct"]["non_events_available"] == 0
    # The missing-oi rule's confusion denominator must be zero.
    from app.research.dhan_direct_ram_study import merge_day, _cohort
    data = study_chunk(f)
    metric = _cohort(data, list(data))["rules"]["volume2_oi10"]
    assert metric["missing_features"] == report["labeled_rolling_proxy_windows"]
    assert metric["thresholds"]["2x"]["tested_feature_complete_windows"] == 0
