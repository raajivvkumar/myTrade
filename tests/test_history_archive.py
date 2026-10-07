import sqlite3

import pandas as pd
import pytest

from app.data.archive import HistoryArchive
from app.backtest.engine import run_backtest, BacktestConfig
from app.strategy.ema_crossover import generate_ema_crossover_signals


def contract(**updates):
    return dict(exch_seg="NFO", token="123", symbol="NIFTY27OCT2622400PE", name="NIFTY",
                expiry="27OCT2026", strike="2240000", lotsize="65", instrumenttype="OPTIDX", **updates)


def candles():
    prices = [10 + i % 6 for i in range(30)]
    return pd.DataFrame(dict(timestamp=pd.date_range("2026-10-06 09:15", periods=30, freq="5min"),
                            open=prices, high=[p+1 for p in prices], low=[p-1 for p in prices],
                            close=prices, volume=100, option_gamma=0.001))


def test_expired_history_offline_restart_backup_and_backtest(tmp_path):
    path = tmp_path / "archive.sqlite3"
    archive = HistoryArchive(path)
    identity = archive.save(contract(), "FIVE_MINUTE", candles(), source="broker")
    reopened = HistoryArchive(path)  # No broker or current instrument master needed.
    frame = reopened.load(identity, "FIVE_MINUTE")
    assert len(frame) == 30 and frame.lot_size.iloc[0] == 65
    assert reopened.catalogue()[0]["expiry"] == "2026-10-27"
    result = run_backtest(frame, generate_ema_crossover_signals(frame, fast_period=2, slow_period=3)["signal"], BacktestConfig())
    assert result.metrics["trades"] > 0
    backup = reopened.backup(tmp_path / "backup.sqlite3")
    pd.testing.assert_frame_equal(frame, HistoryArchive(backup).load(identity, "FIVE_MINUTE"))


def test_recycled_token_and_different_intervals_do_not_mix(tmp_path):
    archive = HistoryArchive(tmp_path / "archive.sqlite3")
    one = contract()
    two = {**one, "symbol": "NIFTY24NOV2622400PE", "expiry": "24NOV2026"}
    a = archive.save(one, "FIVE_MINUTE", candles(), source="broker")
    b = archive.save(two, "FIVE_MINUTE", candles().iloc[:5], source="broker")
    archive.save(one, "ONE_MINUTE", candles().iloc[:3], source="broker")
    assert a != b
    assert len(archive.load(a, "FIVE_MINUTE")) == 30
    assert len(archive.load(b, "FIVE_MINUTE")) == 5
    assert len(archive.load(a, "ONE_MINUTE")) == 3


def test_idempotence_empty_response_and_revision_retention(tmp_path):
    archive = HistoryArchive(tmp_path / "archive.sqlite3")
    identity = archive.save(contract(), "FIVE_MINUTE", candles(), source="broker")
    archive.save(contract(), "FIVE_MINUTE", candles(), source="broker")
    archive.save(contract(), "FIVE_MINUTE", candles().iloc[:0], source="broker")
    assert len(archive.load(identity, "FIVE_MINUTE")) == 30
    updated = candles().iloc[:1].copy().astype({"close": float})
    updated.loc[0, "close"] = 10.5
    archive.save(contract(), "FIVE_MINUTE", updated, source="broker")
    with sqlite3.connect(archive.path) as db:
        assert db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 1
    assert archive.load(identity, "FIVE_MINUTE").iloc[0]["close"] == 10.5
    archive.save(contract(), "FIVE_MINUTE", candles(), source="angel-live-ticks")
    assert archive.load(identity, "FIVE_MINUTE").iloc[0]["close"] == 10.5


def test_forming_bars_excluded_timezone_and_invalid_batch_atomic(tmp_path):
    archive = HistoryArchive(tmp_path / "archive.sqlite3")
    frame = candles()
    frame["timestamp"] = frame.timestamp.dt.tz_localize("Asia/Kolkata").dt.tz_convert("UTC")
    identity = archive.save(contract(), "FIVE_MINUTE", frame, source="broker", now="2026-10-06 09:23")
    assert len(archive.load(identity, "FIVE_MINUTE")) == 1
    frame.loc[1, "high"] = -1
    with pytest.raises(ValueError):
        archive.save(contract(), "FIVE_MINUTE", frame, source="broker")
    assert len(archive.load(identity, "FIVE_MINUTE")) == 1


def test_lot_size_conflict_rejected(tmp_path):
    archive = HistoryArchive(tmp_path / "archive.sqlite3")
    identity = archive.save(contract(), "FIVE_MINUTE", candles(), source="broker")
    with pytest.raises(ValueError, match="lot size conflict"):
        archive.save({**contract(), "lotsize": "75"}, "FIVE_MINUTE", candles(), source="broker")
    assert archive.load(identity, "FIVE_MINUTE").lot_size.iloc[0] == 65


def test_offline_dashboard_browses_and_backtests_archive(monkeypatch, tmp_path):
    import socket
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    def offline(*args, **kwargs):
        raise AssertionError("Offline history must not contact a network service")

    monkeypatch.setattr(socket.socket, "connect", offline)
    monkeypatch.setenv("MYTRADE_DATA_DIR", str(tmp_path))
    archive = HistoryArchive(tmp_path / "archive" / "history.sqlite3")
    archive.save(contract(), "FIVE_MINUTE", candles(), source="broker")
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app" / "dashboard.py").run(timeout=15)
    assert not app.exception
    source = next(s for s in app.selectbox if s.label == "Historical data source")
    source.select("Permanent contract archive").run()
    assert not app.exception
    assert next(n for n in app.number_input if n.label == "Units per lot").value == 65
    next(b for b in app.button if b.label == "Run backtest").click().run()
    assert not app.exception
    assert any(m.label == "Net P&L" for m in app.metric)
    next(b for b in app.button if b.label == "Measure archived candle accuracy").click().run()
    assert not app.exception
    assert any(m.label == "Close MAE" for m in app.metric)
