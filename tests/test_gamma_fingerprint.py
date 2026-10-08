"""Offline synthetic regression tests: no looked-ahead premium 5x claims."""
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.research.gamma_fingerprint import (
    ResearchRules, index_precursors, evaluate_contract, summarize,
)
from app.research.gamma_cli import load_index, run


def index_bars():
    first = pd.Timestamp("2026-03-23 09:15")
    rows = []
    previous = 23000.0
    for i in range(375):
        if i < 120:
            close = 23000 + ((i % 3) - 1) * 0.5
            extra = 0.4
        elif i < 131:
            close = 23000 + (i - 119) * 11
            extra = 4.0
        else:
            close = 23121 + (i - 130) * 0.2
            extra = 0.4
        rows.append({
            "timestamp": first + pd.Timedelta(minutes=i),
            "open": previous,
            "high": max(previous, close) + extra,
            "low": min(previous, close) - extra,
            "close": close,
            "volume": 0,
            "oi": 0,
            "instrument_key": "NSE_INDEX|Nifty 50",
        })
        previous = close
    return pd.DataFrame(rows)


def contract_bars(index):
    out = pd.DataFrame({
        "timestamp": index.timestamp,
        "open": [3.0] * len(index),
        "high": [3.5] * len(index),
        "low": [2.8] * len(index),
        "close": [3.1] * len(index),
        "volume": [10 if n < 120 else 500 for n in range(len(index))],
        "oi": [1000] * len(index),
        "instrument_key": ["NSE_FO|12345|24-03-2026"] * len(index),
        "symbol": ["NIFTY 23000 CE 24 MAR 26"] * len(index),
        "strike_price": [23000.0] * len(index),
        "option_type": ["CE"] * len(index),
        "expiry": ["2026-03-24"] * len(index),
        "lot_size": [65] * len(index),
        "source_quality": ["UPSTOX_UNVALIDATED_NEEDS_NSE_CHECK"] * len(index),
    })
    return out


def test_precursor_appears_after_burst_not_before_and_stays_unvalidated():
    features = index_precursors(index_bars())
    events = features.loc[features.precursor]
    assert len(events) >= 1
    assert events.timestamp.min() >= pd.Timestamp("2026-03-23 11:15")
    assert events.eligible.all()
    assert events.conditions_met.eq(4).all()
    assert (events.available_after_ist == events.timestamp + pd.Timedelta(minutes=1)).all()


def test_no_future_leakage_of_features():
    first = index_bars()
    mutated = first.copy()
    mask = mutated.index >= 225
    mutated.loc[mask, "close"] += 900
    mutated.loc[mask, "high"] += 900
    mutated.loc[mask, "low"] += 900
    mutated.loc[mask, "open"] += 900
    features_a = index_precursors(first)
    features_b = index_precursors(mutated)
    cols = ["move_5m_bps", "range_ratio", "abs_return_ratio", "precursor"]
    pd.testing.assert_frame_equal(features_a.loc[:200, cols],
                                  features_b.loc[:200, cols])


def test_any_missing_minute_invalidates_recent_lookback():
    data = index_bars().drop(index=90)
    result = index_precursors(data)
    row = result.loc[result.timestamp == pd.Timestamp("2026-03-23 10:47")]
    assert len(row) == 1
    assert not bool(row.eligible.iloc[0])


def test_one_exact_contract_can_label_observational_5x_not_real_profit():
    data = index_bars()
    features = index_precursors(data)
    t = features.loc[features.precursor, "timestamp"].iloc[0]
    option = contract_bars(data)
    move_time = t + pd.Timedelta(minutes=8)
    chosen = option.timestamp.eq(move_time)
    assert chosen.any()
    option.loc[chosen, "close"] = 19.0
    option.loc[chosen, "high"] = 20.0
    events = evaluate_contract(features, option)
    choice = events.loc[events.signal_at_ist.eq(str(t))].iloc[0]
    assert choice.entry_next_open == 3.0
    assert choice.observed_ge_5x
    assert choice.joint_watch
    assert not choice.observed_ge_10x
    assert choice.result_type == "OBSERVATIONAL_UPPER_BOUND_NOT_EXECUTABLE_PNL"
    report = summarize(features, events, 30)
    assert report["outcomes_status"] == "EXPLORATORY_OPTION_HINDSIGHT_LABELS"


def test_intrabar_100x_high_does_not_become_5x_close_label():
    data = index_bars()
    features = index_precursors(data)
    option = contract_bars(data)
    option.loc[140, "high"] = 300.0
    result = evaluate_contract(features, option)
    assert not result.empty
    assert not result.observed_ge_5x.any()


def test_option_contract_switch_rejected_even_if_market_moves():
    data = index_bars()
    options = contract_bars(data)
    options.loc[100, "instrument_key"] = "NSE_FO|other|24-03-2026"
    with pytest.raises(ValueError, match="Mixed"):
        evaluate_contract(index_precursors(data), options)


def test_future_missing_bar_censors_that_event():
    index = index_bars()
    features = index_precursors(index)
    event_t = features.loc[features.precursor, "timestamp"].iloc[0]
    option = contract_bars(index)
    missing_time = event_t + pd.Timedelta(minutes=12)
    option = option.loc[~option.timestamp.eq(missing_time)]
    events = evaluate_contract(features, option)
    assert str(event_t) not in set(events.signal_at_ist)


def test_index_only_report_must_not_claim_option_outcome():
    index = index_precursors(index_bars())
    report = summarize(index, None, 30)
    assert report["outcomes_status"] == "NOT_AVAILABLE_NO_EXACT_OPTION_CONTRACT"
    assert "joint_watch_5x_rate" not in report


def test_offline_cli_loads_only_index_and_outputs_clearly_marked_exploration(tmp_path):
    data = index_bars()
    source = tmp_path / "archive"
    folder = source / "NIFTY" / "1minute"
    folder.mkdir(parents=True)
    parquet = folder / "2026-03-23_to_2026-03-23_inclusive.parquet"
    data.to_parquet(parquet)
    parquet.with_suffix(".json").write_text(json.dumps({
        "index": "NIFTY", "interval_minutes": 1, "rows": len(data),
        "data_kind": "INDEX_CANDLES_NOT_OPTION_CONTRACT",
    }))
    f = load_index(source)
    assert len(f) == 375
    class Args:
        index_archive = source
        option_parquet = None
        output_dir = tmp_path / "result"
        move_5m_bps = 12.0
        range_ratio = 1.35
        volatility_ratio = 1.3
        cooldown_minutes = 15
        horizon_minutes = 30
        min_premium = 2.0
    report = run(Args())
    assert report["not_validated"]
    assert report["outcomes_status"] == "NOT_AVAILABLE_NO_EXACT_OPTION_CONTRACT"
    assert (Args.output_dir / "index_precursor_events.csv").is_file()
    assert (Args.output_dir / "gamma_research_summary.json").is_file()


def test_invalid_option_identity_and_no_expiry_crossover():
    data = index_bars()
    features = index_precursors(data)
    option = contract_bars(data)
    option.loc[:, "expiry"] = "2026-03-22"
    with pytest.raises(ValueError, match="after expiry"):
        evaluate_contract(features, option)


def test_missing_intraday_continuity_prevents_false_precursor_across_days():
    first = index_bars()
    second = first.copy()
    second.timestamp = second.timestamp + pd.Timedelta(days=1)
    combined = index_precursors(pd.concat([first, second], ignore_index=True))
    first_bar_of_second_day = combined.loc[
        combined.timestamp.eq(pd.Timestamp("2026-03-24 09:15"))
    ]
    assert not first_bar_of_second_day.eligible.any()
