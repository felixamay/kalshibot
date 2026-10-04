"""Live Tennis API slate parsing."""

import httpx
import pytest

from app.config import Settings
from app.services.market.orchestrator import _kalshi_match_is_trading
from app.services.tennis.livetennis import (
    LiveTennisProvider,
    parse_live_matches,
    state_for_players,
)


@pytest.fixture(autouse=True)
def _isolate_live_tennis_slate(tmp_path, monkeypatch):
    """Tests must not overwrite the process slate used by the running API."""
    monkeypatch.setattr("app.services.tennis.livetennis._SLATE_FILE", tmp_path / "slate.json")

_SLATE = {
    "data": [
        {
            "id": 101,
            "status": "upcoming",
            "tournament": "Not started",
            "players": {"p1": {"name": "Ada Soon"}, "p2": {"name": "Bea Later"}},
            "score": None,
        },
        {
            "id": 202,
            "status": "live",
            "tournament": "M15 Fayetteville, AR",
            "players": {
                "p1": {"name": "Dominick Mosejczuk"},
                "p2": {"name": "Jonah Braswell"},
            },
            "score": {
                "sets": [1, 1],
                "games": [[2, 7, 2], [6, 6, 3]],
                "points": ["30", "15"],
                "server": 2,
            },
        },
        {
            "id": 303,
            "status": "live",
            "tournament": "W15 Nashville",
            "players": {
                "p1": {"name": "Erin Pearce / Mia Yamakita"},
                "p2": {"name": "Emma Malmkjaer Kamper / Isabella Kruger"},
            },
            "score": {"sets": [0, 1], "games": [[1, 4], [6, 0]], "points": ["0", "0"], "server": 1},
        },
    ],
    "meta": {"count": 3, "limit": 200, "offset": 0, "has_more": False, "total": 3},
}


def test_only_live_rows_keep_sets_points_and_server():
    found = parse_live_matches(_SLATE)
    assert [match.player_a for match in found] == [
        "Dominick Mosejczuk",
        "Erin Pearce / Mia Yamakita",
    ]
    match = found[0]
    state = state_for_players(match, match.player_a, match.player_b)
    assert state.set_score == "2-6 7-6 2-3"
    assert state.game_score == "2-3"
    assert state.match_score == "1-1"
    assert state.point_score == "30-15"
    assert state.server == "B"
    assert state.source == "livetennis"
    assert state.available is True


def test_score_flips_to_the_yes_player():
    match = parse_live_matches(_SLATE)[0]
    state = state_for_players(match, "Jonah Braswell", "Dominick Mosejczuk")
    assert state.set_score == "6-2 6-7 3-2"
    assert state.game_score == "3-2"
    assert state.match_score == "1-1"
    assert state.point_score == "15-30"
    assert state.server == "A"


def test_a_live_row_with_no_score_is_not_invented():
    found = parse_live_matches(
        {
            "data": [
                {
                    "id": 9,
                    "status": "live",
                    "players": {"p1": {"name": "Ann Ace"}, "p2": {"name": "Bea Ball"}},
                    "score": None,
                }
            ]
        }
    )
    state = state_for_players(found[0], "Ann Ace", "Bea Ball")
    assert state.set_score is None
    assert state.point_score is None
    assert state.server is None
    assert state.available is True


@pytest.mark.asyncio
async def test_provider_reads_the_live_slate_and_caches_it():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path.endswith("/matches")
        assert request.url.params["status"] == "live"
        assert request.headers["X-API-Key"]
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json=_SLATE)

    settings = Settings(
        tennis_provider="livetennis",
        tennis_api_key="test-key",
        tennis_api_base_url="https://api.livetennisapi.com/api/public/v1",
        tennis_poll_interval_seconds=0,
    )
    provider = LiveTennisProvider(settings)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={
            "X-API-Key": settings.tennis_api_key,
            "Authorization": f"Bearer {settings.tennis_api_key}",
        },
    )
    try:
        first = await provider.list_live_matches()
        second = await provider.list_live_matches()
    finally:
        await provider.close()
    assert calls["n"] == 1
    assert first is not None and len(first) == 2
    assert second is not None and len(second) == 2
    assert first[0].point_score == "30-15"


