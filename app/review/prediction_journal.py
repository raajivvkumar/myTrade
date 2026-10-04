"""Persistent local journal for tracking and reviewing live chart signals."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument TEXT NOT NULL,
    interval TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    signal TEXT NOT NULL CHECK(signal IN ('BUY', 'SELL')),
    entry_close REAL NOT NULL,
    ema_fast REAL,
    ema_slow REAL,
    strength_pct REAL,
    horizon_bars INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    outcome_timestamp TEXT,
    outcome_close REAL,
    outcome_return_pct REAL,
    outcome_reason TEXT,
    review_why_failed TEXT,
    review_why_passed TEXT,
    review_note TEXT,
    UNIQUE(instrument, interval, timestamp, signal)
);
"""


class PredictionJournal:
    """Store signals, evaluate them after a fixed bar horizon, and keep reviews."""

    def __init__(self, path: Path, *, horizon_bars: int = 3) -> None:
        if horizon_bars < 1:
            raise ValueError("horizon_bars must be at least 1")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.horizon_bars = horizon_bars
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def record_signals(
        self, instrument: str, interval: str, signalled: pd.DataFrame
    ) -> int:
        """Add unseen BUY/SELL crossover events; repeated refreshes are safe."""
        added = 0
        with self._connect() as connection:
            for _, row in signalled[signalled["signal"].isin(["BUY", "SELL"])].iterrows():
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO predictions
                    (instrument, interval, timestamp, signal, entry_close,
                     ema_fast, ema_slow, strength_pct, horizon_bars)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        instrument,
                        interval,
                        pd.Timestamp(row["timestamp"]).isoformat(),
                        str(row["signal"]),
                        float(row["close"]),
                        float(row["ema_fast"]) if pd.notna(row["ema_fast"]) else None,
                        float(row["ema_slow"]) if pd.notna(row["ema_slow"]) else None,
                        float(row["strength_pct"]) if pd.notna(row["strength_pct"]) else None,
                        self.horizon_bars,
                    ),
                )
                added += cursor.rowcount
        return added

    def evaluate_matured(
        self, instrument: str, interval: str, completed_candles: pd.DataFrame
    ) -> int:
        """Score each pending call using the close after horizon_bars completed."""
        candles = completed_candles.copy()
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], errors="coerce")
        candles["close"] = pd.to_numeric(candles["close"], errors="coerce")
        candles = (
            candles.dropna(subset=["timestamp", "close"])
            .drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        positions = {
            pd.Timestamp(value).isoformat(): index
            for index, value in enumerate(candles["timestamp"])
        }
        updates = 0
        with self._connect() as connection:
            pending = connection.execute(
                """SELECT id, timestamp, signal, entry_close, horizon_bars
                   FROM predictions
                   WHERE instrument = ? AND interval = ? AND status = 'PENDING'""",
                (instrument, interval),
            ).fetchall()
            for item in pending:
                index = positions.get(item["timestamp"])
                if index is None:
                    continue
                outcome_index = index + int(item["horizon_bars"])
                if outcome_index >= len(candles):
                    continue
                outcome = candles.iloc[outcome_index]
                entry = float(item["entry_close"])
                outcome_close = float(outcome["close"])
                change_pct = (outcome_close / entry - 1.0) * 100.0
                direction_correct = (
                    change_pct > 0 if item["signal"] == "BUY" else change_pct < 0
                )
                if abs(change_pct) < 1e-12:
                    status = "FLAT"
                    reason = "Price was unchanged over the evaluation window."
                elif direction_correct:
                    status = "PASSED"
                    reason = (
                        f"{item['signal']} matched the next {item['horizon_bars']} "
                        f"completed candles ({change_pct:+.3f}% close-to-close)."
                    )
                else:
                    status = "FAILED"
                    reason = (
                        f"{item['signal']} was contradicted by the next "
                        f"{item['horizon_bars']} completed candles "
                        f"({change_pct:+.3f}% close-to-close)."
                    )
                connection.execute(
                    """UPDATE predictions SET status = ?, outcome_timestamp = ?,
                       outcome_close = ?, outcome_return_pct = ?, outcome_reason = ?
                       WHERE id = ? AND status = 'PENDING'""",
                    (
                        status,
                        pd.Timestamp(outcome["timestamp"]).isoformat(),
                        outcome_close,
                        change_pct,
                        reason,
                        item["id"],
                    ),
                )
                updates += 1
        return updates

    def save_review(
        self,
        prediction_id: int,
        *,
        why_failed: str = "",
        why_passed: str = "",
        note: str = "",
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE predictions SET review_why_failed = ?,
                   review_why_passed = ?, review_note = ? WHERE id = ?""",
                (why_failed.strip(), why_passed.strip(), note.strip(), int(prediction_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Prediction {prediction_id} was not found")

    def list_predictions(self, *, limit: int = 500) -> pd.DataFrame:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM predictions ORDER BY timestamp DESC, id DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def review_questions(self, prediction_id: int) -> tuple[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM predictions WHERE id = ?", (int(prediction_id),)
            ).fetchone()
        if row is None:
            raise ValueError(f"Prediction {prediction_id} was not found")
        if row["status"] == "PASSED":
            return ("", "Why did I pass?")
        if row["status"] == "FAILED":
            return ("Why did I fail?", "")
        return ("Why did I fail or pass?", "What should I watch next time?")
