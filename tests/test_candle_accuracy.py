import json
import sqlite3

import pandas as pd
import pytest

from app.review.candle_accuracy import (
    CandleJournal, accuracy_summary, forecast_next, gamma_exposure,
    historical_accuracy, score_forecast, validate_candles,
)
from app.review.prediction_journal import PredictionJournal


def candles(count=20):
    close = [100.0 + i for i in range(count)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01 09:15", periods=count, freq="5min"),
        "open": close, "high": [x + 2 for x in close],
        "low": [x - 2 for x in close], "close": close,
    })


def test_known_drift_accuracy_and_baseline():
    records = historical_accuracy(candles(), window=3)
    summary = accuracy_summary(records)
    assert summary["scored"] == 16
    assert summary["close_mae"] == pytest.approx(0)
    assert summary["baseline_mae"] == pytest.approx(1)
    assert summary["direction_accuracy_pct"] == 100
    assert summary["beats_baseline"]


def test_historical_forecasts_do_not_change_when_future_prices_change():
    data = candles()
    original = historical_accuracy(data, window=3)
    data.loc[8:, ["open", "high", "low", "close"]] += 30
    changed = historical_accuracy(data, window=3)
    assert original.iloc[:5]["predicted_close"].tolist() == changed.iloc[:5]["predicted_close"].tolist()
    # Target row changes while its earlier prediction remains the same.
    assert original.iloc[4]["actual_close"] != changed.iloc[4]["actual_close"]


def test_live_journal_freezes_snapshot_and_scores_only_completed_target(tmp_path):
    data = candles()
    journal = CandleJournal(tmp_path / "journal.sqlite3")
    assert journal.record_latest("OPT", "5min", data.iloc[:11], minutes=5,
                                 option_gamma=0.002, lot_size=50, lots=2,
                                 gamma_source="broker snapshot")
    changed = data.iloc[:11].copy()
    changed.loc[10, ["open", "high", "low", "close"]] += 10
    assert not journal.record_latest("OPT", "5min", changed, minutes=5)
    assert journal.evaluate("OPT", "5min", data.iloc[:11]) == 0
    assert journal.evaluate("OTHER", "5min", data.iloc[:12]) == 0
    assert journal.evaluate("OPT", "5min", data.iloc[:12]) == 1
    assert journal.evaluate("OPT", "5min", changed) == 0
    row = journal.list_records().iloc[0]
    assert row["predicted_close"] == 111
    assert row["actual_close"] == 111
    assert row["gamma_exposure"] == pytest.approx(0.2)


def test_missing_target_is_not_replaced_with_later_bar(tmp_path):
    data = candles()
    journal = CandleJournal(tmp_path / "j.sqlite3")
    journal.record_latest("OPT", "5min", data.iloc[:11], minutes=5)
    assert journal.evaluate("OPT", "5min", data.drop(index=11)) == 0
    assert journal.list_records().iloc[0]["status"] == "PENDING"


def test_price_hit_is_not_direction_hit():
    prediction = forecast_next(candles().iloc[:11])
    actual = {"timestamp": "2026-01-01 10:10", "open": 110, "high": 112,
              "low": 108, "close": 109.99}
    scored = score_forecast(prediction, actual, tolerance_pct=1)
    assert scored["within_tolerance"]
    assert not scored["direction_correct"]
    assert scored["close_absolute_error"] == pytest.approx(1.01)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.01])
def test_bad_gamma_rejected(bad):
    with pytest.raises(ValueError):
        gamma_exposure(bad, 50, 1)


def test_gamma_missing_is_unknown_and_zero_is_valid():
    assert gamma_exposure(None, 50, 1) is None
    assert gamma_exposure(0, 50, 1) == 0
    assert gamma_exposure(0.001, 50, 2) == pytest.approx(0.1)


@pytest.mark.parametrize("mutation", ["duplicate", "unsorted", "range", "infinite"])
def test_invalid_candles_rejected(mutation):
    data = candles()
    if mutation == "duplicate":
        data.loc[1, "timestamp"] = data.loc[0, "timestamp"]
    elif mutation == "unsorted":
        data = data.iloc[::-1]
    elif mutation == "range":
        data.loc[0, "high"] = 90
    else:
        data.loc[0, "close"] = float("inf")
    with pytest.raises(ValueError):
        validate_candles(data)


def test_historical_session_gaps_excluded():
    data = candles()
    data.loc[10:, "timestamp"] += pd.Timedelta(days=1)
    assert len(historical_accuracy(data, window=3)) == 15


def test_existing_signal_journal_migrates_without_data_loss(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    journal = PredictionJournal(path)
    with sqlite3.connect(path) as con:
        con.execute("ALTER TABLE predictions DROP COLUMN gamma_exposure")
        con.execute("ALTER TABLE predictions DROP COLUMN gamma_source")
    upgraded = PredictionJournal(path)
    signalled = pd.DataFrame([{
        "timestamp": "2026-01-01 09:20", "close": 100, "ema_fast": 101,
        "ema_slow": 100, "strength_pct": 1, "signal": "BUY",
        "gamma_exposure": 0.2, "gamma_source": "broker snapshot",
    }])
    assert upgraded.record_signals("OPT", "5min", signalled) == 1
    assert upgraded.list_predictions().iloc[0]["gamma_exposure"] == 0.2
