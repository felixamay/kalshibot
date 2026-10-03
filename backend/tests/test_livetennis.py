"""Live Tennis API slate parsing."""

import httpx
import pytest

from app.config import Settings
from app.services.tennis.livetennis import (
    LiveTennisProvider,
    parse_live_matches,
    state_for_players,
)

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
        headers={"X-API-Key": settings.tennis_api_key},
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
