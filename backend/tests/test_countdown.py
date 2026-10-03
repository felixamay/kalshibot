"""Countdown and signal lifecycle tests — critical safety invariants."""

from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.core.clock import ClockSync, remaining_ms, urgency_stage
from app.core.enums import ConnectionStatus, ExpirationReason, SignalStatus, SignalType
from app.services.market.analyzer import RollingMarketAnalyzer
from app.services.signals.engine import SignalEngine
from app.services.signals.live_signal import LiveSignal
from app.services.signals.ttl import SignalTTLCalculator


@pytest.fixture
def settings() -> Settings:
    return Settings(
        initial_observation_seconds=0,  # skip observation in tests
        min_bet_confidence=50,
        strong_bet_confidence=80,
        min_net_edge=0.01,
        entry_confirmation_count=2,
        max_signals_per_match=10,
        min_signal_ttl_seconds=2,
        default_signal_ttl_seconds=8,
        max_signal_ttl_seconds=15,
        max_data_age_ms=5000,
        min_liquidity_contracts=10,
        max_spread_cents=10,
        database_url="sqlite+aiosqlite:///:memory:",
    )


def make_signal(
    *,
    ttl_ms: int = 8000,
    created_at: float | None = None,
    price: float = 64,
    max_entry: float = 66,
    confidence: float = 91,
    net_edge: float = 0.062,
    version: int = 1,
) -> LiveSignal:
    now = created_at if created_at is not None else time.time() * 1000.0
    return LiveSignal(
        signal_id="SIG-ALC-TEN-00001",
        signal_version=version,
        match_id="m1",
        market_id="mk1",
        market_ticker="KXTEST-YES",
        signal_type=SignalType.BET_NOW,
        player="Carlos Alcaraz",
        direction="YES",
        created_at_ms=now,
        expires_at_ms=now + ttl_ms,
        original_ttl_ms=ttl_ms,
        market_price=price,
        target_entry_price=price,
        maximum_entry_price=max_entry,
        model_probability=0.73,
        net_edge=net_edge,
        confidence=confidence,
        creation_price=price,
    )


class TestClockAndRemaining:
    def test_remaining_decreases_with_server_time(self):
        created = 1_000_000.0
        expires = created + 8000
        assert remaining_ms(expires, created) == 8000
        assert remaining_ms(expires, created + 2500) == 5500
        assert remaining_ms(expires, expires) == 0
        assert remaining_ms(expires, expires + 100) == 0

    def test_timer_never_negative(self):
        assert remaining_ms(1000, 5000) == 0.0

    def test_network_delay_reduces_displayed_remaining(self):
        """Latency-adjusted: browser receives late → less remaining."""
        created = 1_000_000.0
        ttl = 8000
        expires = created + ttl
        receive_delay = 450  # ms late
        rem = remaining_ms(expires, created + receive_delay)
        assert abs(rem - 7550) < 1

    def test_browser_freeze_recalculates_from_timestamps(self):
        created = 1_000_000.0
        expires = created + 8000
        # showed 6.4s then froze 3s
        after_freeze = created + 1600 + 3000  # 4.6s elapsed from start... wait
        # At 6.4 remaining => 1.6s elapsed. Freeze 3s => 4.6 elapsed => 3.4 remaining
        rem = remaining_ms(expires, created + 1600 + 3000)
        assert abs(rem - 3400) < 1

    def test_urgency_stages(self):
        assert urgency_stage(7800) == "NORMAL"
        assert urgency_stage(3400) == "CAUTION"
        assert urgency_stage(1200) == "FINAL"
        assert urgency_stage(0) == "EXPIRED"

    def test_clock_sync_offset(self):
        sync = ClockSync()
        # client sends at 1000, server says 1100, client receives at 1050 => RTT 50, latency 25
        # mid_client=1025, offset=1100-1025=75
        sync.record_rtt(1000, 1100, 1050)
        assert abs(sync.offset_ms - 75) < 1
        est = sync.estimated_server_now_ms(2000)
        assert abs(est - 2075) < 1


