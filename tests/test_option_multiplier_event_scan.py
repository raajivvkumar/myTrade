"""Offline v3 fixtures. Never contacts Dhan, writes market archives, or trades."""
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.research.option_multiplier_event_scan import (
    _features, scan_rolling_frame, summarize_scans,
)
from app.research.dhan_event_discovery_v3 import run


def bars(n=180, jump=None, strike=22400):
    stamp = pd.date_range("2026-10-06 09:15", periods=n, freq="min")
    frame = pd.DataFrame({
        "timestamp": stamp, "actual_strike": float(strike),
        "open": 10.0, "close": 10.0, "high": 11.0,
        "low": 9.0, "volume": 10.0, "oi": 100.0,
        "iv": 0.2, "spot": 22400.0,
    })
    if jump is not None:
        frame.loc[jump, ["close", "high"]] = 200.0
    return frame


def scan(data, **kwargs):
    return scan_rolling_frame(
        data, series="WEEK_1_ATM_CALL", side="CALL", **kwargs)


def test_first_observed_2x_3x_5x_10x_crossings_include_minute_and_numerology():
    found = summarize_scans([scan(bars(jump=50), horizon=60)])
    for level in ("2x", "3x", "5x", "10x"):
        stat = found["thresholds"][level]
        assert stat["observed_episode_count_not_deduped_across_aliases"] == 1
        assert stat["positive_anchor_windows_overlapping"] > 0
        event = found["examples"][level]["records"][0]
        assert event["hypothetical_entry_open"] == 10
        assert event["observed_crossing_close"] == 200
        assert event["first_eligible_entry_bar_ist"] == "2026-10-06T09:15+05:30"
        assert event["first_observed_crossing_close_ist"] == "2026-10-06T10:05+05:30"
        assert event["minutes_from_entry_bar"] == 50
        assert event["strike_numerology"]["compound_total"] == 8
        assert event["strike_numerology"]["root_number"] == 8
        assert event["date_numerology"]["calendar_weekday"] == "Tuesday"
        assert event["verified_fixed_contract"] is False
        assert event["verified_executable_pnl"] is False


def test_positive_before_future_cutoff_is_counted_but_negative_is_censored():
    found = summarize_scans([scan(bars(n=12, jump=8), horizon=30)])
    result = found["thresholds"]["2x"]
    assert result["positive_anchor_windows_overlapping"] > 0
    assert result["known_negative_anchor_windows"] == 0
    assert result["censored_unobserved_outcomes"]["response_or_session_end"] > 0
    assert result["past_only_rule_scores"]["premium20"]["tested_feature_complete_windows"] == 0


def test_known_negative_requires_full_uninterrupted_horizon():
    found = summarize_scans([scan(bars(100), horizon=15)])
    result = found["thresholds"]["2x"]
    assert result["known_negative_anchor_windows"] == 85
    assert result["positive_anchor_windows_overlapping"] == 0
    assert result["censored_unobserved_outcomes"]["response_or_session_end"] == 15
    assert result["past_only_rule_scores"]["premium20"]["tested_feature_complete_windows"] == 69


def test_strike_change_breaks_future_identity_no_fake_premium_profit():
    frame = bars(n=90)
    frame.loc[20:, "actual_strike"] = 22450.0
    # Strike-switch jump is not a within-contract price multiplier.
    frame.loc[20:, ["open", "close", "high"]] = 200.0
    found = summarize_scans([scan(frame, horizon=60)])
    assert found["strike_switches"] == 1
    assert found["thresholds"]["2x"][
        "observed_episode_count_not_deduped_across_aliases"] == 0


def test_missing_minute_splits_and_avoids_false_event():
    frame = bars(100)
    frame = frame.drop(index=35).reset_index(drop=True)
    # After a gap, a different stable price regime must not bridge the gap.
    frame.loc[frame.timestamp >= pd.Timestamp("2026-10-06 09:51"), [
        "open", "close", "high"]] = 200
    found = summarize_scans([scan(frame, horizon=60)])
    assert found["missing_minute_splits"] == 1
    assert found["thresholds"]["2x"]["positive_anchor_windows_overlapping"] == 0


def test_missing_oi_cannot_earn_false_negative_or_true_negative():
    data = bars(100)
    data["oi"] = np.nan
    found = summarize_scans([scan(data, horizon=15)])
    report = found["thresholds"]["2x"]
    rule = report["past_only_rule_scores"]["volume2_oi10"]
    assert rule["tested_feature_complete_windows"] == 0
    assert rule["missing"] == 69


