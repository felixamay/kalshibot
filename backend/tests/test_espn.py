"""ESPN scoreboard parsing and the live-match link."""

from __future__ import annotations

import time

from app.services.signals.engine import SignalEngine, match_is_live
from app.services.tennis.espn import (
    EspnLiveMatch,
    parse_scoreboard,
    same_match,
    state_for_players,
)
from tests.test_patterns import settings


def test_same_players_match_in_either_order():
    assert same_match("Elena Rybakina", "Alina Charaeva", "Alina Charaeva", "Elena Rybakina")
    assert not same_match("Elena Rybakina", "Alina Charaeva", "Lois Boisson", "Erika Andreeva")


def test_only_in_progress_matches_are_parsed_and_the_score_follows_the_yes_player():
    payload = {
        "events": [
            {
                "shortName": "China Open",
                "groupings": [
                    {
                        "competitions": [
                            {
                                "id": "1",
                                "status": {"type": {"state": "post", "name": "STATUS_FINAL", "detail": "Final"}},
                                "competitors": [
                                    {"athlete": {"displayName": "Iga Swiatek"}, "linescores": [{"value": 6}]},
                                    {"athlete": {"displayName": "Aryna Sabalenka"}, "linescores": [{"value": 4}]},
                                ],
                            },
                            {
                                "id": "184323",
                                "status": {
                                    "type": {"state": "in", "name": "STATUS_IN_PROGRESS", "detail": "2nd Set"}
                                },
                                "competitors": [
                                    {
                                        "athlete": {"displayName": "Alina Charaeva"},
                                        "linescores": [{"value": 3}, {"value": 5}],
                                    },
                                    {
                                        "athlete": {"displayName": "Elena Rybakina"},
                                        "linescores": [{"value": 6}, {"value": 4}],
                                    },
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }
    live = parse_scoreboard(payload)
    assert [match.match_id for match in live] == ["184323"]
    state = state_for_players(live[0], "Elena Rybakina", "Alina Charaeva")
    assert state.available is True
    assert state.source == "espn"
    assert state.set_score == "6-3 4-5"
    assert state.match_score == "1-0"
    assert state.game_score == "4-5"
    assert state.source_url == "https://www.espn.com/tennis/match/_/id/184323"


def test_a_confirmed_score_overrides_a_stale_kalshi_start():
    engine = SignalEngine(settings())
    now = time.time() * 1000.0
    engine.register_match(
        match_id="old",
        player_a="Elena Rybakina",
        player_b="Alina Charaeva",
        tournament="China Open",
        market_ticker="KXOLD",
        market_db_id="1",
        now_ms=now,
        scheduled_start_ms=now - 32 * 60 * 60 * 1000,
    )
    engine.register_match(
        match_id="soon",
        player_a="Tomorrow",
        player_b="Player",
        tournament="China Open",
        market_ticker="KXSOON",
        market_db_id="2",
        now_ms=now,
        scheduled_start_ms=now - 60_000,
    )
    engine.snap.matches["KXOLD"].score_confirmed = True
    engine.snap.matches["KXSOON"].score_confirmed = False
    assert match_is_live(engine.snap.matches["KXOLD"], now) is True
    assert match_is_live(engine.snap.matches["KXSOON"], now) is False
    payload = engine.dashboard_payload()
    assert [card["market_ticker"] for card in payload["matches"]] == ["KXOLD"]


def test_state_for_players_keeps_orientation():
    match = EspnLiveMatch(
        match_id="9",
        player_a="Lois Boisson",
        player_b="Erika Andreeva",
        tournament="Adana",
        detail="3rd Set",
        sets=[(6, 3), (4, 6), (0, 0)],
        source_url="https://www.espn.com/tennis/match/_/id/9",
    )
    state = state_for_players(match, "Erika Andreeva", "Lois Boisson")
    assert state.set_score == "3-6 6-4 0-0"
    assert state.match_score == "1-1"
