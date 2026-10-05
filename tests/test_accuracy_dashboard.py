from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.review.candle_accuracy import CandleJournal
from test_candle_accuracy import candles


def test_dashboard_and_saved_accuracy_view(monkeypatch, tmp_path):
    monkeypatch.setenv("MYTRADE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.broker.instruments.load_instruments", lambda *args, **kwargs: [])
    data = candles()
    journal = CandleJournal(tmp_path / "mytrade_journal.sqlite3")
    journal.record_latest("TEST OPTION", "FIVE_MINUTE", data.iloc[:11], minutes=5,
                          option_gamma=0.001, lot_size=50, gamma_source="test fixture")
    journal.evaluate("TEST OPTION", "FIVE_MINUTE", data.iloc[:12])
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app" / "dashboard.py").run(timeout=30)
    assert not app.exception
    assert [tab.label for tab in app.tabs] == [
        "Live chart and signals", "Backtesting", "Candle accuracy", "Prediction review",
    ]
    assert any(metric.label == "Close MAE" for metric in app.metric)
