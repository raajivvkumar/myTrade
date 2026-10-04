"""Local Parquet storage for market candles."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def save_candles_parquet(frame: pd.DataFrame, path: Path) -> Path:
    """Merge candles with an existing Parquet file and save chronologically."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = pd.read_parquet(path)
        frame = pd.concat([existing, frame], ignore_index=True)

    if not frame.empty and "timestamp" in frame.columns:
        frame = (
            frame.drop_duplicates(subset=["timestamp"], keep="last")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    frame.to_parquet(path, index=False)
    return path


def load_candles_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_parquet(path)
