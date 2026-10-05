"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.strategy_routes import router as strategy_router
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
strategy_runner = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine, orchestrator, strategy_runner
    logger.info("Starting Kalshi Tennis Signal Analyst (READ-ONLY)")
    logger.info("Order placement enabled: FALSE")
    await init_db()

    async def broadcast(msg: dict) -> None:
        await hub.broadcast(msg)

    from app.database import AsyncSessionLocal
    from app.services.kalshi.trading import KalshiTradingClient
    from app.services.trading.runner import StrategyRunner
    from app.services.trading.store import load_book
    from app.services.trading.strategy import StrategyBook

    engine = SignalEngine(settings, broadcast=broadcast, session_factory=AsyncSessionLocal)
    orchestrator = MarketOrchestrator(engine, settings)
    strategy_book = StrategyBook(
        max_data_age_ms=settings.max_data_age_ms,
        min_liquidity=settings.min_liquidity_contracts,
    )
    trading = KalshiTradingClient(orchestrator.client)
    strategy_runner = StrategyRunner(strategy_book, trading, session_factory=AsyncSessionLocal)
    await load_book(AsyncSessionLocal, strategy_book)
    engine.strategy_hook = strategy_runner.on_quote
    await orchestrator.start()
    import asyncio
    asyncio.create_task(trading.refresh_connection())
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


@app.middleware("http")
async def do_not_cache_api(request, call_next):
    """Dashboard JSON must not be stored. A cached board shows finished matches."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api") or path in {"/", "/ws"}:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response

app.include_router(router, prefix="/api")
app.include_router(strategy_router, prefix="/api")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    # Connection status first, then the board. A refresh should not sit on
    # DISCONNECTED while the match list is serialized.
    if engine:
        await hub.send(
            ws,
            {
                "type": "connection",
                "payload": {"status": engine.snap.connection_status.value},
            },
        )
        await hub.send(ws, {"type": "dashboard", "payload": engine.dashboard_payload()})
    await hub.send_clock_sync(ws, time.time() * 1000.0)
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")
            if msg_type == "ping":
                client_send = data.get("client_send_ms") or time.time() * 1000.0
                await hub.send_clock_sync(ws, client_send)
                await hub.send(
                    ws,
                    {
                        "type": "pong",
                        "payload": {
                            "server_time_ms": time.time() * 1000.0,
                            "client_send_ms": client_send,
                        },
                    },
                )
            elif msg_type == "clock_sync":
                await hub.send_clock_sync(ws, data.get("client_send_ms") or time.time() * 1000.0)
            elif msg_type == "get_dashboard":
                if engine:
                    await hub.send(ws, {"type": "dashboard", "payload": engine.dashboard_payload()})
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        logger.exception("Client websocket closed")
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
