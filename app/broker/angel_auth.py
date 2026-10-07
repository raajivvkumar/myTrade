"""Angel One SmartAPI session setup for market-data access.

Credentials are read from local environment variables at runtime. This module
never logs or persists credential values or session tokens.

SmartAPI is imported only when an authenticated broker connection is explicitly
requested. Keeping the SDK out of module import paths lets offline history,
backtests, dashboards, and tests run without triggering the SDK's external-IP
lookup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import pyotp


@dataclass
class AngelMarketDataSession:
    client: Any
    feed_token: str
    auth_token: str


def _load_smart_connect() -> type[Any]:
    """Load SmartConnect only for an explicit broker connection."""
    try:
        from SmartApi import SmartConnect
    except Exception as exc:
        raise RuntimeError(
            "Angel One SmartAPI could not be loaded. Broker login requires the "
            "SmartAPI dependency and network access. Offline history and "
            "backtests remain available without it."
        ) from exc
    return SmartConnect


def connect_market_data() -> AngelMarketDataSession:
    api_key = os.getenv("ANGEL_API_KEY")
    client_code = os.getenv("ANGEL_CLIENT_CODE")
    pin = os.getenv("ANGEL_PIN")
    totp_secret = os.getenv("ANGEL_TOTP_SECRET")

    missing = [
        name
        for name, value in {
            "ANGEL_API_KEY": api_key,
            "ANGEL_CLIENT_CODE": client_code,
            "ANGEL_PIN": pin,
            "ANGEL_TOTP_SECRET": totp_secret,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing local Angel One configuration: " + ", ".join(missing)
        )

    SmartConnect = _load_smart_connect()
    client = SmartConnect(api_key=api_key)
    otp = pyotp.TOTP(totp_secret).now()
    response = client.generateSession(client_code, pin, otp)

    if not response or not response.get("status"):
        message = (
            response.get("message", "Authentication failed")
            if response
            else "Authentication failed"
        )
        raise RuntimeError(f"Angel One authentication failed: {message}")

    auth_token = (response.get("data") or {}).get("jwtToken")
    feed_token = client.getfeedToken()
    if not auth_token or not feed_token:
        raise RuntimeError(
            "Angel One did not return required market-data session tokens"
        )

    return AngelMarketDataSession(
        client=client,
        feed_token=feed_token,
        auth_token=auth_token,
    )
