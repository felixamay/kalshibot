"""Pattern learning: repetition, confirmation, and late-move protection."""

from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.core.enums import ConnectionStatus, SignalStatus, SignalType
from app.database import Base
from app.services.market.orchestrator import occurrence_start_ms
from app.services.signals.engine import SignalEngine
from app.services.signals.live_signal import LiveSignal
from app.services.signals.patterns import (
    BookSnapshot,
    PatternAssessment,
    PatternEngine,
    combine_exit_decision,
    make_point,
    normalized_move,
    pattern_similarity_score,
    performance_report,
    repetition_label,
    resistance_zones,
    seed_pullback,
    support_zones,
    time_decay_score,
    bet_behind_copy,
)
from app.services.tennis.probability import ProbabilityResult


def settings() -> Settings:
    return Settings(
        _env_file=None,
        initial_observation_seconds=0,
        database_url="sqlite+aiosqlite:///:memory:",
        pattern_engine_enabled=True,
        baseline_volatility_floor=0.75,
        # Production skips rescoring for 500ms so login stays responsive.
        # These tests send a whole path in one turn and need every tick scored.
        pattern_eval_interval_ms=0,
    )


def early_book(price: float) -> BookSnapshot:
    return BookSnapshot(
        price=price,
        spread=1.0,
        imbalance=0.32,
        depth_bid=1400,
        depth_ask=700,
        momentum=1.1,
        acceleration=0.4,
        trade_velocity=1.2,
        liquidity_quality="GOOD",
        spread_quality="EXCELLENT",
        fresh=True,
        adjusted_edge=0.04,
        microprice=price + 0.15,
    )


def developing_pullback(start: float = 60, low: float = 57, current: float = 58) -> list:
    prices = [start, start - 1, start - 2, low, low + 0.4, current]
    return [make_point(i * 1000, price) for i, price in enumerate(prices)]


def test_bet_behind_names_the_player_or_the_market():
    market_headline, market_detail = bet_behind_copy("Osaka", "YES", "PULLBACK_RECOVERY")
    assert market_headline == "Bet YES on Osaka now"
    assert "market" in market_detail.lower()
    player_headline, player_detail = bet_behind_copy("Osaka", "YES", "SERVE_CHANGE")
    assert player_headline == "Bet YES on Osaka now"
    assert "player" in player_detail.lower()
    against_headline, against_detail = bet_behind_copy("Osaka", "NO", "REVERSAL")
    assert against_headline == "Bet NO on Osaka now"
    assert "market" in against_detail.lower()


def test_normalized_move_uses_match_volatility():
    assert normalized_move(4, 1) == pytest.approx(4.0)
    assert normalized_move(4, 5) == pytest.approx(0.8)
    assert normalized_move(4, 1) > normalized_move(4, 5)


def test_similarity_compares_normalized_shape_not_raw_cents():
    same = pattern_similarity_score(4, 4, 6, 6)
    different_vol = pattern_similarity_score(4, 0.8, 6, 1.2)
    assert same > 85
    assert same > different_vol + 15


