"""Angel One SmartAPI session setup for market-data access.

Credentials are read from local environment variables at runtime. This module
never logs or persists credential values or session tokens.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pyotp
from SmartApi import SmartConnect


@dataclass
class AngelMarketDataSession:
    client: SmartConnect
    feed_token: str


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

    client = SmartConnect(api_key=api_key)
    otp = pyotp.TOTP(totp_secret).now()
    response = client.generateSession(client_code, pin, otp)

    if not response or not response.get("status"):
        message = response.get("message", "Authentication failed") if response else "Authentication failed"
        raise RuntimeError(f"Angel One authentication failed: {message}")

    feed_token = client.getfeedToken()
    if not feed_token:
        raise RuntimeError("Angel One did not return a market-data feed token")

    return AngelMarketDataSession(client=client, feed_token=feed_token)
