"""Kalshi WebSocket client — market data only. Never sends order commands."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from app.config import Settings, get_settings
from app.core.enums import ConnectionStatus

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class KalshiWebSocketClient:
    """
    Subscribes to ticker / orderbook_delta / trade channels.

    SAFETY: Outbound messages are limited to subscribe, unsubscribe, and ping.
    Any attempt to send order-related commands raises PermissionError.
    """

    ALLOWED_OUTBOUND_TYPES = {"subscribe", "unsubscribe", "ping", "cmd"}

    def __init__(
        self,
        settings: Settings | None = None,
        on_message: MessageHandler | None = None,
        on_status: Callable[[ConnectionStatus], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.on_message = on_message
        self.on_status = on_status
        self.status = ConnectionStatus.DISCONNECTED
        self._ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._subscribed_tickers: set[str] = set()
        self.last_message_at_ms: float = 0.0
        self.reconnect_attempts = 0

    async def _set_status(self, status: ConnectionStatus) -> None:
        self.status = status
        if self.on_status:
            await self.on_status(status)

    def _assert_safe_outbound(self, payload: dict[str, Any]) -> None:
        """Allow market-data subscriptions only. Never send order commands."""
        cmd = str(payload.get("cmd") or payload.get("type") or "").lower()
        allowed_cmds = {"subscribe", "unsubscribe", "update_subscription"}
        if cmd not in allowed_cmds:
            raise PermissionError(
                "BLOCKED: WebSocket command is not a market-data subscription. "
                "This application NEVER places or cancels orders."
            )
        channels = (payload.get("params") or {}).get("channels") or []
        allowed_channels = {"orderbook_delta", "ticker", "trade", "market_lifecycle_v2"}
        for channel in channels:
            if str(channel) not in allowed_channels:
                raise PermissionError(
                    f"BLOCKED: WebSocket channel {channel} is not a read-only market feed."
                )

    async def send(self, payload: dict[str, Any]) -> None:
        self._assert_safe_outbound(payload)
        if not self._ws:
            raise RuntimeError("WebSocket not connected")
        await self._ws.send(json.dumps(payload))

    async def subscribe_markets(self, tickers: list[str]) -> None:
        self._subscribed_tickers.update(tickers)
        if not self._ws:
            return
        # Kalshi WS subscribe format (channels: orderbook_delta, ticker, trade)
        msg = {
            "id": int(time.time() * 1000) % 1_000_000,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta", "ticker", "trade"],
                "market_tickers": list(tickers),
            },
        }
        await self.send(msg)

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="kalshi-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._set_status(ConnectionStatus.DISCONNECTED)

    async def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._set_status(ConnectionStatus.RECONNECTING)
                extra_headers = {}
                # Authenticated WS when key is configured
                if self.settings.kalshi_api_key_id and (
                    self.settings.kalshi_private_key_pem or self.settings.kalshi_private_key_path
                ):
                    try:
                        from app.services.kalshi.client import KalshiReadOnlyClient

                        tmp = KalshiReadOnlyClient(self.settings)
                        if tmp._private_key:
                            ts = str(int(time.time() * 1000))
                            path = "/trade-api/ws/v2"
                            sig = tmp._sign(ts, "GET", path)
                            extra_headers = {
                                "KALSHI-ACCESS-KEY": self.settings.kalshi_api_key_id,
                                "KALSHI-ACCESS-TIMESTAMP": ts,
                                "KALSHI-ACCESS-SIGNATURE": sig,
                            }
                    except Exception as exc:
                        logger.debug("WS auth headers skipped: %s", exc)

                async with websockets.connect(
                    self.settings.kalshi_ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=8 * 1024 * 1024,
                    additional_headers=extra_headers or None,
                ) as ws:
                    self._ws = ws
                    self.reconnect_attempts = 0
                    backoff = 1.0
                    await self._set_status(ConnectionStatus.CONNECTED)
                    logger.info("Kalshi WebSocket connected")
                    if self._subscribed_tickers:
                        await self.subscribe_markets(list(self._subscribed_tickers))
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        self.last_message_at_ms = time.time() * 1000.0
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if self.on_message:
                            await self.on_message(data)
            except asyncio.CancelledError:
                break
            except ConnectionClosed as exc:
                logger.warning("Kalshi WS closed: %s", exc)
            except Exception as exc:
                msg = str(exc)
                if "401" in msg:
                    logger.warning(
                        "Kalshi WebSocket requires an API key (HTTP 401). "
                        "REST quotes continue. Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH."
                    )
                    backoff = 60.0
                else:
                    logger.error("Kalshi WS error: %s", exc)
            finally:
                self._ws = None
                await self._set_status(ConnectionStatus.DISCONNECTED)

            if self._stop.is_set():
                break
            self.reconnect_attempts += 1
            await self._set_status(ConnectionStatus.RECONNECTING)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    @property
    def is_connected(self) -> bool:
        return self.status == ConnectionStatus.CONNECTED

    @property
    def data_age_ms(self) -> float:
        if not self.last_message_at_ms:
            return float("inf")
        return time.time() * 1000.0 - self.last_message_at_ms
