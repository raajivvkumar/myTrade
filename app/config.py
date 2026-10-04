"""Non-secret runtime configuration for myTrade."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    openai_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.getenv("MYTRADE_DATA_DIR", "data")),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5"),
        )

    def ensure_local_directories(self) -> None:
        for subdir in ("raw", "processed", "live", "reference"):
            (self.data_dir / subdir).mkdir(parents=True, exist_ok=True)
