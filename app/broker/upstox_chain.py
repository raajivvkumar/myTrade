"""Read-only Upstox NIFTY option chain, strictly in memory.

API response is not written, cached on disk, nor routed through order APIs.
All credentials remain in the local process environment.
"""
from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

BASE = "https://api.upstox.com/v2"
INDEX_KEY = "NSE_INDEX|Nifty 50"
ALLOWED_RELATIVE_EXPIRIES = {
    "current_week", "next_week", "far_week",
    "current_month", "next_month", "far_month",
}


def get_option_chain(expiry: str = "current_week", *, token: str | None = None,
                     session=None) -> dict:
    load_dotenv()
    if expiry not in ALLOWED_RELATIVE_EXPIRIES:
        from datetime import date
        try:
            if date.fromisoformat(expiry).isoformat() != expiry:
                raise ValueError()
        except ValueError as exc:
            raise ValueError("Expiry must be YYYY-MM-DD or official relative keyword") from exc
    credential = token if token is not None else os.getenv("UPSTOX_ANALYTICS_TOKEN", "")
    if not credential or not credential.strip():
        raise RuntimeError(
            "Set UPSTOX_ANALYTICS_TOKEN only in your private local .env file"
        )
    client = session if session is not None else requests.Session()
    try:
        response = client.get(
            BASE + "/option/chain",
            params={"instrument_key": INDEX_KEY, "expiry_date": expiry},
            headers={"Accept": "application/json",
                     "Authorization": "Bearer " + credential.strip()},
            timeout=25,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Option-chain request failed (network/timeout)") from exc
    if response.status_code != 200:
        raise RuntimeError(
            f"Upstox option-chain HTTP {response.status_code}; "
            "check Analytics Token, expiry and account permissions"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError("Invalid option-chain JSON") from exc
    if not isinstance(body, dict) or body.get("status") != "success":
        raise RuntimeError("Upstox option-chain response unsuccessful")
    if not isinstance(body.get("data"), list):
        raise ValueError("Upstox option-chain 'data' must be a list")
    return body