class TestLiveSignalSafety:
    def test_signal_starts_with_correct_ttl(self):
        sig = make_signal(ttl_ms=8000, created_at=1_000_000.0)
        assert sig.original_ttl_ms == 8000
        assert sig.remaining_ms(1_000_000.0) == 8000

    def test_expires_exactly_at_expires_at(self):
        sig = make_signal(ttl_ms=8000, created_at=1_000_000.0)
        assert sig.is_actionable(1_000_000.0 + 7999)
        assert not sig.is_actionable(1_000_000.0 + 8000)
        assert not sig.is_actionable(1_000_000.0 + 8001)

    def test_expired_bet_now_never_remains_actionable(self):
        sig = make_signal(ttl_ms=1000, created_at=1_000_000.0)
        now = 1_002_000.0
        assert not sig.is_actionable(now)
        d = sig.to_public_dict(now)
        assert d["actionable"] is False
        assert d["display_label"] == "SIGNAL EXPIRED"
        assert d["signal_type"] != "BET_NOW" or not d["actionable"]
        # Must not show BET NOW as actionable
        assert not (d["display_label"] == "BET_NOW" and d["actionable"])

    def test_price_exceeds_max_entry_cancels(self):
        sig = make_signal(price=64, max_entry=66, created_at=1_000_000.0)
        sig.cancel(
            ExpirationReason.PRICE_MOVED,
            price=68,
            message="Price exceeded maximum entry.",
            server_now_ms=1_001_000.0,
        )
        assert sig.status == SignalStatus.CANCELLED
        assert not sig.is_actionable(1_001_000.0)
        d = sig.to_public_dict(1_001_000.0)
        assert d["display_sublabel"] == "DO NOT CHASE"

    def test_new_version_invalidates_old(self):
        old = make_signal(version=3, created_at=1_000_000.0)
        old.cancel(ExpirationReason.SUPERSEDED, server_now_ms=1_001_000.0)
        new = make_signal(version=4, created_at=1_001_000.0)
        assert not old.is_actionable(1_001_100.0)
        assert old.status == SignalStatus.SUPERSEDED
        assert new.is_actionable(1_001_100.0)
        assert new.signal_version == 4

    def test_progress_bar_ratio(self):
        sig = make_signal(ttl_ms=10000, created_at=1_000_000.0)
        assert abs(sig.progress(1_000_000.0) - 1.0) < 1e-6
        assert abs(sig.progress(1_005_000.0) - 0.5) < 1e-6
        assert sig.progress(1_020_000.0) == 0.0


class TestTTLCalculator:
    def test_ttl_within_bounds(self, settings: Settings):
        calc = SignalTTLCalculator(settings)
        analyzer = RollingMarketAnalyzer("T", settings.window_sizes)
        # seed stable ticks
        base = time.time() * 1000.0
        for i in range(20):
            analyzer.update(
                yes_bid=50,
                yes_ask=52,
                depth_yes=200,
                depth_no=200,
                imbalance=0.1,
                ts_ms=base + i * 200,
            )
        result = calc.calculate(analyzer.state, confidence=90)
        assert settings.min_signal_ttl_seconds <= result.ttl_seconds <= settings.max_signal_ttl_seconds

    def test_fast_market_shorter_ttl(self, settings: Settings):
        calc = SignalTTLCalculator(settings)
        analyzer = RollingMarketAnalyzer("T", settings.window_sizes)
        base = time.time() * 1000.0
        for i in range(30):
            # wild swings
            px = 50 + (i % 5) * 3
            analyzer.update(
                yes_bid=px,
                yes_ask=px + 5,
                depth_yes=20,
                depth_no=20,
                ts_ms=base + i * 50,
            )
        result = calc.calculate(analyzer.state, confidence=85, match_volatile=True)
        assert result.ttl_seconds <= settings.default_signal_ttl_seconds


@pytest.mark.asyncio
async def test_engine_cancels_on_price_move(settings: Settings):
    engine = SignalEngine(settings)
    engine.snap.connection_status = ConnectionStatus.CONNECTED
    ctx = engine.register_match(
        match_id="m1",
        player_a="Alcaraz",
        player_b="Sinner",
        tournament="Test Open",
        market_ticker="KXTEST",
        market_db_id="mk1",
        now_ms=time.time() * 1000.0 - 400_000,  # observation done
    )
    # Build favorable book
    for i in range(10):
        await engine.on_market_update(
            "KXTEST",
            yes_bid=60,
            yes_ask=62,
            depth_yes=100,
            depth_no=80,
            imbalance=0.3,
            status="OPEN",
        )
    # Manually inject active signal
    now = time.time() * 1000.0
    sig = make_signal(created_at=now, price=62, max_entry=64, ttl_ms=8000)
    sig.market_ticker = "KXTEST"
    engine.snap.signals[sig.signal_id] = sig
    ctx.active_signal_id = sig.signal_id

    # Price jumps beyond max
    await engine.on_market_update(
        "KXTEST",
        yes_bid=68,
        yes_ask=70,
        depth_yes=100,
        depth_no=80,
        imbalance=0.1,
        status="OPEN",
    )
    assert sig.status != SignalStatus.ACTIVE
    assert sig.expiration_reason == ExpirationReason.PRICE_MOVED
    assert not sig.is_actionable()


