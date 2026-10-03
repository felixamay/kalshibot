"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import get_settings
from app.database import init_db
from app.services.market.orchestrator import MarketOrchestrator
from app.services.signals.engine import SignalEngine
from app.ws.hub import hub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("kalshi_signals")

settings = get_settings()

engine: Optional[SignalEngine] = None
orchestrator: Optional[MarketOrchestrator] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, orchestrator
    logger.info("Starting Kalshi Tennis Signal Analyst (READ-ONLY)")
    logger.info("Order placement enabled: FALSE")
    await init_db()

    async def broadcast(msg: dict) -> None:
        await hub.broadcast(msg)

    engine = SignalEngine(settings, broadcast=broadcast)
    orchestrator = MarketOrchestrator(engine, settings)
    await orchestrator.start()
    yield
    if orchestrator:
        await orchestrator.stop()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Live Kalshi tennis market analyst. READ-ONLY. "
        "Never places, modifies, or cancels bets. Users place all bets manually on Kalshi."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"https://.*\.(web\.app|firebaseapp\.com|trycloudflare\.com)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    # Connection status first, then the board. A refresh should not sit on
    # DISCONNECTED while the match list is serialized.
    if engine:
        await ws.send_json(
            {
                "type": "connection",
                "payload": {"status": engine.snap.connection_status.value},
            }
        )
        await ws.send_json(
            {
                "type": "dashboard",
                "payload": engine.dashboard_payload(),
            }
        )
    await hub.send_clock_sync(ws, time.time() * 1000.0)
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                client_send = data.get("client_send_ms") or time.time() * 1000.0
                await hub.send_clock_sync(ws, client_send)
                await ws.send_json(
                    {
                        "type": "pong",
                        "payload": {
                            "server_time_ms": time.time() * 1000.0,
                            "client_send_ms": client_send,
                        },
                    }
                )
            elif msg_type == "clock_sync":
                await hub.send_clock_sync(ws, data.get("client_send_ms") or time.time() * 1000.0)
            elif msg_type == "get_dashboard":
                if engine:
                    await ws.send_json({"type": "dashboard", "payload": engine.dashboard_payload()})
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        await hub.disconnect(ws)


@app.get("/")
async def root() -> dict:
    return {
        "app": settings.app_name,
        "kalshi_read_only": True,
        "order_placement_enabled": False,
        "docs": "/docs",
        "health": "/api/health",
    }
