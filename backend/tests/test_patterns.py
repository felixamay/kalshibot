"""Pattern learning: repetition, confirmation, and late-move protection."""

from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.core.enums import ConnectionStatus, SignalType
from app.database import Base
from app.services.signals.engine import SignalEngine
from app.services.signals.patterns import (
    BookSnapshot,
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
)
from app.services.tennis.probability import ProbabilityResult


def settings() -> Settings:
    return Settings(
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


def test_entry_requires_confirmation_and_ignores_a_single_sighting():
    engine = PatternEngine(settings())
    seed_pullback(engine, "KX", 1_000, 52, 49, 55, vol=1)
    once = engine.assess(
        ticker="KX",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert once.repetition_label == "OBSERVED ONLY"
    assert once.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")

    for index in range(3):
        seed_pullback(engine, "KX2", 2_000 + index, 52 + index, 49 + index, 56 + index, vol=1)
    waiting = engine.assess(
        ticker="KX2",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=1,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert waiting.decision in ("PATTERN_WATCH", "PATTERN_DEVELOPING")
    assert waiting.decision not in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL")
    ready = engine.assess(
        ticker="KX2",
        points=developing_pullback(),
        book=early_book(58),
        confirmation_count=3,
        baseline_volatility_override=1,
        player_a="Player A",
    )
    assert ready.pattern_type == "PULLBACK_RECOVERY"
    assert ready.stage in ("EARLY", "DEVELOPING", "MATURE")
    assert ready.decision == "PATTERN_ENTRY_SIGNAL", ready.explanation
    assert ready.confidence >= 70
    assert ready.entry_score >= 75


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
