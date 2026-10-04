from __future__ import annotations

import pandas as pd
import pytest

from app.review.prediction_journal import PredictionJournal


def test_signal_is_logged_once_then_matured_and_reviewed(tmp_path) -> None:
    journal = PredictionJournal(tmp_path / "journal.sqlite3", horizon_bars=2)
    signal_time = pd.Timestamp("2026-01-01 09:20")
    signals = pd.DataFrame(
        [
            {
                "timestamp": signal_time,
                "close": 100.0,
                "ema_fast": 101.0,
                "ema_slow": 100.0,
                "strength_pct": 1.0,
                "signal": "BUY",
            }
        ]
    )
    assert journal.record_signals("NIFTY50", "FIVE_MINUTE", signals) == 1
    assert journal.record_signals("NIFTY50", "FIVE_MINUTE", signals) == 0

    candles = pd.DataFrame(
        {
            "timestamp": [
                pd.Timestamp("2026-01-01 09:20"),
                pd.Timestamp("2026-01-01 09:25"),
                pd.Timestamp("2026-01-01 09:30"),
            ],
            "close": [100.0, 99.0, 98.0],
        }
    )
    assert journal.evaluate_matured("NIFTY50", "FIVE_MINUTE", candles) == 1
    row = journal.list_predictions().iloc[0]
    assert row["status"] == "FAILED"
    assert row["outcome_return_pct"] == pytest.approx(-2.0)
    assert "but the fast/slow EMA crossover" in row["outcome_reason"]

    journal.save_review(
        int(row["id"]),
        why_failed="The crossover was late during a reversal.",
        why_passed="",
        note="Add a trend filter and recheck out-of-sample.",
    )
    saved = journal.list_predictions().iloc[0]
    assert saved["review_why_failed"].startswith("The crossover")
    assert "Why did I fail?" == journal.review_questions(int(row["id"]))[0]
