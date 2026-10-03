"""Pattern suggestions follow the live match stage, not a 5-minute clock."""

from __future__ import annotations

import time

import pytest

from app.core.enums import ConnectionStatus, SignalType
from app.services.signals.engine import SignalEngine
from app.services.signals.patterns import seed_pullback
from app.services.tennis.probability import ProbabilityResult
from app.services.tennis.provider import TennisLiveState
from app.services.tennis.stage import pattern_serve_block, serve_fingerprint, serves_added, suggestion_block
from tests.test_patterns import settings


def _score(**kwargs) -> TennisLiveState:
    return TennisLiveState(
        match_external_id="m",
        player_a="Player A",
        player_b="Player B",
        available=True,
        source="test",
        **kwargs,
    )


def test_price_past_85_blocks_and_an_even_price_does_not():
    assert suggestion_block(None, 90) is not None
    assert "85" in suggestion_block(None, 90)
    assert suggestion_block(None, 12) is not None
    assert suggestion_block(None, 85) is None
    assert suggestion_block(None, 58) is None
    assert suggestion_block(None, 0) is None


def test_explicit_game_percent_blocks_above_85():
    late = _score(game_percent=86, point_score="15-0", game_score="1-0", match_score="0-0")
    assert "85" in (suggestion_block(late, 55) or "")
    early = _score(game_percent=70, point_score="15-0", game_score="1-0", match_score="0-0")
    assert suggestion_block(early, 55) is None


def test_before_first_serve_blocks_and_the_first_point_opens_the_window():
    waiting = _score(match_score="0-0", game_score="0-0", point_score="0-0")
    reason = suggestion_block(waiting, 55) or ""
    assert "first serve" in reason
    started = _score(match_score="0-0", game_score="0-0", point_score="15-0")
    assert suggestion_block(started, 55) is None


def test_last_five_serves_block_only_when_the_winner_is_clear():
    clear = _score(match_score="1-0", game_score="5-0", point_score="40-0")
    reason = suggestion_block(clear, 70) or ""
    assert "last 5 serves" in reason
    tight = _score(match_score="1-0", game_score="5-4", point_score="30-30")
    assert suggestion_block(tight, 60) is None


def test_no_score_feed_uses_the_price_and_treats_a_live_match_as_started():
    assert suggestion_block(None, 60) is None
    assert suggestion_block(
        TennisLiveState("m", "A", "B", available=False),
        60,
    ) is None


@pytest.mark.asyncio
async def test_engine_does_not_suggest_outside_the_early_window():
    engine = _engine()
    seed_pullback(engine.pattern_engine, "KXTEST", time.time() * 1000, 56, 53, 60, vol=1)

    await _play(engine, (90, 89, 88, 87, 87.4, 88))
    card = engine.dashboard_payload()["matches"][0]
    assert engine.dashboard_payload()["actionable_signals"] == []
    assert "85" in card["hold_reason"]

    engine.snap.matches["KXTEST"].tennis = _score(
        match_score="0-0", game_score="0-0", point_score="0-0"
    )
    await _play(engine, (60, 59, 58, 57, 57.4, 58))
    card = engine.dashboard_payload()["matches"][0]
    assert engine.dashboard_payload()["actionable_signals"] == []
    assert "first serve" in card["hold_reason"]

    engine.snap.matches["KXTEST"].tennis = _score(
        match_score="1-0", game_score="5-0", point_score="40-0"
    )
    await _play(engine, (60, 59, 58, 57, 57.4, 58))
    card = engine.dashboard_payload()["matches"][0]
    assert engine.dashboard_payload()["actionable_signals"] == []
    assert "last 5 serves" in card["hold_reason"]


@pytest.mark.asyncio
async def test_a_discovered_pattern_is_suggested_in_the_early_window():
    engine = _engine()
    fresh = _engine()
    path = (60, 59, 58, 57, 57.4, 58)
    await _play(fresh, path)
    assert fresh.dashboard_payload()["actionable_signals"] == []

    seed_pullback(engine.pattern_engine, "KXTEST", time.time() * 1000, 56, 53, 60, vol=1)
    ctx = engine.snap.matches["KXTEST"]
    ctx.tennis = _score(match_score="1-0", game_score="5-4", point_score="15-0")
    engine.note_serves(ctx)
    ctx.tennis = _score(match_score="1-0", game_score="5-4", point_score="30-0")
    engine.note_serves(ctx)
    ctx.tennis = _score(match_score="1-0", game_score="5-4", point_score="30-15")
    engine.note_serves(ctx)
    assert ctx.serves_seen == 2
    await _play(engine, path)
    payload = engine.dashboard_payload()
    assert payload["actionable_signals"], payload["matches"][0]["hold_reason"]
    assert payload["matches"][0]["display_state"] in (
        "PATTERN_ENTRY_SIGNAL",
        "STRONG_PATTERN_SIGNAL",
    )
    assert payload["actionable_signals"][0]["signal_type"] in (
        SignalType.PATTERN_ENTRY_SIGNAL.value,
        SignalType.STRONG_PATTERN_SIGNAL.value,
    )


def test_a_pattern_waits_for_every_two_serves():
    assert pattern_serve_block(0, 0)
    assert pattern_serve_block(1, 0)
    assert pattern_serve_block(2, 0) is None
    assert pattern_serve_block(3, 2)
    assert pattern_serve_block(4, 2) is None

    first = serve_fingerprint(_score(game_score="1-0", set_score="6-3 1-0"))
    primed = serve_fingerprint(_score(game_score="1-0", set_score="6-3 1-0"))
    assert serves_added(first, primed, False) == (0, False)
    nxt = serve_fingerprint(_score(game_score="2-0", set_score="6-3 2-0"))
    added, visible = serves_added(first, nxt, False)
    assert added == 4
    assert visible is False
    point = serve_fingerprint(_score(game_score="2-0", set_score="6-3 2-0", point_score="15-0"))
    added, visible = serves_added(nxt, point, False)
    assert added == 1
    assert visible is True


@pytest.mark.asyncio
async def test_live_score_does_not_make_a_pattern_before_two_serves():
    engine = _engine()
    seed_pullback(engine.pattern_engine, "KXTEST", time.time() * 1000, 56, 53, 60, vol=1)
    ctx = engine.snap.matches["KXTEST"]
    ctx.tennis = _score(match_score="1-0", game_score="3-2", point_score="15-0")
    engine.note_serves(ctx)
    ctx.tennis = _score(match_score="1-0", game_score="3-2", point_score="30-0")
    engine.note_serves(ctx)
    assert ctx.serves_seen == 1
    await _play(engine, (60, 59, 58, 57, 57.4, 58))
    card = engine.dashboard_payload()["matches"][0]
    assert engine.dashboard_payload()["actionable_signals"] == []
    assert "two serves" in card["hold_reason"]


def _engine() -> SignalEngine:
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
        now_ms=now,
    )

    def fake_estimate(**kwargs):
        return ProbabilityResult(
            player="Player A",
            direction="YES",
            model_win_probability=0.55,
            executable_market_probability=0.5,
            raw_edge=0.05,
            estimated_net_edge=0.04,
            source="tennis_enhanced",
        )

    engine.prob_model.estimate = fake_estimate  # type: ignore[method-assign]
    return engine


async def _play(engine: SignalEngine, prices: tuple[float, ...]) -> None:
    for price in prices:
        await engine.on_market_update(
            "KXTEST",
            yes_bid=price - 0.5,
            yes_ask=price + 0.5,
            depth_yes=1600,
            depth_no=700,
            imbalance=0.32,
            status="OPEN",
        )