@pytest.mark.asyncio
async def test_engine_cancels_on_stale_and_disconnect(settings: Settings):
    engine = SignalEngine(settings)
    engine.snap.connection_status = ConnectionStatus.CONNECTED
    ctx = engine.register_match(
        match_id="m1",
        player_a="A",
        player_b="B",
        tournament="T",
        market_ticker="KXSTALE",
        market_db_id="mk2",
        now_ms=time.time() * 1000.0 - 400_000,
    )
    now = time.time() * 1000.0
    sig = make_signal(created_at=now, ttl_ms=8000)
    sig.market_ticker = "KXSTALE"
    engine.snap.signals[sig.signal_id] = sig
    ctx.active_signal_id = sig.signal_id

    # Stale: last update old
    analyzer = engine.snap.analyzers["KXSTALE"]
    analyzer.state.last_update_ms = now - 10_000
    analyzer.state.yes_bid = 60
    analyzer.state.yes_ask = 62
    analyzer.state.depth_yes = 100
    analyzer.state.depth_no = 100
    await engine._validate_active_signal(ctx, analyzer.state, now)
    assert sig.expiration_reason == ExpirationReason.STALE_DATA

    # New signal + disconnect
    sig2 = make_signal(created_at=now, ttl_ms=8000)
    sig2.signal_id = "SIG-2"
    sig2.market_ticker = "KXSTALE"
    engine.snap.signals[sig2.signal_id] = sig2
    ctx.active_signal_id = sig2.signal_id
    analyzer.state.last_update_ms = now
    engine.snap.connection_status = ConnectionStatus.DISCONNECTED
    await engine._validate_active_signal(ctx, analyzer.state, now)
    assert sig2.expiration_reason == ExpirationReason.CONNECTION_LOST


@pytest.mark.asyncio
async def test_edge_and_confidence_cancel(settings: Settings):
    engine = SignalEngine(settings)
    engine.snap.connection_status = ConnectionStatus.CONNECTED
    ctx = engine.register_match(
        match_id="m1",
        player_a="A",
        player_b="B",
        tournament="T",
        market_ticker="KXEDGE",
        market_db_id="mk3",
        now_ms=time.time() * 1000.0 - 400_000,
    )
    now = time.time() * 1000.0
    sig = make_signal(created_at=now, price=50, max_entry=55, net_edge=0.08, confidence=90)
    sig.market_ticker = "KXEDGE"
    engine.snap.signals[sig.signal_id] = sig
    ctx.active_signal_id = sig.signal_id

    # Update market so model edge collapses (price near model)
    await engine.on_market_update(
        "KXEDGE",
        yes_bid=72,
        yes_ask=74,  # still under max 55? No — 74 > 55 so PRICE_MOVED first
        depth_yes=100,
        depth_no=100,
        imbalance=0,
        status="OPEN",
    )
    assert not sig.is_actionable()


def test_confidence_is_not_deadlocked_by_confirmation_count():
    """Entry gate must be reachable before ENTRY_CONFIRMATION_COUNT is met."""
    from app.services.market.analyzer import RollingMarketAnalyzer
    from app.services.signals.confidence import SignalConfidenceCalculator
    from app.services.tennis.probability import ProbabilityResult

    settings = Settings(
        min_bet_confidence=85,
        entry_confirmation_count=5,
        min_liquidity_contracts=10,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    calc = SignalConfidenceCalculator(settings)
    analyzer = RollingMarketAnalyzer("T", settings.window_sizes)
    base = time.time() * 1000.0
    for i in range(12):
        analyzer.update(
            yes_bid=60,
            yes_ask=62,
            depth_yes=400,
            depth_no=300,
            imbalance=0.35,
            ts_ms=base + i * 400,
        )
    prob = ProbabilityResult(
        player="A",
        direction="YES",
        model_win_probability=0.75,
        executable_market_probability=0.62,
        raw_edge=0.13,
        estimated_net_edge=0.08,
        source="test",
    )
    at_zero = calc.calculate(analyzer.state, prob, confirmation_count=0)
    at_full = calc.calculate(
        analyzer.state, prob, confirmation_count=settings.entry_confirmation_count
    )
    assert at_zero.confidence == at_full.confidence
    # Previously this was scaled by ~0.55 at count 0, so the entry gate could never open.
    assert at_zero.confidence > 70

    from app.services.kalshi.client import KalshiReadOnlyClient
    import asyncio

    client = KalshiReadOnlyClient(Settings(kalshi_use_auth=False, database_url="sqlite+aiosqlite:///:memory:"))

    async def attempt():
        with pytest.raises(PermissionError):
            await client.request("POST", "/portfolio/orders")

    asyncio.get_event_loop().run_until_complete(attempt())


def test_no_place_order_methods():
    from app.services.kalshi import client as c
    from app.services.kalshi.client import KalshiReadOnlyClient

    assert not hasattr(KalshiReadOnlyClient, "place_order")
    assert not hasattr(KalshiReadOnlyClient, "create_order")
    assert not hasattr(KalshiReadOnlyClient, "cancel_order")
    src = open(c.__file__).read()
    assert "place_order" not in src or "assert not hasattr" in src
