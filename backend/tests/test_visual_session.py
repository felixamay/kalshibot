import logging
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.tennis.visual_session import (
    KalshiVisualSession,
    VisualServiceCounter,
    action_allowed,
    apply_visual_score,
    mark_score_unavailable,
    parse_kalshi_board,
    parse_visible_score,
)


def score(games, sets="0-0", server=None):
    return SimpleNamespace(available=True, game_score=games, match_score=sets, server=server, is_tiebreak=False)


def test_computer_actions_never_trade():
    assert action_allowed({"type": "screenshot"})
    assert action_allowed({"type": "scroll", "scroll_y": 200})
    assert action_allowed({"type": "navigate", "url": "https://kalshi.com/markets/kxwtamatch/event"})
    assert not action_allowed({"type": "click", "x": 10, "y": 10})
    assert not action_allowed({"type": "click", "button": "buy"})
    assert not action_allowed({"type": "type", "text": "1"})
    assert not action_allowed({"type": "keypress", "keys": ["Enter"]})
    assert not action_allowed({"type": "drag", "path": []})
    assert not action_allowed({"type": "navigate", "url": "https://example.com/buy"})
    assert not action_allowed({"type": "scroll", "note": "click Sell"})


def test_kalshi_board_reads_sets_and_points_and_counts_two_games():
    text = "Moro Canas vs Ritschard\nLIVE\n1-1\n40-40\nYes 46¢\nNo 57¢"
    board = parse_kalshi_board(text)
    assert board.sets == (1, 1)
    assert board.points == (3, 3)
    counter = VisualServiceCounter()
    assert counter.observe_board(parse_kalshi_board("LIVE\n1-1\n40-30"), 1) == ([], [])
    blocks, lines = counter.observe_board(parse_kalshi_board("LIVE\n1-1\n0-0"), 2)
    assert lines == ["SERVICE GAME 1/2"]
    assert blocks == []
    counter.observe_board(parse_kalshi_board("LIVE\n1-1\n40-15"), 3)
    blocks, lines = counter.observe_board(parse_kalshi_board("LIVE\n1-1\n0-0"), 4)
    assert lines == ["SERVICE GAME 2/2", "GPT PATTERN ANALYSIS STARTED"]
    assert len(blocks) == 1


def test_kalshi_page_score_is_logged_and_a_blank_page_does_not_use_espn(caplog):
    text = "Alejandro Moro Canas vs Alexander Ritschard\nLIVE\n1-1\n40-40\nServer: Alejandro Moro Canas"
    board = parse_kalshi_board(text)
    ctx = SimpleNamespace(match_id="itf", player_a="Alejandro Moro Canas", player_b="Alexander Ritschard", tournament="ITF", tennis=None)
    counter = VisualServiceCounter()
    caplog.set_level(logging.INFO)
    apply_visual_score(ctx, board, counter, text)
    assert ctx.tennis.available is True
    assert ctx.tennis.source == "kalshi_visual"
    assert ctx.tennis.source_url is None
    assert ctx.tennis.set_score == "1-1 40-40"
    assert ctx.tennis.server == "A"
    assert "GPT SCORE READ: 1-1 40-40" in caplog.text
    assert "CURRENT SERVER: Alejandro Moro Canas" in caplog.text
    assert "SERVICE GAME COUNT: 0/2" in caplog.text
    assert "espn" not in caplog.text.casefold()
    blank = SimpleNamespace(match_id="blank", player_a="Ann", player_b="Bea", tennis=None)
    mark_score_unavailable(blank)
    assert blank.tennis.available is False
    assert blank.tennis.set_score is None
    assert blank.tennis.source == "kalshi_visual"
    kept = SimpleNamespace(match_id="kept", player_a="Ann", player_b="Bea", tennis=SimpleNamespace(available=True, source="livetennis", source_url=None, set_score="6-3"))
    mark_score_unavailable(kept)
    assert kept.tennis.source == "livetennis"


def test_visible_score_parses_the_displayed_set():
    text = "Maria Sakkari vs Elina Svitolina\n6-4 2-3\nServer: Maria Sakkari\nYes 62¢"
    state = parse_visible_score(text, "Maria Sakkari", "Elina Svitolina")
    assert state.game_score == "2-3"
    assert state.match_score == "1-0"
    assert state.server == "A"


@pytest.mark.asyncio
async def test_two_service_games_always_return_a_pattern_label(caplog):
    session = KalshiVisualSession(Settings(_env_file=None))
    caplog.set_level(logging.INFO)
    assert await session.note_score(score("0-0")) is None
    assert await session.note_score(score("1-0")) is None
    assert session.counter.label == "1/2"
    label = await session.note_score(score("1-1"))
    assert label == "NO RELIABLE PATTERN"
    assert session.last_result == "NO RELIABLE PATTERN"
    assert session.diagnostics()["gpt_browser_connected"] == "NO"
    assert session.diagnostics()["service_games_counted"] == "2/2"
    text = caplog.text
    assert "SERVICE GAME 1/2" in text
    assert "SERVICE GAME 2/2" in text
    assert "GPT PATTERN ANALYSIS STARTED" in text
    assert label in ("YES — Ann", "WATCH", "WAIT", "NO RELIABLE PATTERN")


@pytest.mark.asyncio
async def test_visual_pattern_yes_requires_kalshi_confirmation():
    from app.services.market.analyzer import MarketState
    from app.services.signals.ai_patterns import HybridPatternDecisionEngine

    class Engine:
        def __init__(self):
            self.hybrid_engine = HybridPatternDecisionEngine(Settings(_env_file=None, openai_api_key="test"))
            self.snap = SimpleNamespace(analyzers={})

    session = KalshiVisualSession(Settings(_env_file=None, openai_api_key="test"), Engine())
    session.gpt_connected = True
    session.browser_open = True

    async def seeing(_ctx):
        return {"pattern_name": "Hold pressure", "favored_side": "PLAYER_A", "pattern_stage": "DEVELOPING",
                "pattern_confidence": 70, "recommendation": "ENTRY_SIGNAL", "maximum_entry": 0.6,
                "ideal_entry_low": 0.4, "reason": "pattern visible"}

    session.inspect = seeing
    ctx = SimpleNamespace(match_id="m", player_a="Ann", player_b="Bea", market_ticker="T", last_pattern=None)
    now = __import__("time").time() * 1000
    flat = MarketState("T", yes_bid=49, yes_ask=50, depth_yes=5000, depth_no=5000, imbalance=0, last_update_ms=now)
    session.engine.snap.analyzers["T"] = SimpleNamespace(state=flat)
    label, reason = await session.conclude(ctx, None)
    assert label == "WATCH"
    assert "do not confirm" in reason
    live = MarketState("T", yes_bid=49, yes_ask=50, depth_yes=5000, depth_no=2000, imbalance=0.44, last_update_ms=now)
    session.engine.snap.analyzers["T"] = SimpleNamespace(state=live)
    label, reason = await session.conclude(ctx, None)
    assert label == "YES — Ann"
    assert ctx.hybrid_decision["signal"]["play_sound"] is True
