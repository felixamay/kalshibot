"""Observed success must suggest a bet without a scoreboard, and survive restart."""

from __future__ import annotations

import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.config import Settings
from app.core.enums import ConnectionStatus, SignalType
from app.database import Base
from app.services.signals.engine import SignalEngine
from app.services.signals.patterns import seed_pullback
from app.services.tennis.probability import ProbabilityResult


def settings() -> Settings:
    return Settings(
        _env_file=None,
        initial_observation_seconds=0,
        database_url="sqlite+aiosqlite:///:memory:",
        pattern_engine_enabled=True,
        baseline_volatility_floor=0.75,
        pattern_eval_interval_ms=0,
    )


def _install_estimate(engine: SignalEngine) -> None:
    def fake_estimate(**kwargs):
        return ProbabilityResult(
            player="Player A",
            direction="YES",
            model_win_probability=0.55,
            executable_market_probability=0.5,
            raw_edge=0.05,
            estimated_net_edge=0.04,
            source="market_only",
        )

    engine.prob_model.estimate = fake_estimate  # type: ignore[method-assign]


async def _quote(engine: SignalEngine, ticker: str, *, depth_yes: float = 1600, depth_no: float = 700) -> None:
    for price in (60, 59, 58, 57, 57.4, 58, 58, 58):
        await engine.on_market_update(
            ticker,
            yes_bid=price - 0.5,
            yes_ask=price + 0.5,
            depth_yes=depth_yes,
            depth_no=depth_no,
            imbalance=0.32,
            status="OPEN",
        )


@pytest.mark.asyncio
async def test_three_successes_bet_without_service_games_or_restart_wait():
    engine = SignalEngine(settings())
    engine.snap.connection_status = ConnectionStatus.CONNECTED
    now = time.time() * 1000.0
    ctx = engine.register_match(
        match_id="m1",
        player_a="Player A",
        player_b="Player B",
        tournament="Test",
        market_ticker="KXTEST",
        market_db_id="mk1",
        now_ms=now - 5_000,
    )
    ctx.latest_reasoning = {
        "reliable": False,
        "decision": "WAIT",
        "reason": "Restart: awaiting fresh service-game evidence",
        "pattern_type": "SUPPORT_BOUNCE",
        "pattern_confidence": 10,
        "entry_score": 10,
        "maximum_entry_price": 1,
        "entry_zone_low": 1,
        "entry_zone_high": 1,
        "block_id": "stale-restart",
    }
    _install_estimate(engine)
    for index in range(3):
        seed_pullback(engine.pattern_engine, "KXTEST", now + index, 56, 53, 60, vol=1)
    await _quote(engine, "KXTEST")
    assert ctx.tracker.available is False
    card = engine.dashboard_payload()["matches"][0]
    assert card["display_state"] == "PATTERN_ENTRY_SIGNAL", card["hold_reason"]
    assert "SERVICE-GAME DATA UNAVAILABLE" not in (card["hold_reason"] or "")
    emitted = [
        sig for sig in engine.snap.signals.values() if sig.signal_type == SignalType.PATTERN_ENTRY_SIGNAL
    ]
    assert emitted and emitted[0].is_actionable()


@pytest.mark.asyncio
async def test_no_observed_success_still_waits_for_service_games():
    engine = SignalEngine(settings())
    engine.snap.connection_status = ConnectionStatus.CONNECTED
    now = time.time() * 1000.0
    engine.register_match(
        match_id="m1",
        player_a="Player A",
        player_b="Player B",
        tournament="Test",
        market_ticker="KXTEST",
        market_db_id="mk1",
        now_ms=now - 5_000,
    )
    _install_estimate(engine)
    await _quote(engine, "KXTEST")
    card = engine.dashboard_payload()["matches"][0]
    assert card["display_state"] == "SEARCHING_FOR_ENTRY"
    assert "SERVICE-GAME DATA UNAVAILABLE" in card["hold_reason"]
    assert not engine.dashboard_payload()["actionable_signals"]


@pytest.mark.asyncio
async def test_poor_liquidity_still_blocks_a_qualified_rate():
    engine = SignalEngine(settings())
    engine.snap.connection_status = ConnectionStatus.CONNECTED
    now = time.time() * 1000.0
    engine.register_match(
        match_id="m1",
        player_a="Player A",
        player_b="Player B",
        tournament="Test",
        market_ticker="KXTEST",
        market_db_id="mk1",
        now_ms=now - 5_000,
    )
    _install_estimate(engine)
    for index in range(3):
        seed_pullback(engine.pattern_engine, "KXTEST", now + index, 56, 53, 60, vol=1)
    await _quote(engine, "KXTEST", depth_yes=0, depth_no=0)
    card = engine.dashboard_payload()["matches"][0]
    assert card["display_state"] != "PATTERN_ENTRY_SIGNAL"
    assert not engine.dashboard_payload()["actionable_signals"]


@pytest.mark.asyncio
async def test_pattern_library_survives_restart():
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    engine = SignalEngine(settings(), session_factory=factory)
    engine.snap.connection_status = ConnectionStatus.CONNECTED
    now = time.time() * 1000.0
    engine.register_match(
        match_id="m1",
        player_a="Player A",
        player_b="Player B",
        tournament="Test",
        market_ticker="KXTEST",
        market_db_id="mk1",
        now_ms=now - 5_000,
    )
    _install_estimate(engine)
    for index in range(3):
        seed_pullback(engine.pattern_engine, "KXTEST", now + index, 56, 53, 60, vol=1)
    await _quote(engine, "KXTEST")
    stored = engine.pattern_engine.match_memory["KXTEST"]
    assert len(stored) >= 3
    assert sum(1 for item in stored if item.success_or_failure == "success") >= 3

    restart = SignalEngine(settings(), session_factory=factory)
    restored = restart.register_match(
        match_id="m1",
        player_a="Player A",
        player_b="Player B",
        tournament="Test",
        market_ticker="KXTEST",
        market_db_id="mk1",
        now_ms=now,
    )
    await restart.restore_match_history(restored)
    rows = restart.pattern_engine.match_memory["KXTEST"]
    successes = [item for item in rows if item.pattern_type == "PULLBACK_RECOVERY" and item.success_or_failure == "success"]
    assert len(successes) >= 3
    assert successes[0].ticker == "KXTEST"
    assert successes[0].normalized_pullback > 0
    assert not restored.latest_reasoning or restored.latest_reasoning.get("reliable") is False
    await db.dispose()
