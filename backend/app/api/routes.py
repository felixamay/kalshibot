"""HTTP API routes."""

from __future__ import annotations

import time
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.enums import SignalStatus
from app.database import get_db
from app.models import ManualEntry, ManualExit, ManualPosition, User
from app.schemas.api import (
    HealthResponse,
    LoginRequest,
    ManualEntryRequest,
    ManualExitRequest,
    RegisterRequest,
    TokenResponse,
)
from app.services.auth.security import (
    create_access_token,
    get_current_user,
    get_optional_user,
    hash_password,
    verify_password,
)

router = APIRouter()


def get_engine():
    from app.main import engine

    return engine


def get_orchestrator():
    from app.main import orchestrator

    return orchestrator


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    engine = get_engine()
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    dash = engine.dashboard_payload() if engine else {}
    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        server_time_ms=time.time() * 1000.0,
        connection_status=dash.get("connection_status", "DISCONNECTED"),
        live_matches=dash.get("live_match_count", 0),
        database=db_status,
        kalshi_read_only=True,
        order_placement_enabled=False,
    )


@router.get("/time")
async def server_time() -> dict[str, float]:
    return {"server_time_ms": time.time() * 1000.0}


@router.post("/auth/register", response_model=TokenResponse)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    existing = await db.execute(select(User).where(User.email == body.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")
    user = User(
        id=str(uuid4()),
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        display_name=body.display_name,
    )
    db.add(user)
    await db.flush()
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
    )


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == body.email.lower()))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
    )


@router.get("/auth/me")
async def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "alerts_enabled": user.alerts_enabled,
    }


@router.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    engine = get_engine()
    return engine.dashboard_payload()


@router.get("/signals/active")
async def active_signals() -> dict[str, Any]:
    engine = get_engine()
    now = time.time() * 1000.0
    signals = [
        s.to_public_dict(now)
        for s in engine.snap.signals.values()
        if s.is_actionable(now)
    ]
    # Absolute safety filter
    signals = [s for s in signals if s["actionable"] and s["remaining_ms"] > 0 and s["status"] == "ACTIVE"]
    return {"server_time_ms": now, "signals": signals}


@router.get("/signals/history")
async def signal_history(limit: int = 50) -> dict[str, Any]:
    engine = get_engine()
    return {"signals": engine.snap.signal_history[-limit:]}


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict[str, Any]:
    engine = get_engine()
    sig = engine.snap.signals.get(signal_id)
    if not sig:
        # check history
        for h in reversed(engine.snap.signal_history):
            if h.get("signal_id") == signal_id:
                return h
        raise HTTPException(404, "Signal not found")
    return sig.to_public_dict()


def _strategy_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "initial_observation_seconds": s.initial_observation_seconds,
        "watch_confidence": s.watch_confidence,
        "min_bet_confidence": s.min_bet_confidence,
        "strong_bet_confidence": s.strong_bet_confidence,
        "min_net_edge": s.min_net_edge,
        "excellent_market_min_edge": s.excellent_market_min_edge,
        "medium_market_min_edge": s.medium_market_min_edge,
        "poor_market_min_edge": s.poor_market_min_edge,
        "strong_net_edge": s.strong_net_edge,
        "entry_confirmation_count": s.entry_confirmation_count,
        "max_signals_per_match": s.max_signals_per_match,
        "reentry_cooldown_seconds": s.reentry_cooldown_seconds,
        "min_signal_ttl_seconds": s.min_signal_ttl_seconds,
        "default_signal_ttl_seconds": s.default_signal_ttl_seconds,
        "max_signal_ttl_seconds": s.max_signal_ttl_seconds,
        "signal_timer_refresh_ms": s.signal_timer_refresh_ms,
        "max_data_age_ms": s.max_data_age_ms,
        "spread_excellent_cents": s.spread_excellent_cents,
        "spread_good_cents": s.spread_good_cents,
        "spread_acceptable_cents": s.spread_acceptable_cents,
        "uncertainty_penalty_high": s.uncertainty_penalty_high,
        "order_placement_enabled": False,
        "read_only_kalshi": True,
    }


@router.get("/config")
async def public_config() -> dict[str, Any]:
    return _strategy_config()


