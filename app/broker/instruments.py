"""Angel One instrument-master download, cache, and search utilities."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/"
    "OpenAPIScripMaster.json"
)


def _cache_is_fresh(path: Path, max_age_hours: int) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return datetime.now(timezone.utc) - modified < timedelta(hours=max_age_hours)


def load_instruments(
    cache_path: Path,
    *,
    force_refresh: bool = False,
    max_age_hours: int = 20,
) -> list[dict[str, Any]]:
    """Load Angel's instrument master, using a local cache when possible."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if not force_refresh and _cache_is_fresh(cache_path, max_age_hours):
        with cache_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    response = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
    response.raise_for_status()
    instruments = response.json()
    if not isinstance(instruments, list):
        raise RuntimeError("Unexpected Angel instrument-master response format")

    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(instruments, handle, ensure_ascii=False)

    return instruments


def search_instruments(
    instruments: list[dict[str, Any]],
    query: str,
    *,
    exchange: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search name/symbol/token fields without hard-coding instrument tokens."""
    needle = query.strip().upper()
    exchange_needle = exchange.strip().upper() if exchange else None
    matches: list[dict[str, Any]] = []

    for item in instruments:
        item_exchange = str(item.get("exch_seg", "")).upper()
        if exchange_needle and exchange_needle not in item_exchange:
            continue

        haystack = " ".join(
            str(item.get(field, "")).upper()
            for field in ("name", "symbol", "token", "instrumenttype")
        )
        if needle in haystack:
            matches.append(item)
            if len(matches) >= limit:
                break

    return matches
