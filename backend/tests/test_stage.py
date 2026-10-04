"""The study counts completed games and never forces a recommendation."""
import pytest
from app.config import Settings
from app.services.signals.engine import SignalEngine
from app.services.tennis.provider import TennisLiveState

@pytest.mark.asyncio
async def test_points_and_lopsided_price_never_force_entry():
    engine = SignalEngine(Settings(_env_file=None, initial_observation_seconds=0))
    ctx = engine.register_match(match_id="m", player_a="A", player_b="B", tournament=None, market_ticker="T", market_db_id="t")
    for point in ("0-0", "15-0", "30-0", "40-0"):
        ctx.tennis = TennisLiveState("m", "A", "B", game_score="3-2", match_score="0-0", server="A", point_score=point, available=True)
        await engine.on_market_update("T", yes_bid=89, yes_ask=90, depth_yes=1000, depth_no=1000)
    assert ctx.serves_seen == 0
    assert ctx.tracker.available
    assert not engine.dashboard_payload()["actionable_signals"]
