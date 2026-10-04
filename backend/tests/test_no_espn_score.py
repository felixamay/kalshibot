"""The live score factory must not call ESPN."""

import pytest

from app.config import Settings
from app.services.tennis.provider import NullTennisProvider, create_tennis_provider


@pytest.mark.asyncio
async def test_factory_never_returns_espn():
    cases = [
        Settings(_env_file=None, tennis_provider="espn", tennis_api_key="x"),
        Settings(_env_file=None, tennis_provider="livetennis", tennis_api_key=""),
        Settings(_env_file=None, tennis_provider="", tennis_api_key=""),
        Settings(_env_file=None, tennis_provider="custom", tennis_api_key=""),
    ]
    for settings in cases:
        provider = create_tennis_provider(settings)
        assert isinstance(provider, NullTennisProvider)
        assert type(provider).__module__ != "app.services.tennis.espn"
        assert await provider.list_live_matches() == []
        await provider.close()

    live = create_tennis_provider(
        Settings(
            _env_file=None,
            tennis_provider="livetennis",
            tennis_api_key="test-key",
            tennis_api_base_url="https://example.invalid",
        )
    )
    try:
        assert isinstance(live, NullTennisProvider)
        assert type(live).__name__ != "LiveTennisProvider"
        assert await live.list_live_matches() == []
    finally:
        await live.close()