def test_signals_do_not_read_entry_bar_future_close():
    data = bars(100)
    data.loc[16, ["close", "high"]] = 200.0
    frame = scan(data, horizon=15)["2026-10-06"]
    event = frame["event_examples"]["2"][0]
    assert event["first_prior_momentum20_sign_ist"] is None
    assert event["past_features_at_entry"] is None
    # The next minute may use the completed previous candle as its signal.
    raw = {key: pd.to_numeric(data[key]).to_numpy(dtype=float)
           for key in ("open", "close", "volume", "oi")}
    assert _features(raw, 15)["signals"]["premium20"] is False
    assert _features(raw, 16)["signals"]["premium20"] is True


def test_alias_duplicates_are_suspected_not_claimed_same_fixed_expiry():
    first = scan(bars(jump=50), horizon=60)
    other = scan_rolling_frame(
        bars(jump=50), series="WEEK_2_ATM_CALL", side="CALL", horizon=60)
    found = summarize_scans([first, other])
    item = found["examples"]["2x"]
    assert found["thresholds"]["2x"][
        "observed_episode_count_not_deduped_across_aliases"] == 2
    assert len(item["records"]) == 1
    assert item["potential_alias_coincidences_in_sample"] == 1
    assert found["chronological_holdout"][
        "contract_expiry_independent_holdout"] is False


def test_astrology_called_only_when_positive_example_and_not_for_negative():
    seen = []
    def astronomy(stamp):
        seen.append(pd.Timestamp(stamp))
        return {"planets": {"Moon": {"rashi": "Mesha"}}}
    none = summarize_scans([scan(bars(), horizon=15,
                                 astrology_fn=astronomy)])
    assert not seen
    assert none["examples"]["2x"]["records"] == []
    found = summarize_scans([scan(bars(jump=50), horizon=60,
                                 astrology_fn=astronomy)])
    assert found["examples"]["2x"]["records"][0][
        "vedic_at_entry_bar"]["planets"]["Moon"]["rashi"] == "Mesha"
    assert len(seen) >= 2


def options(execute=False, full=False, max_requests=1):
    return SimpleNamespace(
        from_date=date(2026, 10, 6), through=date(2026, 10, 6),
        execute=execute, full=full, max_requests=max_requests,
        horizon=60, min_price=2.0, pause=.25, progress=False,
        max_examples=12, vedic_astrology=False, vedic_transits=False,
    )


class MockDhan:
    def __init__(self):
        self.called = 0

    def profile(self):
        return {"dataPlan": "Active"}

    def _call(self, method, path, payload):
        self.called += 1
        assert (method, path) == ("POST", "/charts/rollingoption")
        frame = bars(n=120, jump=50)
        beginning = int(pd.Timestamp(
            "2026-10-06 09:15", tz="Asia/Kolkata").timestamp())
        return {"data": {"ce": {
            "timestamp": [beginning + i * 60 for i in range(len(frame))],
            "strike": frame.actual_strike.tolist(),
            **{key: frame[key].tolist() for key in (
                "open", "high", "low", "close", "volume", "oi", "iv", "spot")}
        }}}


def test_default_preview_never_loads_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = MockDhan()
    report = run(options(execute=False), client=client)
    assert report["status"] == "PREVIEW_NO_API_CALLS"
    assert client.called == 0
    assert report["market_files_saved"] == 0
    assert not list(tmp_path.iterdir())


def test_authenticated_synthetic_dhan_study_is_ram_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = MockDhan()
    report = run(options(execute=True), client=client, sleeper=lambda _: None)
    assert report["status"] == "PARTIAL_MAX_REQUESTS"
    assert report["completed_api_requests"] == 1
    assert report["rows_received"] == 120
    assert report["orders_sent"] == 0
    assert report["market_files_saved"] == 0
    assert report["discovery"]["thresholds"]["2x"][
        "observed_episode_count_not_deduped_across_aliases"] > 0
    assert report["history_verified_exact_contract"] is False
    assert report["verified_historical_gamma_events"] is None
    assert not list(tmp_path.iterdir())


def test_validate_vedic_transit_requires_vedic_astrology():
    args = options()
    args.vedic_transits = True
    with pytest.raises(ValueError, match="requires"):
        run(args)