@pytest.mark.asyncio
async def test_a_rate_limit_holds_the_last_slate():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_SLATE)
        return httpx.Response(
            429,
            json={"error": "rate_limited", "scope": "day", "resets_at": "2099-01-01T00:00:00Z"},
            headers={"Retry-After": "120"},
        )

    settings = Settings(
        tennis_provider="livetennis",
        tennis_api_key="test-key",
        tennis_api_base_url="https://api.livetennisapi.com/api/public/v1",
        tennis_poll_interval_seconds=0,
    )
    provider = LiveTennisProvider(settings)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"X-API-Key": settings.tennis_api_key},
    )
    try:
        first = await provider.list_live_matches()
        provider._fetched_at = 0
        second = await provider.list_live_matches()
        provider._fetched_at = 0
        third = await provider.list_live_matches()
    finally:
        await provider.close()
    assert first is not None and len(first) == 2
    assert second is not None and len(second) == 2
    assert third is not None and len(third) == 2
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_a_restart_during_a_rate_limit_keeps_the_last_slate(tmp_path, monkeypatch):
    slate = tmp_path / "slate.json"
    monkeypatch.setattr("app.services.tennis.livetennis._SLATE_FILE", slate)
    slate.write_text(
        '{"saved_at": %s, "matches": [{"match_id": "198783", "player_a": "Luis Guto Miguel / Eduardo Ribeiro", "player_b": "Kestelboim / Zormann", "tournament": "Curitiba, Brazil, Doubles", "sets": [[7, 5], [4, 3]], "point_score": "30-30", "match_score": "1-0", "server": "B"}]}'
        % __import__("time").time()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limited"}, headers={"Retry-After": "60"})

    settings = Settings(
        tennis_provider="livetennis",
        tennis_api_key="test-key",
        tennis_api_base_url="https://api.livetennisapi.com/api/public/v1",
        tennis_poll_interval_seconds=0,
    )
    provider = LiveTennisProvider(settings)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        live = await provider.list_live_matches()
    finally:
        await provider.close()
    assert live is not None and len(live) == 1
    assert live[0].player_a == "Luis Guto Miguel / Eduardo Ribeiro"
    assert live[0].set_score == "7-5 4-3"
    assert live[0].point_score == "30-30"


@pytest.mark.asyncio
async def test_live_tennis_sends_the_key_as_bearer_and_x_api_key():
    settings = Settings(
        tennis_provider="livetennis",
        tennis_api_key="test-key",
        tennis_api_base_url="https://api.livetennisapi.com/api/public/v1",
    )
    provider = LiveTennisProvider(settings)
    try:
        assert provider._client.headers["X-API-Key"] == "test-key"
        assert provider._client.headers["Authorization"] == "Bearer test-key"
    finally:
        await provider.close()


def test_a_kalshi_match_that_just_traded_is_treated_as_live():
    now = 1_000_000.0
    started = "1970-01-12T13:40:00+00:00"
    assert _kalshi_match_is_trading(
        {
            "last_price_dollars": "0.29",
            "occurrence_datetime": started,
            "updated_time": "1970-01-12T13:46:10+00:00",
            "status": "active",
        },
        now,
    )
    assert not _kalshi_match_is_trading(
        {
            "last_price_dollars": "0.29",
            "occurrence_datetime": "1970-01-12T14:00:00+00:00",
            "updated_time": "1970-01-12T13:46:10+00:00",
        },
        now,
    )
    assert not _kalshi_match_is_trading(
        {
            "last_price_dollars": "0",
            "occurrence_datetime": started,
            "updated_time": "1970-01-12T13:46:10+00:00",
        },
        now,
    )
    assert not _kalshi_match_is_trading(
        {
            "last_price_dollars": "0.99",
            "occurrence_datetime": started,
            "updated_time": "1970-01-12T13:46:10+00:00",
            "status": "finalized",
        },
        now,
    )


@pytest.mark.asyncio
async def test_a_fresh_saved_slate_is_used_before_spending_a_call(tmp_path, monkeypatch):
    slate = tmp_path / "slate.json"
    monkeypatch.setattr("app.services.tennis.livetennis._SLATE_FILE", slate)
    slate.write_text(
        '{"saved_at": %s, "matches": [{"match_id": "88", "player_a": "Ann Ace", "player_b": "Bea Ball", "tournament": "Live Cup", "sets": [[3, 2]], "point_score": "15-0", "match_score": "0-0", "server": "A"}]}'
        % __import__("time").time()
    )
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_SLATE)

    settings = Settings(
        tennis_provider="livetennis",
        tennis_api_key="test-key",
        tennis_api_base_url="https://api.livetennisapi.com/api/public/v1",
        tennis_poll_interval_seconds=900,
    )
    provider = LiveTennisProvider(settings)
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        live = await provider.list_live_matches()
    finally:
        await provider.close()
    assert calls["n"] == 0
    assert live is not None and len(live) == 1
    assert live[0].player_a == "Ann Ace"
    assert live[0].point_score == "15-0"
