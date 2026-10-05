"""Per-user Kalshi strategy settings and position monitor."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.services.auth.security import get_current_user, get_optional_user
from app.services.trading.store import load_user, recent_events, save_user
from app.services.trading.strategy import DEFAULTS, OrderReceipt, OrderRequest

router = APIRouter()


class StrategyUpdate(BaseModel):
    auto_entry: Optional[bool] = None
    auto_exit: Optional[bool] = None
    paused: Optional[bool] = None
    bet_amount: Optional[float] = Field(default=None, gt=0)
    entry_type: Optional[str] = None
    entry_price: Optional[float] = Field(default=None, gt=0, le=99)
    take_profit_percent: Optional[float] = Field(default=None, gt=0)
    stop_loss_percent: Optional[float] = Field(default=None, gt=0)
    trailing_stop_enabled: Optional[bool] = None
    trailing_activation_percent: Optional[float] = Field(default=None, gt=0)
    trailing_stop_percent: Optional[float] = Field(default=None, gt=0)
    max_trades_per_match: Optional[int] = Field(default=None, ge=1)
    cooldown_seconds: Optional[int] = Field(default=None, ge=0)
    max_position_per_match: Optional[float] = Field(default=None, gt=0)
    max_daily_loss: Optional[float] = Field(default=None, gt=0)
    max_daily_exposure: Optional[float] = Field(default=None, gt=0)
    market_scope: Optional[str] = None
    selected_tickers: Optional[list[str]] = None
    one_direction_per_market: Optional[bool] = None
    short_run_enabled: Optional[bool] = None
    watch_one_game: Optional[bool] = None
    watched_ticker: Optional[str] = None
    min_buy_score: Optional[float] = Field(default=None, ge=0, le=100)
    strong_buy_score: Optional[float] = Field(default=None, ge=0, le=100)
    min_sell_score: Optional[float] = Field(default=None, ge=0, le=100)
    min_liquidity_growth: Optional[float] = Field(default=None, ge=0)
    max_spread: Optional[float] = Field(default=None, gt=0)
    min_expected_profit_percent: Optional[float] = Field(default=None, ge=0)
    pullback_min_percent: Optional[float] = Field(default=None, ge=0)
    pullback_max_percent: Optional[float] = Field(default=None, gt=0)
    max_entry_price: Optional[float] = Field(default=None, gt=0, le=99)
    apply_to_open: Optional[bool] = None


class CloseAllRequest(BaseModel):
    confirm: bool = False


def get_runner():
    from app.main import strategy_runner

    if strategy_runner is None:
        raise HTTPException(503, "Strategy runner is not ready")
    return strategy_runner


def get_engine():
    from app.main import engine

    return engine


@router.get("/strategy/settings")
async def read_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    runner = get_runner()
    stored = await load_user(db, user.id)
    runner.book.settings[user.id] = stored
    return {"user_id": user.id, "settings": stored}


@router.put("/strategy/settings")
async def write_settings(
    body: StrategyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    runner = get_runner()
    current = await load_user(db, user.id)
    runner.book.settings[user.id] = current
    incoming = {key: value for key, value in body.model_dump().items() if value is not None and key != "apply_to_open"}
    if incoming.get("entry_type"):
        incoming["entry_type"] = str(incoming["entry_type"]).upper()
        if incoming["entry_type"] not in {"YES", "NO", "EITHER"}:
            raise HTTPException(400, "Entry type must be YES, NO, or EITHER")
    if incoming.get("market_scope"):
        incoming["market_scope"] = str(incoming["market_scope"]).upper()
        if incoming["market_scope"] not in {"ALL_LIVE", "SELECTED"}:
            raise HTTPException(400, "Market selection must be ALL_LIVE or SELECTED")
    merged = dict(current)
    merged.update(incoming)
    result = runner.book.replace_settings(user.id, merged, body.apply_to_open)
    if result["needs_confirmation"]:
        raise HTTPException(409, result)
    saved = await save_user(db, user.id, runner.book.settings[user.id])
    return {"user_id": user.id, "settings": saved, "needs_confirmation": False}


@router.post("/strategy/reset")
async def reset_settings(
    body: StrategyUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    runner = get_runner()
    current = await load_user(db, user.id)
    runner.book.settings[user.id] = current
    result = runner.book.replace_settings(user.id, dict(DEFAULTS), body.apply_to_open)
    if result["needs_confirmation"]:
        raise HTTPException(409, result)
    saved = await save_user(db, user.id, runner.book.settings[user.id])
    return {"user_id": user.id, "settings": saved}


@router.post("/strategy/pause")
async def pause_strategy(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _flag(user.id, db, paused=True)


@router.post("/strategy/resume")
async def resume_strategy(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _flag(user.id, db, paused=False)


@router.post("/strategy/stop")
async def stop_auto_trading(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _flag(user.id, db, emergency_stop=True, auto_entry=False)


@router.post("/strategy/close-all")
async def close_all(
    body: CloseAllRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.confirm is not True:
        raise HTTPException(400, "CLOSE ALL POSITIONS requires confirm=true")
    runner = get_runner()
    if not runner.book.trading_connected:
        raise HTTPException(409, "Trading API is disconnected. No orders were sent.")
    closed = 0
    for position in list(runner.book.open_positions(user.id)):
        if position.filled_qty <= 0:
            continue
        price = position.current_exit_price or position.entry_price
        request = OrderRequest(user.id, position.ticker, "sell", position.side, price, max(1, int(position.filled_qty)), position.id)
        receipt = await runner.gateway.submit(request)
        runner.book.apply_receipt(position.id, receipt, closing=True, now_ms=position.opened_at_ms)
        if isinstance(receipt, OrderReceipt) and receipt.ok and receipt.filled_qty > 0:
            closed += 1
    await runner.persist()
    return {"closed": closed}


@router.get("/strategy/positions")
async def positions(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    runner = get_runner()
    rows = [row.public() for row in runner.book.positions.values() if row.user_id == user.id and row.closed_at_ms is None]
    events = await recent_events(db, user.id)
    reasons = [
        {"market_ticker": ticker, "detail": detail}
        for (owner, ticker), detail in runner.book.last_reason.items()
        if owner == user.id
    ]
    signals = [dict(signal, market_ticker=ticker) for (owner, ticker), signal in runner.book.signals.items() if owner == user.id]
    return {"positions": rows, "events": events, "reasons": reasons, "signals": signals}


@router.get("/strategy/status")
async def status(user: Optional[User] = Depends(get_optional_user)) -> dict[str, Any]:
    runner = get_runner()
    engine = get_engine()
    market = "DISCONNECTED"
    if engine is not None and engine.snap.connection_status.value == "CONNECTED":
        market = "CONNECTED"
    settings = runner.book.settings.get(user.id) if user else {}
    live_matches = []
    if engine is not None:
        import time

        from app.services.signals.engine import match_is_live

        now = time.time() * 1000.0
        for ctx in engine.snap.matches.values():
            if match_is_live(ctx, now):
                live_matches.append({"ticker": ctx.market_ticker, "label": f"{ctx.player_a} vs {ctx.player_b}"})
    signal = None
    watching = None
    if user:
        watched = settings.get("watched_ticker")
        for (owner, ticker), row in runner.book.signals.items():
            if owner == user.id and (not watched or ticker == watched):
                signal = dict(row, market_ticker=ticker)
                break
        if settings.get("watch_one_game") and watched:
            match = next((item for item in live_matches if item["ticker"] == watched), None)
            watching = match["label"] if match else watched
    return {
        "market_data": "CONNECTED" if market == "CONNECTED" else "DISCONNECTED",
        "trading_api": "CONNECTED" if runner.book.trading_connected else "DISCONNECTED",
        "auto_entry": bool(settings.get("auto_entry")),
        "auto_exit": bool(settings.get("auto_exit")),
        "paused": bool(settings.get("paused")),
        "emergency_stop": bool(settings.get("emergency_stop")),
        "live_matches": live_matches,
        "watching": watching,
        "signal": signal,
    }


async def _flag(user_id: str, db: AsyncSession, **flags: Any) -> dict[str, Any]:
    runner = get_runner()
    current = await load_user(db, user_id)
    current.update(flags)
    runner.book.settings[user_id] = current
    saved = await save_user(db, user_id, current)
    return {"user_id": user_id, "settings": saved}
