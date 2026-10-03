"""WebSocket hub for frontend clients — advisory data only."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ClientHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        raw = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self.clients)
        for ws in clients:
            try:
                await ws.send_text(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    async def send_clock_sync(self, ws: WebSocket, client_send_ms: float) -> None:
        server_now = time.time() * 1000.0
        await ws.send_text(
            json.dumps(
                {
                    "type": "clock_sync",
                    "payload": {
                        "client_send_ms": client_send_ms,
                        "server_time_ms": server_now,
                        "server_recv_ms": server_now,
                    },
                }
            )
        )


hub = ClientHub()
