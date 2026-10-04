"""Angel One SmartAPI WebSocket V2 tick stream.

This module only receives market data. It never places, modifies, or cancels orders.
"""
from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone
from typing import Any


class AngelLiveFeed:
    """Small thread-safe adapter that exposes normalized LTP ticks via a queue."""

    def __init__(
        self,
        *,
        auth_token: str,
        api_key: str,
        client_code: str,
        feed_token: str,
        exchange_type: int,
        symbol_token: str,
    ) -> None:
        if not symbol_token.strip():
            raise ValueError("symbol_token cannot be empty")
        self.exchange_type = int(exchange_type)
        self.symbol_token = symbol_token.strip()
        self.ticks: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=2000)
        self._opened = threading.Event()
        self._stopped = threading.Event()
        self._socket: Any = None
        self._thread: threading.Thread | None = None
        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        except ImportError as exc:
            raise RuntimeError(
                "SmartAPI WebSocket dependency is unavailable. Reinstall requirements.txt."
            ) from exc

        self._socket = SmartWebSocketV2(
            auth_token=auth_token,
            api_key=api_key,
            client_code=client_code,
            feed_token=feed_token,
        )
        self._socket.on_open = self._on_open
        self._socket.on_data = self._on_data
        self._socket.on_error = self._on_error
        self._socket.on_close = self._on_close

    @property
    def connected(self) -> bool:
        return self._opened.is_set() and not self._stopped.is_set()

    def start(self, timeout: float = 15.0) -> None:
        """Connect in a daemon thread and subscribe to the selected LTP token."""
        if self._thread and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._socket.connect, name="mytrade-angel-feed", daemon=True
        )
        self._thread.start()
        if not self._opened.wait(timeout):
            self.stop()
            raise TimeoutError("Angel One WebSocket did not open within 15 seconds")

    def _on_open(self, _wsapp: Any) -> None:
        self._socket.subscribe(
            "mytradefeed01",
            1,  # LTP mode
            [{"exchangeType": self.exchange_type, "tokens": [self.symbol_token]}],
        )
        self._opened.set()

    def _on_data(self, _wsapp: Any, message: dict[str, Any]) -> None:
        try:
            token = str(message.get("token", self.symbol_token))
            if token != self.symbol_token:
                return
            raw_price = message.get("last_traded_price")
            if raw_price is None:
                return
            timestamp = message.get("exchange_timestamp")
            if timestamp is None:
                tick_time = datetime.now(timezone.utc)
            else:
                tick_time = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc)
            tick = {
                "timestamp": tick_time,
                "price": float(raw_price) / 100.0,
                "volume": int(message.get("last_traded_quantity", 0) or 0),
            }
            try:
                self.ticks.put_nowait(tick)
            except queue.Full:
                # Drop the oldest tick to keep the displayed market state fresh.
                try:
                    self.ticks.get_nowait()
                except queue.Empty:
                    pass
                self.ticks.put_nowait(tick)
        except (TypeError, ValueError, OverflowError):
            return

    def _on_error(self, _wsapp: Any, error: Any) -> None:
        self._stopped.set()
        self.error = str(error)

    def _on_close(self, _wsapp: Any, *_args: Any) -> None:
        self._stopped.set()

    def stop(self) -> None:
        """Close the market-data socket if it is active."""
        self._stopped.set()
        self._opened.clear()
        if self._socket is not None:
            try:
                self._socket.close_connection()
            except Exception:
                pass
