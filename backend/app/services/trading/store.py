"""Load and save per-user strategy rows. Settings are keyed by user_id."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.models import (
    DailyRiskState,
    KalshiOrderRecord,
    PositionEvent,
    StrategyEvent,
    StrategyPosition,
    UserStrategySettings,
)
from app.services.trading.strategy import DEFAULTS, PositionState, StrategyBook, default_settings


SHORT_RUN_KEYS = (
    "short_run_enabled",
    "watch_one_game",
    "watched_ticker",
    "min_buy_score",
    "strong_buy_score",
    "min_sell_score",
    "min_liquidity_growth",
    "max_spread",
    "min_expected_profit_percent",
    "pullback_min_percent",
    "pullback_max_percent",
    "max_entry_price",
)


def settings_from_row(row: UserStrategySettings) -> dict[str, Any]:
    data = default_settings()
    for key in DEFAULTS:
        if key == "selected_tickers" or key in SHORT_RUN_KEYS:
            continue
        if hasattr(row, key):
            data[key] = getattr(row, key)
    try:
        data["selected_tickers"] = json.loads(row.selected_tickers_json or "[]")
    except json.JSONDecodeError:
        data["selected_tickers"] = []
    try:
        extra = json.loads(getattr(row, "short_run_json", None) or "{}")
    except json.JSONDecodeError:
        extra = {}
    if isinstance(extra, dict):
        data.update({key: extra[key] for key in SHORT_RUN_KEYS if key in extra})
    return data


def apply_row(row: UserStrategySettings, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if key == "selected_tickers":
            row.selected_tickers_json = json.dumps(list(value or []))
        elif key in SHORT_RUN_KEYS:
            continue
        elif hasattr(row, key) and key in DEFAULTS:
            setattr(row, key, value)
    current = {}
    try:
        current = json.loads(getattr(row, "short_run_json", None) or "{}")
    except json.JSONDecodeError:
        current = {}
    if not isinstance(current, dict):
        current = {}
    for key in SHORT_RUN_KEYS:
        if key in values:
            current[key] = values[key]
    row.short_run_json = json.dumps(current)


async def load_user(session, user_id: str) -> dict[str, Any]:
    row = await session.get(UserStrategySettings, user_id)
    if row is None:
        row = UserStrategySettings(user_id=user_id)
        session.add(row)
        await session.flush()
    return settings_from_row(row)


async def save_user(session, user_id: str, values: dict[str, Any]) -> dict[str, Any]:
    row = await session.get(UserStrategySettings, user_id)
    if row is None:
        row = UserStrategySettings(user_id=user_id)
        session.add(row)
    apply_row(row, values)
    await session.flush()
    return settings_from_row(row)


async def load_book(session_factory, book: StrategyBook) -> None:
    async with session_factory() as session:
        rows = (await session.execute(select(UserStrategySettings))).scalars()
        for row in rows:
            book.settings[row.user_id] = settings_from_row(row)
        positions = (await session.execute(select(StrategyPosition).where(StrategyPosition.closed_at_ms.is_(None)))).scalars()
        for row in positions:
            book.positions[row.id] = PositionState(
                id=row.id,
                user_id=row.user_id,
                ticker=row.market_ticker,
                label=row.player_label,
                side=row.side,
                status=row.status,
                bet_amount=row.bet_amount,
                requested_price=row.requested_price,
                entry_price=row.entry_price,
                filled_qty=row.filled_qty,
                fees=row.fees,
                peak_price=row.peak_price,
                current_exit_price=row.current_exit_price,
                trailing_active=row.trailing_active,
                rules=json.loads(row.rules_json or "{}"),
                opened_at_ms=row.opened_at_ms,
                closed_at_ms=row.closed_at_ms,
                close_price=row.close_price,
                realized_pnl=row.realized_pnl,
            )
        risk_rows = (await session.execute(select(DailyRiskState))).scalars()
        for risk in risk_rows:
            book.daily_loss[risk.user_id] = risk.realized_loss
            book.daily_exposure[risk.user_id] = risk.exposure


async def save_book(session_factory, book: StrategyBook) -> None:
    async with session_factory() as session:
        for user_id, values in book.settings.items():
            await save_user(session, user_id, values)
        for position in book.positions.values():
            await session.merge(
                StrategyPosition(
                    id=position.id,
                    user_id=position.user_id,
                    market_ticker=position.ticker,
                    player_label=position.label,
                    side=position.side,
                    status=position.status,
                    bet_amount=position.bet_amount,
                    requested_price=position.requested_price,
                    entry_price=position.entry_price,
                    filled_qty=position.filled_qty,
                    fees=position.fees,
                    rules_json=json.dumps(position.rules),
                    peak_price=position.peak_price,
                    current_exit_price=position.current_exit_price,
                    trailing_active=position.trailing_active,
                    opened_at_ms=position.opened_at_ms,
                    closed_at_ms=position.closed_at_ms,
                    close_price=position.close_price,
                    realized_pnl=position.realized_pnl,
                )
            )
            if position.order_id:
                await session.merge(
                    KalshiOrderRecord(
                        id=position.order_id,
                        user_id=position.user_id,
                        position_id=position.id,
                        market_ticker=position.ticker,
                        action="sell" if position.closed_at_ms else "buy",
                        side=position.side,
                        requested_price=position.requested_price,
                        filled_price=position.entry_price or None,
                        requested_qty=position.filled_qty,
                        filled_qty=position.filled_qty,
                        fees=position.fees,
                        kalshi_order_id=position.order_id,
                        status=position.status,
                        created_at_ms=position.opened_at_ms,
                    )
                )
        for event in book.events:
            session.add(
                StrategyEvent(
                    user_id=event["user_id"],
                    market_ticker=event.get("market_ticker") or "",
                    kind=event["kind"],
                    detail=event.get("detail") or "",
                    created_at_ms=0,
                )
            )
            if event.get("position_id"):
                session.add(
                    PositionEvent(
                        position_id=event["position_id"],
                        user_id=event["user_id"],
                        kind=event["kind"],
                        detail=event.get("detail") or "",
                        created_at_ms=0,
                    )
                )
        book.events.clear()
        day = datetime.now(timezone.utc).date().isoformat()
        for user_id in set(book.daily_loss) | set(book.daily_exposure):
            await session.merge(
                DailyRiskState(
                    id=f"{user_id}:{day}",
                    user_id=user_id,
                    day=day,
                    realized_loss=book.daily_loss.get(user_id, 0),
                    exposure=book.daily_exposure.get(user_id, 0),
                )
            )
        await session.commit()


async def recent_events(session, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(StrategyEvent).where(StrategyEvent.user_id == user_id).order_by(StrategyEvent.id.desc()).limit(limit)
        )
    ).scalars()
    return [
        {"id": row.id, "market_ticker": row.market_ticker, "kind": row.kind, "detail": row.detail}
        for row in rows
    ]
