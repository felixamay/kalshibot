"""Mispricing strategy: price level does not decide the bet. Edge does."""

from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.core.enums import ConnectionStatus, SignalType
from app.services.signals.engine import SignalEngine
from app.services.market.analyzer import MarketState, WindowStats
from app.services.signals.mispricing import (
    assess_market,
    compare_strategy_profiles,
    dynamic_min_edge,
    liquidity_quality,
    max_entry_price_cents,
    spread_quality,
)
from app.services.tennis.probability import ProbabilityResult


def settings() -> Settings:
    return Settings(database_url="sqlite+aiosqlite:///:memory:")


def book(
    *,
    bid: float,
    ask: float,
    depth: float,
    imbalance: float = 0.1,
    volatility: float = 0.2,
    momentum: float = 0.3,
) -> MarketState:
    market = MarketState(
        ticker="KXTEST",
        yes_bid=bid,
        yes_ask=ask,
        depth_yes=depth * 0.6,
        depth_no=depth * 0.4,
        imbalance=imbalance,
        last_update_ms=time.time() * 1000.0,
        status="OPEN",
    )
    spread = ask - bid
    for window in (5000, 60000):
        market.windows[window] = WindowStats(
            window_ms=window,
            count=8,
            volatility=volatility,
            momentum=momentum,
            trade_velocity=1.2,
            avg_spread=spread,
            avg_imbalance=imbalance,
            liquidity=depth,
        )
    return market


def priced(
    *,
    model: float,
    market_prob: float,
    net_edge: float,
    source: str = "tennis_enhanced",
) -> ProbabilityResult:
    return ProbabilityResult(
        player="Player A",
        direction="YES",
        model_win_probability=model,
        executable_market_probability=market_prob,
        raw_edge=model - market_prob,
        estimated_net_edge=net_edge,
        source=source,
        estimated_fees=0.012,
    )


def test_low_price_with_real_edge_can_qualify():
    s = settings()
    market = book(bid=41, ask=42, depth=2500)
    prob = priced(model=0.49, market_prob=0.42, net_edge=0.05, source="tennis_enhanced")
    read = assess_market(
        s, market, prob, confidence=83, confirmation_count=3, tennis_available=True
    )
    assert read.decision == "BET_SIGNAL"
    assert read.uncertainty_adjusted_edge > 0.02
    assert read.liquidity_quality in ("HIGH", "GOOD")


def test_high_price_with_tiny_edge_fails():
    s = settings()
    market = book(bid=74, ask=75, depth=2500)
    prob = priced(model=0.78, market_prob=0.75, net_edge=0.01, source="tennis_enhanced")
    read = assess_market(
        s, market, prob, confidence=91, confirmation_count=3, tennis_available=True
    )
    assert read.decision == "NO_BET"
    assert read.uncertainty_adjusted_edge < (read.dynamic_min_edge or 1)


def test_dynamic_edge_follows_liquidity_and_spread():
    s = settings()
    assert dynamic_min_edge("HIGH", "EXCELLENT", extreme_volatility=False, settings=s) == s.excellent_market_min_edge
    assert dynamic_min_edge("GOOD", "GOOD", extreme_volatility=False, settings=s) == s.min_net_edge
    assert dynamic_min_edge("MEDIUM", "GOOD", extreme_volatility=False, settings=s) == s.medium_market_min_edge
    assert dynamic_min_edge("LOW", "POOR", extreme_volatility=False, settings=s) == s.poor_market_min_edge
    assert dynamic_min_edge("HIGH", "EXCELLENT", extreme_volatility=True, settings=s) is None


def test_one_cent_spread_is_excellent_and_wide_spread_is_poor():
    s = settings()
    tight = book(bid=50, ask=51, depth=800)
    wide = book(bid=40, ask=48, depth=800)
    assert spread_quality(tight, s) == "EXCELLENT"
    assert spread_quality(wide, s) == "VERY_POOR"
    assert dynamic_min_edge("HIGH", "EXCELLENT", extreme_volatility=False, settings=s) < dynamic_min_edge(
        "HIGH", "POOR", extreme_volatility=False, settings=s
    )


def test_poor_liquidity_raises_required_edge_and_very_low_blocks():
    s = settings()
    thin = book(bid=40, ask=41, depth=25)
    empty = book(bid=40, ask=41, depth=0)
    assert liquidity_quality(thin, s) in ("LOW", "VERY_LOW", "MEDIUM")
    assert liquidity_quality(empty, s) == "VERY_LOW"
    assert dynamic_min_edge("LOW", "EXCELLENT", extreme_volatility=False, settings=s) >= s.poor_market_min_edge
    assert dynamic_min_edge("VERY_LOW", "EXCELLENT", extreme_volatility=False, settings=s) is None


def test_extreme_volatility_blocks_even_a_large_edge():
    s = settings()
    market = book(bid=40, ask=41, depth=3000, volatility=8)
    prob = priced(model=0.55, market_prob=0.41, net_edge=0.10, source="tennis_enhanced")
    read = assess_market(
        s, market, prob, confidence=95, confirmation_count=5, tennis_available=True
    )
    assert read.extreme_volatility
    assert read.decision == "MATCH_TOO_VOLATILE"
    assert read.dynamic_min_edge is None