@router.patch("/config/strategy")
async def update_strategy(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Update in-memory strategy thresholds. Never enables order placement."""
    allowed = {
        "watch_confidence",
        "min_bet_confidence",
        "strong_bet_confidence",
        "min_net_edge",
        "excellent_market_min_edge",
        "medium_market_min_edge",
        "poor_market_min_edge",
        "strong_net_edge",
        "entry_confirmation_count",
        "max_signals_per_match",
        "default_signal_ttl_seconds",
    }
    s = get_settings()
    for key, value in body.items():
        if key not in allowed:
            continue
        current = getattr(s, key)
        setattr(s, key, type(current)(value))
    return _strategy_config()


@router.post("/positions/enter")
async def manual_enter(
    body: ManualEntryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Record a manual bet the user placed on Kalshi themselves.
    NEVER sends an order to Kalshi.
    """
    engine = get_engine()
    match_id = None
    market_id = body.market_ticker
    ctx = engine.snap.matches.get(body.market_ticker)
    if ctx:
        match_id = ctx.match_id
        market_id = ctx.market_db_id

    # Invalidate linked signal if present
    if body.signal_id and body.signal_id in engine.snap.signals:
        from app.core.enums import ExpirationReason

        sig = engine.snap.signals[body.signal_id]
        if sig.status == SignalStatus.ACTIVE:
            sig.cancel(ExpirationReason.USER_PLACED, price=body.entry_price)
            sig.status = SignalStatus.CONVERTED

    position = ManualPosition(
        id=str(uuid4()),
        user_id=user.id,
        market_id=market_id,
        match_id=match_id,
        signal_id=body.signal_id,
        direction=body.direction,
        player=body.player,
        status="OPEN",
        entry_price=body.entry_price,
        amount=body.amount,
        peak_price=body.entry_price,
        current_exit_price=body.entry_price,
        position_state="HOLD",
    )
    entry = ManualEntry(
        id=str(uuid4()),
        position_id=position.id,
        entry_price=body.entry_price,
        amount=body.amount,
        notes=body.notes,
    )
    db.add(position)
    db.add(entry)
    await db.flush()

    if ctx:
        engine.open_position(
            body.market_ticker,
            position_id=position.id,
            player=body.player,
            direction=body.direction,
            entry_price=body.entry_price,
            amount=body.amount,
        )

    return {
        "position_id": position.id,
        "status": "OPEN",
        "message": "Manual entry recorded. No order was sent to Kalshi.",
        "kalshi_order_placed": False,
    }


@router.post("/positions/exit")
async def manual_exit(
    body: ManualExitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await db.execute(
        select(ManualPosition).where(
            ManualPosition.id == body.position_id, ManualPosition.user_id == user.id
        )
    )
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(404, "Position not found")
    pnl = None
    if position.direction == "YES":
        pnl = (body.exit_price - position.entry_price) / 100.0 * body.amount
    else:
        pnl = (position.entry_price - body.exit_price) / 100.0 * body.amount

    exit_row = ManualExit(
        id=str(uuid4()),
        position_id=position.id,
        exit_price=body.exit_price,
        amount=body.amount,
        pnl=pnl,
        notes=body.notes,
    )
    position.status = "CLOSED"
    position.closed_at = None  # server default via update
    position.position_state = "CLOSED"
    db.add(exit_row)
    await db.flush()

    settings = get_settings()
    # Start cooldown on matching ticker if known
    engine = get_engine()
    for ticker, ctx in engine.snap.matches.items():
        if ctx.market_db_id == position.market_id or ticker == position.market_id:
            engine.close_position(ticker)

    return {
        "position_id": position.id,
        "pnl": pnl,
        "status": "CLOSED",
        "cooldown_seconds": settings.reentry_cooldown_seconds,
        "message": "Manual exit recorded. No order was sent to Kalshi.",
        "kalshi_order_placed": False,
    }


@router.get("/positions")
async def list_positions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await db.execute(
        select(ManualPosition).where(ManualPosition.user_id == user.id)
    )
    rows = result.scalars().all()
    return {
        "positions": [
            {
                "id": p.id,
                "market_id": p.market_id,
                "player": p.player,
                "direction": p.direction,
                "entry_price": p.entry_price,
                "amount": p.amount,
                "status": p.status,
                "position_state": p.position_state,
                "peak_price": p.peak_price,
                "signal_id": p.signal_id,
            }
            for p in rows
        ]
    }


@router.get("/analytics/signals")
async def signal_analytics() -> dict[str, Any]:
    """Signal performance snapshots at +1/5/10/30/60s horizons (in-memory)."""
    from app.services.analytics.performance import performance_tracker

    return performance_tracker.summary()


@router.get("/backtest/ttl")
async def backtest_ttl() -> dict[str, Any]:
    from app.services.analytics.backtest_ttl import run_ttl_backtest

    return run_ttl_backtest()


def _horizon_price(item: dict[str, Any], seconds: int) -> float | None:
    results = item.get("results")
    if not isinstance(results, dict):
        return None
    row = results.get(seconds) or results.get(str(seconds)) or {}
    if not isinstance(row, dict):
        return None
    return row.get("future_price")


def _horizon_field(item: dict[str, Any], field: str) -> float | None:
    results = item.get("results")
    if not isinstance(results, dict):
        return None
    for key in (15, "15", 5, "5", 30, "30"):
        row = results.get(key)
        if isinstance(row, dict) and row.get(field) is not None:
            return row.get(field)
    return None


@router.get("/backtest/strategies")
async def backtest_strategies() -> dict[str, Any]:
    """Compare the old fixed gate with the dynamic mispricing gate. Does not pick a winner."""
    from app.services.signals.mispricing import compare_strategy_profiles

    illustrative = [
        {
            "name": "42c contract with a real edge",
            "confidence": 83,
            "adjusted_edge": 0.05,
            "dynamic_min_edge": 0.02,
            "liquidity_quality": "GOOD",
            "spread_quality": "GOOD",
            "market_quality": "GOOD",
            "max_adverse_movement": -1.0,
        },
        {
            "name": "75c favorite with a tiny edge",
            "confidence": 88,
            "adjusted_edge": 0.01,
            "dynamic_min_edge": 0.02,
            "liquidity_quality": "HIGH",
            "spread_quality": "EXCELLENT",
            "market_quality": "HIGH",
            "max_adverse_movement": -0.5,
        },
    ]
    recorded = []
    engine = get_engine()
    if engine:
        for item in engine.snap.signal_history[-200:]:
            recorded.append(
                {
                    "name": item.get("signal_id"),
                    "confidence": item.get("confidence") or 0,
                    "adjusted_edge": item.get("net_edge") or 0,
                    "dynamic_min_edge": 0.02,
                    "liquidity_quality": "MEDIUM",
                    "spread_quality": "GOOD",
                    "price_after_5s": _horizon_price(item, 5),
                    "price_after_15s": _horizon_price(item, 15),
                    "price_after_30s": _horizon_price(item, 30),
                    "max_favorable_movement": _horizon_field(item, "max_favorable_movement"),
                    "max_adverse_movement": _horizon_field(item, "max_adverse_movement"),
                }
            )
    return {
        "illustrative": compare_strategy_profiles(illustrative),
        "recorded": compare_strategy_profiles(recorded) if recorded else None,
        "auto_select_enabled": False,
    }