def test_two_occurrences_are_potential_and_three_are_recurring():
    assert repetition_label(1, 1, 2) == "OBSERVED ONLY"
    assert repetition_label(2, 2, 2) == "POTENTIAL"
    assert repetition_label(3, 3, 2) == "RECURRING"
    engine = PatternEngine(settings())
    for index in range(2):
        seed_pullback(engine, "KX", 1_000 + index, 52, 49, 55, vol=1)
    view = engine.assess(
        ticker="KX",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert view.repetition_label == "POTENTIAL"
    seed_pullback(engine, "KX", 9_000, 56, 53, 60, vol=1)
    again = engine.assess(
        ticker="KX",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert again.repetition_label == "RECURRING"
    assert again.occurrences == 3


def test_three_successful_observations_are_bet_eligible():
    """The same pattern becomes a bet after 3 successes in this match, not 4."""
    engine = PatternEngine(settings())
    for index in range(3):
        seed_pullback(engine, "KX", 1_000 + index, 56, 53, 60, vol=1)
    view = engine.assess(
        ticker="KX",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Osaka",
        player_b="Swiatek",
    )
    assert view.successes >= 3
    assert view.decision in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL"), view.explanation
    assert view.tradeable is True
    assert view.player == "Osaka"
    assert "Osaka" in view.explanation

    two = PatternEngine(settings())
    for index in range(2):
        seed_pullback(two, "KX", 1_000 + index, 56, 53, 60, vol=1)
    waiting = two.assess(
        ticker="KX",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Osaka",
    )
    assert waiting.successes < 3
    assert waiting.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL"), waiting.explanation


def test_three_successes_stay_blocked_when_the_setup_is_invalid():
    engine = PatternEngine(settings())
    book = early_book(58)
    shared = dict(
        pattern_type="PULLBACK_RECOVERY",
        label="OBSERVED ONLY",
        stage="EARLY",
        progress=20,
        confidence=40,
        entry_score=40,
        confirmation_count=0,
        confirming_signals=0,
        evidence=[],
        discovered=False,
        observed_success_count=3,
    )
    eligible, tradeable, blockers, _reasons = engine._decide(book=book, **shared)
    assert eligible == "PATTERN_ENTRY_SIGNAL"
    assert tradeable is True
    assert blockers == []

    stale = BookSnapshot(
        price=58, spread=1, imbalance=0.3, depth_bid=1400, depth_ask=700,
        momentum=1, liquidity_quality="GOOD", spread_quality="EXCELLENT", fresh=False,
    )
    decision, tradeable, blockers, _reasons = engine._decide(book=stale, **shared)
    assert decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")
    assert tradeable is False
    assert any("stale" in item.lower() for item in blockers)

    thin = BookSnapshot(
        price=58, spread=1, imbalance=0.3, depth_bid=0, depth_ask=0,
        momentum=1, liquidity_quality="VERY_LOW", spread_quality="EXCELLENT", fresh=True,
    )
    decision, tradeable, blockers, _reasons = engine._decide(book=thin, **shared)
    assert decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")
    assert any("Liquidity" in item for item in blockers)

    against = BookSnapshot(
        price=58, spread=1, imbalance=-0.4, depth_bid=1400, depth_ask=700,
        momentum=-1.2, liquidity_quality="GOOD", spread_quality="EXCELLENT", fresh=True,
    )
    decision, tradeable, blockers, _reasons = engine._decide(book=against, **shared)
    assert decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")
    assert any("contradicts" in item.lower() for item in blockers)

    late, tradeable, _blockers, _reasons = engine._decide(book=book, **{**shared, "stage": "LATE", "progress": 80})
    assert late == "PATTERN_ALREADY_ADVANCED"
    assert tradeable is False

    broken, tradeable, _blockers, _reasons = engine._decide(
        book=book, **{**shared, "pattern_type": "FAILED_BREAKOUT"}
    )
    assert broken == "FAILED_BREAKOUT"
    assert tradeable is False

    fourth_not_required, tradeable, _blockers, _reasons = engine._decide(
        book=book, **{**shared, "observed_success_count": 3}
    )
    assert fourth_not_required == "PATTERN_ENTRY_SIGNAL"
    assert tradeable is True


def test_occurrence_time_is_the_match_start():
    assert occurrence_start_ms({}) is None
    assert occurrence_start_ms({"occurrence_datetime": ""}) is None
    start = occurrence_start_ms({"occurrence_datetime": "2026-10-04T14:00:00Z"})
    assert start == 1791122400000.0


@pytest.mark.asyncio
async def test_a_match_that_has_not_started_is_not_a_bet():
    s = settings()
    engine = SignalEngine(s)
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
        scheduled_start_ms=now + 3_600_000,
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
    seed_pullback(engine.pattern_engine, "KXTEST", now, 56, 53, 60, vol=1)
    for price in (60, 59, 58, 57, 57.4, 58):
        await engine.on_market_update(
            "KXTEST",
            yes_bid=price - 0.5,
            yes_ask=price + 0.5,
            depth_yes=1600,
            depth_no=700,
            imbalance=0.32,
            status="OPEN",
        )
    engine.register_match(
        match_id="m2",
        player_a="Live A",
        player_b="Live B",
        tournament="Test",
        market_ticker="KXLIVE",
        market_db_id="mk2",
        now_ms=now,
        scheduled_start_ms=now - 60_000,
    )
    engine.register_match(
        match_id="m3",
        player_a="Old A",
        player_b="Old B",
        tournament="Test",
        market_ticker="KXOLD",
        market_db_id="mk3",
        now_ms=now,
        scheduled_start_ms=now - (7 * 60 * 60 * 1000),
    )
    payload = engine.dashboard_payload()
    assert payload["actionable_signals"] == []
    assert [m["market_ticker"] for m in payload["matches"]] == ["KXLIVE"]
    assert payload["live_match_count"] == 1
    assert payload["no_live_markets"] is False


def test_low_confidence_early_pattern_waits_for_confirmation():
    """Low confidence blocks entry even when a pullback is developing."""
    engine = PatternEngine(settings())
    seed_pullback(engine, "KX", 1_000, 52, 49, 55, vol=1)
    once = engine.assess(
        ticker="KX",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert once.pattern_type == "PULLBACK_RECOVERY"
    assert once.stage in ("EARLY", "DEVELOPING")
    assert once.decision in ("PATTERN_WATCH", "PATTERN_DEVELOPING"), once.explanation
    assert once.tradeable is False
    assert once.confirmation_needed == 3
    assert once.reasons
    assert "wait" in once.explanation.lower()
    assert "confirmation" not in once.explanation.lower()

    fresh = PatternEngine(settings())
    forming = fresh.assess(
        ticker="NEW",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert forming.pattern_type == "PULLBACK_RECOVERY"
    assert forming.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL"), forming.explanation
    assert forming.tradeable is False
    assert forming.reasons
    assert "confirmation" not in forming.explanation.lower()


def test_zero_printed_depth_blocks_entry():
    """Unknown depth cannot confirm an executable entry."""
    engine = PatternEngine(settings())
    seed_pullback(engine, "KXATPMATCH-26OCT02CERMEN-CER", 1_000, 52, 49, 55, vol=1)
    book = BookSnapshot(
        price=58,
        spread=1.0,
        imbalance=0.1,
        depth_bid=0.0,
        depth_ask=0.0,
        momentum=0.4,
        liquidity_quality="VERY_LOW",
        spread_quality="EXCELLENT",
        fresh=True,
        extreme=False,
        adjusted_edge=0.02,
        microprice=58.2,
    )
    view = engine.assess(
        ticker="KXATPMATCH-26OCT02CERMEN-CER",
        points=developing_pullback(),
        book=book,
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Francisco Cerundolo",
        player_b="Jakub Mensik",
    )
    assert view.stage in ("EARLY", "DEVELOPING")
    assert view.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL"), view.explanation
    assert view.tradeable is False
    assert any("Liquidity" in blocker for blocker in view.blockers)
    assert "Bet YES" not in view.explanation

    wide = BookSnapshot(
        price=58,
        spread=8.0,
        depth_bid=0.0,
        depth_ask=0.0,
        liquidity_quality="VERY_LOW",
        spread_quality="VERY_POOR",
        fresh=True,
        extreme=False,
    )
    blocked = engine.assess(
        ticker="KXATPMATCH-26OCT02CERMEN-CER",
        points=developing_pullback(),
        book=wide,
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Francisco Cerundolo",
    )
    assert blocked.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")


def test_late_and_completed_patterns_do_not_trigger_entry():
    engine = PatternEngine(settings())
    for index in range(3):
        seed_pullback(engine, "KX", 1_000 + index, 56, 50, 56, vol=1)
    late_prices = [56, 54, 52, 50, 53, 55.5]
    late_points = [make_point(i * 1000, price) for i, price in enumerate(late_prices)]
    late = engine.assess(
        ticker="KX",
        points=late_points,
        book=early_book(55.5),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert late.progress >= 75
    assert late.decision == "PATTERN_ALREADY_ADVANCED"
    done_prices = [56, 54, 52, 50, 54, 57]
    done_points = [make_point(i * 1000, price) for i, price in enumerate(done_prices)]
    done = engine.assess(
        ticker="KX",
        points=done_points,
        book=early_book(57),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert done.progress >= 95
    assert done.decision == "PATTERN_ALREADY_ADVANCED"


def test_support_and_resistance_are_zones():
    support = support_zones([49, 50, 48, 49.5], width=2)
    assert support
    assert support[0]["low"] <= 49 <= support[0]["high"]
    assert support[0]["touches"] >= 3
    assert support[0]["high"] - support[0]["low"] > 0.5
    resistance = resistance_zones([60, 61, 60.5, 59.5], width=2)
    assert resistance
    assert resistance[0]["low"] <= 60 <= resistance[0]["high"]
    assert resistance[0]["touches"] >= 3


def test_pullback_recovery_breakout_and_failed_breakout():
    engine = PatternEngine(settings())
    completed = [52, 51, 50, 49, 50, 52, 55, 53]
    points = [make_point(i * 1000, price, imbalance=0.2 if price >= 52 else -0.05) for i, price in enumerate(completed)]
    engine.assess(
        ticker="KX",
        points=points,
        book=early_book(53),
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    stored = engine.match_memory["KX"]
    assert any(item.pattern_type == "PULLBACK_RECOVERY" for item in stored)

    breakout_prices = [50, 60, 54, 60, 55, 60, 54, 63]
    breakout_points = [make_point(i * 1000, price, imbalance=0.35) for i, price in enumerate(breakout_prices)]
    breaking = engine.assess(
        ticker="BRK",
        points=breakout_points,
        book=early_book(63),
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert breaking.pattern_type == "BREAKOUT"
    assert breaking.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")

    failed_prices = [50, 60, 54, 60, 55, 60, 54, 63, 57]
    failed_points = [make_point(i * 1000, price) for i, price in enumerate(failed_prices)]
    failed = engine.assess(
        ticker="FAIL",
        points=failed_points,
        book=early_book(57),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert failed.pattern_type == "FAILED_BREAKOUT"
    assert failed.decision == "FAILED_BREAKOUT"
    assert failed.tradeable is False

    # The same break that then bounces is a pullback, not a dead breakout.
    bounced_prices = [50, 60, 54, 60, 55, 60, 54, 63, 57, 59]
    bounced = engine.assess(
        ticker="BOUNCE",
        points=[make_point(i * 1000, price) for i, price in enumerate(bounced_prices)],
        book=early_book(59),
        confirmation_count=0,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert bounced.pattern_type == "PULLBACK_RECOVERY", bounced.explanation


def test_order_book_pattern_cannot_open_an_entry():
    engine = PatternEngine(settings())
    points = []
    for index, imbalance in enumerate((0.4, 0.45, 0.5, -0.3, -0.4)):
        points.append(make_point(index * 1000, 50, imbalance=imbalance, depth_bid=2000 if imbalance > 0 else 100))
    view = engine.assess(
        ticker="BOOK",
        points=points,
        book=BookSnapshot(
            price=50,
            spread=1,
            imbalance=-0.4,
            depth_bid=80,
            depth_ask=900,
            liquidity_quality="GOOD",
            spread_quality="EXCELLENT",
            fresh=True,
            microprice=49.6,
        ),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert view.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")
    assert view.tradeable is False
    assert view.orderbook_evidence


def test_pattern_health_and_slip_combine_for_exit():
    s = settings()
    assert combine_exit_decision(
        pattern_health=24,
        pattern_broken=True,
        slip_decision="SLIPPING",
        slip_score=81,
        emergency=False,
        settings=s,
    ) == "STOP_EXIT_SIGNAL"
    assert combine_exit_decision(
        pattern_health=87,
        pattern_broken=False,
        slip_decision="HOLD",
        slip_score=20,
        emergency=False,
        settings=s,
    ) == "PATTERN_HEALTHY"
    assert combine_exit_decision(
        pattern_health=58,
        pattern_broken=False,
        slip_decision="HOLD",
        slip_score=30,
        emergency=False,
        settings=s,
    ) == "PATTERN_WEAKENING"
    assert combine_exit_decision(
        pattern_health=36,
        pattern_broken=False,
        slip_decision="WATCH_CLOSELY",
        slip_score=67,
        emergency=False,
        settings=s,
    ) == "PATTERN_AT_RISK"
    assert time_decay_score(32_000, 15_000, progress=10) > time_decay_score(10_000, 15_000, progress=10)


def test_pattern_memory_does_not_grow_without_limit():
    """A noisy match used to store a pattern per swing and stall login."""
    engine = PatternEngine(settings())
    book = early_book(50)
    points = [
        make_point(i * 1000.0, 50 + ((i * 17) % 11) - 5, depth_bid=800, depth_ask=600)
        for i in range(400)
    ]
    for slide in range(30):
        window = points[slide:] + [
            make_point((400 + slide) * 1000.0, 48 + (slide % 7), depth_bid=800, depth_ask=600)
        ]
        engine.assess(
            ticker="T",
            points=window[-400:],
            book=book,
            now_ms=window[-1].ts_ms,
        )
    assert len(engine.match_memory["T"]) <= 40
    assert len(engine.historical) <= 400


def test_pattern_tables_and_backtest_do_not_auto_select():
    import app.models  # noqa: F401

    names = set(Base.metadata.tables)
    for table in (
        "pattern_definitions",
        "pattern_instances",
        "pattern_features",
        "pattern_results",
        "pattern_similarity",
        "pattern_performance",
    ):
        assert table in names
    report = performance_report(PatternEngine(settings()))
    assert report["auto_select_enabled"] is False
    assert report["order_placement_enabled"] is False


@pytest.mark.asyncio
async def test_engine_emits_pattern_entry_after_repeated_pullbacks():
    s = settings()
    engine = SignalEngine(s)
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
    for index in range(3):
        seed_pullback(engine.pattern_engine, "KXTEST", now + index, 56, 53, 60, vol=1)
    path = [60, 59, 58, 57, 57.4, 58, 58, 58]
    for price in path:
        await engine.on_market_update(
            "KXTEST",
            yes_bid=price - 0.5,
            yes_ask=price + 0.5,
            depth_yes=1600,
            depth_no=700,
            imbalance=0.32,
            status="OPEN",
        )
    emitted = [sig for sig in engine.snap.signals.values() if sig.signal_type == SignalType.PATTERN_ENTRY_SIGNAL]
    assert emitted, engine.dashboard_payload()["matches"][0]["hold_reason"]
    assert emitted[0].is_actionable()
    assert emitted[0].pattern_name == "Pullback + Recovery"
    assert emitted[0].maximum_entry_price >= emitted[0].market_price
    assert emitted[0].expires_at_ms > emitted[0].created_at_ms
    assert s.min_signal_ttl_seconds * 1000 <= emitted[0].original_ttl_ms <= s.max_signal_ttl_seconds * 1000
    assert emitted[0].maximum_entry_price <= emitted[0].market_price + s.max_entry_slippage_cents


@pytest.mark.asyncio
async def test_pattern_failure_cancels_before_countdown():
    s = settings()
    engine = SignalEngine(s)
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
    sig = LiveSignal(
        signal_id="SIG-HOLD",
        signal_version=1,
        match_id="m1",
        market_id="mk1",
        market_ticker="KXTEST",
        signal_type=SignalType.PATTERN_ENTRY_SIGNAL,
        player="Player A",
        direction="YES",
        created_at_ms=now,
        expires_at_ms=now + 20_000,
        original_ttl_ms=20_000,
        market_price=58,
        target_entry_price=58,
        maximum_entry_price=66,
        model_probability=0.55,
        net_edge=0.02,
        confidence=70,
        creation_price=58,
        pattern_type="PULLBACK_RECOVERY",
        bet_instruction="Bet YES on Player A now",
    )
    engine.snap.signals[sig.signal_id] = sig
    ctx.active_signal_id = sig.signal_id

    def relabel(*_args, **_kwargs):
        return PatternAssessment(
            decision="PATTERN_ALREADY_ADVANCED",
            pattern_type="FAILED_BREAKOUT",
            stage="LATE",
            progress=90,
            explanation="Pattern progressed beyond ideal entry zone.",
        )

    engine._assess_pattern = relabel  # type: ignore[method-assign]
    await engine.on_market_update(
        "KXTEST",
        yes_bid=59.5,
        yes_ask=60.5,
        depth_yes=1600,
        depth_no=700,
        imbalance=0.2,
        status="OPEN",
    )
    assert sig.status != SignalStatus.ACTIVE
    assert not sig.is_actionable()


@pytest.mark.asyncio
async def test_study_clock_blocks_entry_for_five_minutes():
    s = settings()
    s.initial_observation_seconds = 300
    engine = SignalEngine(s)
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
    seed_pullback(engine.pattern_engine, "KXTEST", now, 56, 53, 60, vol=1)
    for price in (60, 59, 58, 57, 57.4, 58):
        await engine.on_market_update(
            "KXTEST",
            yes_bid=price - 0.5,
            yes_ask=price + 0.5,
            depth_yes=1600,
            depth_no=700,
            imbalance=0.32,
            status="OPEN",
        )
    payload = engine.dashboard_payload()
    card = payload["matches"][0]
    assert card["observation_remaining_ms"] == pytest.approx(300_000, abs=100)
    assert card["display_state"] == "STUDYING_MATCH"
    assert not payload["actionable_signals"]