def test_model_uncertainty_reduces_usable_edge():
    s = settings()
    market = book(bid=48, ask=49, depth=2000)
    informed = priced(model=0.56, market_prob=0.49, net_edge=0.04, source="tennis_enhanced")
    weak = priced(model=0.56, market_prob=0.49, net_edge=0.04, source="market_implied_adjusted")
    low = assess_market(s, market, informed, confidence=84, confirmation_count=3, tennis_available=True)
    high = assess_market(s, market, weak, confidence=84, confirmation_count=3, tennis_available=False)
    assert high.model_uncertainty == "HIGH"
    assert low.model_uncertainty == "LOW"
    assert high.uncertainty_adjusted_edge == low.uncertainty_adjusted_edge - s.uncertainty_penalty_high


def test_watch_between_72_and_79_is_not_a_bet():
    s = settings()
    market = book(bid=52, ask=53, depth=2000)
    prob = priced(model=0.58, market_prob=0.53, net_edge=0.035, source="tennis_enhanced")
    read = assess_market(
        s, market, prob, confidence=76, confirmation_count=1, tennis_available=True
    )
    assert read.decision == "WATCH"
    assert "not an instruction to bet" in read.explanation


def test_bet_signal_at_80_after_three_confirmations():
    s = settings()
    market = book(bid=53, ask=54, depth=1800)
    prob = priced(model=0.60, market_prob=0.54, net_edge=0.04, source="tennis_enhanced")
    early = assess_market(
        s, market, prob, confidence=83, confirmation_count=2, tennis_available=True
    )
    ready = assess_market(
        s, market, prob, confidence=83, confirmation_count=3, tennis_available=True
    )
    assert early.decision == "CLOSE_TO_SIGNAL"
    assert early.confirmation_needed == 3
    assert ready.decision == "BET_SIGNAL"


def test_strong_signal_needs_90_confidence_and_strong_edge_not_90_win_prob():
    s = settings()
    market = book(bid=47, ask=48, depth=3000)
    # 58% model vs 48% market is a pricing opportunity, not a 90% favorite.
    prob = priced(model=0.58, market_prob=0.48, net_edge=0.06, source="tennis_enhanced")
    read = assess_market(
        s, market, prob, confidence=92, confirmation_count=3, tennis_available=True
    )
    assert prob.model_win_probability < 0.7
    assert read.decision == "STRONG_BET_SIGNAL"
    assert "not a 90% win probability" in read.explanation

    favorite = priced(model=0.90, market_prob=0.88, net_edge=0.005, source="tennis_enhanced")
    weak = assess_market(
        s, market, favorite, confidence=93, confirmation_count=3, tennis_available=True
    )
    assert weak.decision != "STRONG_BET_SIGNAL"


def test_price_move_and_max_entry_block_chase():
    s = settings()
    cap = max_entry_price_cents(0.61, 0.02, 0.012, s.expected_slippage, s.safety_margin)
    assert abs(cap - 57.0) < 0.2
    market = book(bid=60, ask=61, depth=2000)
    prob = priced(model=0.61, market_prob=0.61, net_edge=0.0, source="tennis_enhanced")
    read = assess_market(
        s,
        market,
        prob,
        confidence=86,
        confirmation_count=3,
        tennis_available=True,
        best_seen_ask=52,
    )
    assert read.decision == "OPPORTUNITY_MISSED"
    assert read.max_entry_price_cents < 61


def test_max_entry_price_formula():
    # Model 61%, required edge 2%, costs about 2% → about 57¢.
    cap = max_entry_price_cents(0.61, 0.02, 0.012, 0.003, 0.005)
    assert abs(cap - 57.0) < 0.2


@pytest.mark.asyncio
async def test_flat_mispricing_does_not_emit_without_a_pattern():
    """A cheap contract with edge is not a bet unless a pattern has repeated."""
    s = settings()
    s.initial_observation_seconds = 0
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
        return priced(model=0.49, market_prob=0.42, net_edge=0.05, source="tennis_enhanced")

    engine.prob_model.estimate = fake_estimate  # type: ignore[method-assign]
    for _ in range(3):
        await engine.on_market_update(
            "KXTEST",
            yes_bid=41,
            yes_ask=42,
            depth_yes=1600,
            depth_no=1200,
            imbalance=0.2,
            status="OPEN",
        )
    assert list(engine.snap.signals.values()) == []
    card = engine.dashboard_payload()["matches"][0]
    assert card["display_state"] not in ("PATTERN_ENTRY_SIGNAL", "BET_SIGNAL", "ENTRY_SIGNAL")


def test_strategy_comparison_does_not_auto_select():
    report = compare_strategy_profiles(
        [
            {
                "name": "mispriced underdog",
                "confidence": 82,
                "adjusted_edge": 0.05,
                "dynamic_min_edge": 0.02,
                "liquidity_quality": "GOOD",
                "spread_quality": "GOOD",
                "max_adverse_movement": -1,
            },
            {
                "name": "favorite with no edge",
                "confidence": 90,
                "adjusted_edge": 0.01,
                "dynamic_min_edge": 0.02,
                "liquidity_quality": "HIGH",
                "spread_quality": "EXCELLENT",
                "max_adverse_movement": -4,
            },
        ]
    )
    assert report["auto_select_enabled"] is False
    assert report["new"]["signals"] == 1
    assert report["old"]["signals"] == 0
    assert "out-of-sample" in report["warning"] or "historical profit" in report["warning"]
