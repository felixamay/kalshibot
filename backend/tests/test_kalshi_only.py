"""The running app reads and trades through Kalshi. It does not open ChatGPT."""

import asyncio

import pytest

from app.config import Settings
from app.services.market.orchestrator import MarketOrchestrator
from app.services.signals.ai_patterns import GPTPatternAnalyst
from app.services.signals.engine import SignalEngine
from app.services.tennis.openai_browser import OpenAIHostedBrowser
from app.services.tennis.service_games import ServeBlock


def test_orchestrator_does_not_construct_a_browser():
    engine = SignalEngine(Settings(_env_file=None, openai_api_key="present"))
    orchestrator = MarketOrchestrator(engine)
    assert orchestrator.visual is None
    assert orchestrator._visual_enabled is False


@pytest.mark.asyncio
async def test_startup_tasks_do_not_include_gpt(monkeypatch):
    engine = SignalEngine(Settings(_env_file=None, openai_api_key="present"))
    orchestrator = MarketOrchestrator(engine)

    class FakeSocket:
        def __init__(self, *args, **kwargs):
            self._subscribed_tickers = ()

        async def start(self):
            return None

    monkeypatch.setattr("app.services.market.orchestrator.KalshiWebSocketClient", FakeSocket)
    names = []
    original = asyncio.create_task

    def capture(coro, *, name=None):
        names.append(name)
        coro.close()
        return original(asyncio.sleep(0))

    monkeypatch.setattr(asyncio, "create_task", capture)
    await orchestrator.start()
    assert "gpt-visual" not in names
    assert "kalshi-visual" not in names
    assert orchestrator.visual is None
    for task in orchestrator._tasks:
        task.cancel()
    await asyncio.gather(*orchestrator._tasks, return_exceptions=True)


def test_schedule_ai_analysis_does_not_queue_a_request():
    engine = SignalEngine(Settings(_env_file=None, openai_api_key="present"))
    ctx = engine.register_match(
        match_id="m", player_a="A", player_b="B", tournament=None, market_ticker="T", market_db_id="t",
    )
    block = ServeBlock("b", "m", 0, 1, ["A", "B"], [{}, {}])
    assert engine.schedule_ai_analysis(ctx, block, engine.snap.analyzers["T"].state, deep=True) is False
    assert engine.ai_tasks == set()


@pytest.mark.asyncio
async def test_pattern_analyst_without_a_test_transport_does_not_call_openai():
    analyst = GPTPatternAnalyst(Settings(_env_file=None, openai_api_key="present"))
    with pytest.raises(RuntimeError, match="AI unavailable"):
        await analyst.analyze({}, "gpt-6-luna")


@pytest.mark.asyncio
async def test_hosted_browser_does_not_dial_openai(monkeypatch, tmp_path):
    def refused(*args, **kwargs):
        raise AssertionError("real OpenAI transport constructed")

    monkeypatch.setattr("app.services.tennis.openai_browser.HttpxBrowserTransport", refused)
    hosted = OpenAIHostedBrowser(
        Settings(_env_file=None, openai_api_key="present"),
        session_file=tmp_path / "session.json",
    )
    await hosted.start()
    assert hosted.public_status()["connected"] is False
    assert await hosted.observe("read the Kalshi page") is None
