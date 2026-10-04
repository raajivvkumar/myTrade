"""Allowlisted market catalogue for myTrade's supported research universe."""
from __future__ import annotations

import re
from typing import Any


FOCUS_MARKETS = ("NIFTY 50", "MIDCPNIFTY", "BANKNIFTY", "MCX COMMODITIES")
INDEX_PREFIXES = {
    "NIFTY 50": ("NIFTY",),
    "MIDCPNIFTY": ("MIDCPNIFTY", "NIFTYMIDSELECT", "NIFTYMIDCAP"),
    "BANKNIFTY": ("BANKNIFTY", "NIFTYBANK"),
}


def _normalized(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def focus_instruments(
    instruments: list[dict[str, Any]],
    focus: str,
    *,
    limit: int = 300,
) -> list[dict[str, Any]]:
    """Filter Angel's current instrument master to the configured market focus.

    Tokens, expiries and lot sizes always come from the current Angel master;
    they are not hard-coded because exchange contracts change over time.
    """
    if focus not in FOCUS_MARKETS:
        raise ValueError(f"Unsupported market focus: {focus}")

    results: list[dict[str, Any]] = []
    prefixes = INDEX_PREFIXES.get(focus)
    for item in instruments:
        exchange = str(item.get("exch_seg", "")).upper()
        symbol = _normalized(item.get("symbol"))
        name = _normalized(item.get("name"))
        combined = f"{symbol} {name}"

        if focus == "MCX COMMODITIES":
            if exchange != "MCX":
                continue
            instrument_type = str(item.get("instrumenttype", "")).upper()
            if instrument_type and instrument_type not in {"FUTCOM", "OPTFUT"}:
                continue
            if not item.get("token"):
                continue
            results.append(item)
            continue

        if exchange not in {"NSE", "NFO"}:
            continue
        assert prefixes is not None
        instrument_type = str(item.get("instrumenttype", "")).upper()
        is_index_contract = instrument_type in {"FUTIDX", "OPTIDX"}
        if focus == "NIFTY 50":
            # Cash index rows are exact matches. Derivative contracts are
            # included by their exchange-provided FUTIDX/OPTIDX type.
            is_nifty_cash = symbol in {"NIFTY", "NIFTY50"} or name in {"NIFTY", "NIFTY50"}
            is_nifty_derivative = (
                is_index_contract
                and symbol.startswith("NIFTY")
                and not any(part in combined for part in ("BANK", "MID", "FIN", "NEXT", "IT", "AUTO", "PHARMA"))
            )
            if not (is_nifty_cash or is_nifty_derivative):
                continue
        else:
            is_focus_cash = any(symbol == prefix or name == prefix for prefix in prefixes)
            is_focus_derivative = (
                is_index_contract
                and any(symbol.startswith(prefix) or name.startswith(prefix) for prefix in prefixes)
            )
            if not (is_focus_cash or is_focus_derivative):
                continue

        if item.get("token"):
            results.append(item)

    def sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
        expiry = str(item.get("expiry") or "00000000")
        symbol = str(item.get("symbol") or "")
        # Keep index cash instruments first, followed by currently listed contracts.
        instrument_type = str(item.get("instrumenttype") or "")
        return (expiry, instrument_type, symbol)

    results.sort(key=sort_key)
    return results[:limit]


def instrument_label(item: dict[str, Any]) -> str:
    symbol = item.get("symbol") or item.get("name") or item.get("token")
    exchange = item.get("exch_seg") or "?"
    expiry = item.get("expiry")
    lot_size = item.get("lotsize") or 1
    instrument_type = item.get("instrumenttype") or "INDEX"
    expiry_text = f" · {expiry}" if expiry else ""
    return (
        f"{exchange}:{symbol} · {instrument_type}{expiry_text} "
        f"· lot {lot_size} · token {item.get('token')}"
    )
