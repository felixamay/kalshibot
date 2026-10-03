"""Additional safety and integration tests."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core.enums import EXPIRATION_REASON_MESSAGES, ExpirationReason
from app.services.kalshi.client import KalshiReadOnlyClient
from app.services.signals.ttl import SignalTTLCalculator
from app.services.market.analyzer import RollingMarketAnalyzer
from app.services.tennis.probability import TennisProbabilityModel
from app.services.market.analyzer import MarketState


def test_all_expiration_reasons_have_messages():
    for reason in ExpirationReason:
        assert reason in EXPIRATION_REASON_MESSAGES
        assert len(EXPIRATION_REASON_MESSAGES[reason]) > 10


def test_probability_model_net_edge_formula():
    settings = Settings(
        estimated_fee_rate=0.07,
        expected_slippage=0.01,
        safety_margin=0.015,
        database_url="sqlite+aiosqlite:///:memory:",
    )
    model = TennisProbabilityModel(settings)
    market = MarketState(ticker="T", yes_bid=60, yes_ask=62, depth_yes=100, depth_no=100, imbalance=0.2)
    market.last_update_ms = 1e12
    result = model.estimate(player="A", market=market, direction="YES")
    assert 0 < result.model_win_probability < 1
    assert result.executable_market_probability == pytest.approx(0.62)
    fees = (
        settings.estimated_fee_rate
        * result.executable_market_probability
        * (1 - result.executable_market_probability)
    )
    assert result.estimated_fees == pytest.approx(fees)
    assert result.estimated_net_edge == pytest.approx(
        result.raw_edge - fees - settings.expected_slippage - settings.safety_margin
    )


@pytest.mark.asyncio
async def test_kalshi_public_exchange_status():
    client = KalshiReadOnlyClient(
        Settings(kalshi_use_auth=False, database_url="sqlite+aiosqlite:///:memory:")
    )
    try:
        data = await client.get_exchange_status()
        assert isinstance(data, dict)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_kalshi_discovers_or_empty_tennis():
    client = KalshiReadOnlyClient(
        Settings(kalshi_use_auth=False, database_url="sqlite+aiosqlite:///:memory:")
    )
    try:
        markets = await client.search_tennis_markets()
        assert isinstance(markets, list)
        # Either real markets or empty — never invent
        for m in markets:
            assert "ticker" in m
    finally:
        await client.close()
