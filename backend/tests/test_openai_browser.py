"""OpenAI-hosted browser session: create, approve, reuse, recover, never trade."""

import asyncio
import json

import pytest

from app.config import Settings
from app.services.tennis.openai_browser import (
    VERIFICATION_TASK,
    OpenAIHostedBrowser,
    action_is_trade,
    approval_event,
    session_create_body,
    user_message,
)


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.session = {
            "id": "sess_test",
            "environment": {"status": "connected"},
            "required_actions": [],
        }
        self.events = []
        self.opened = None
        self.get_status = 200

    async def request(self, method, path, json_body=None):
        self.calls.append((method, path, json_body))
        if method == "POST" and path == "/v1/agents/sessions":
            return 200, self.session
        if method == "GET" and path == "/v1/agents/sessions/sess_test":
            return self.get_status, self.session if self.get_status < 400 else {"error": {"message": "missing"}}
        if method == "POST" and path.endswith("/events"):
            event = json_body["events"][0]
            if event["type"] == "agent.session.input.message":
                self.session["required_actions"] = [{
                    "type": "computer_use_approval_request",
                    "request_id": "req_origin",
                    "request": {"type": "browser_origin_access", "origin": "https://kalshi.com", "reason": "open the page"},
                }]
            if event["type"] == "agent.session.input.computer_use_approval_request_result":
                self.session["required_actions"] = []
            return 202, {"ok": True}
        return 404, {"error": {"message": path}}

    async def stream_events(self, path, opened):
        opened.set()
        for event in self.events:
            yield event

    async def aclose(self):
        return None


def browser(tmp_path, transport=None):
    return OpenAIHostedBrowser(
        Settings(_env_file=None, openai_api_key="test-key"),
        transport=transport or FakeTransport(),
        session_file=tmp_path / "session.json",
    )


def test_session_body_enables_computer_use_desktop_and_network():
    body = session_create_body()
    assert body["agent"]["tools"] == [{"type": "computer_use", "include_screenshots": True}]
    assert body["environment"]["type"] == "openai_hosted"
    assert body["environment"]["desktop"]["enabled"] is True
    assert body["environment"]["network"]["access"] == "enabled"
    assert "Never click Buy" in body["agent"]["instructions"]


def test_origin_approval_and_navigation_task():
    approved = approval_event({"type": "browser_origin_access", "origin": "https://kalshi.com"}, "req_1")
    assert approved["response"] == {"type": "browser_origin_access", "decision": "approve"}
    denied = approval_event({"type": "browser_origin_access", "origin": "https://example.com"}, "req_2")
    assert denied["response"]["decision"] == "deny"
    message = user_message(VERIFICATION_TASK)
    text = message["input"][0]["content"][0]["text"]
    assert text == VERIFICATION_TASK
    assert "https://kalshi.com" not in text or "Open Kalshi" in text
    assert "trading controls" in text


def test_trading_controls_are_never_activated():
    click = {"type": "computer_use_call", "action": {"type": "click", "button": "buy"}}
    assert action_is_trade(click) is True
    assert action_is_trade({"type": "computer_use_call", "action": {"type": "screenshot"}}) is False
    auth = approval_event({"type": "browser_authentication", "fields": [{"id": "password"}]}, "req_auth")
    assert auth["response"] == {"type": "browser_authentication", "action": "cancel"}
    blob = json.dumps(auth)
    assert "password" not in blob
    assert "click" not in blob


@pytest.mark.asyncio
async def test_public_kalshi_page_counts_even_if_sign_in_is_declined(tmp_path):
    hosted = browser(tmp_path)
    await hosted._handle_event({
        "type": "agent.session.turn.output_text.done",
        "text": "I’ll open Kalshi and check whether tennis markets are visible, without interacting with trading controls.",
    })
    assert hosted.kalshi_loaded is False
    await hosted._handle_event({
        "type": "agent.session.turn.output_text.done",
        "text": "Yes. https://kalshi.com/category/sports contains tennis markets. An account prompt appeared, so authentication is required to continue. I did not interact with trading controls.",
    })
    finished = await hosted._handle_event({"type": "agent.session.turn.completed", "turn": {"subagent_id": None}})
    assert finished is True
    assert hosted.kalshi_loaded is True
    assert hosted.error is None


def test_status_hides_the_session_id(tmp_path):
    hosted = browser(tmp_path)
    hosted.session_id = "sess_secret"
    hosted.state = "CONNECTED"
    hosted.environment_alive = True
    hosted.kalshi_loaded = True
    hosted.last_successful_observation = "Kalshi lists tennis markets."
    hosted.error = None
    body = hosted.public_status()
    assert body == {
        "connected": True,
        "session_id_present": True,
        "kalshi_loaded": True,
        "last_successful_observation": "Kalshi lists tennis markets.",
        "error": None,
    }
    assert "sess_secret" not in json.dumps(body)


@pytest.mark.asyncio
async def test_creates_session_approves_kalshi_and_reuses_it(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO)
    transport = FakeTransport()
    transport.events = [
        {"type": "agent.session.requires_action"},
        {"type": "agent.session.turn.output_text.done", "text": "The Kalshi page contains tennis markets."},
        {"type": "agent.session.turn.completed", "turn": {"subagent_id": None}},
    ]
    hosted = browser(tmp_path, transport)
    await hosted.start()
    assert hosted.public_status()["connected"] is True
    assert hosted.session_id == "sess_test"
    creates = [call for call in transport.calls if call[0] == "POST" and call[1] == "/v1/agents/sessions"]
    assert len(creates) == 1
    approvals = [
        call for call in transport.calls
        if call[2] and call[2].get("events", [{}])[0].get("response", {}).get("decision") == "approve"
    ]
    assert approvals
    assert "GPT BROWSER SESSION CREATED" in caplog.text
    assert "KALSHI ORIGIN APPROVED" in caplog.text
    assert "KALSHI PAGE LOADED" in caplog.text
    assert "GPT VISUAL ANALYST READY" in caplog.text
    await hosted.start()
    creates = [call for call in transport.calls if call[0] == "POST" and call[1] == "/v1/agents/sessions"]
    assert len(creates) == 1
    assert hosted.trade_controls_activated is False


@pytest.mark.asyncio
async def test_observe_reuses_the_open_kalshi_browser(tmp_path):
    transport = FakeTransport()
    transport.events = [
        {"type": "agent.session.turn.output_text.done", "text": "5-7 4-5 30-15\nServer: Nico Hipfl"},
        {"type": "agent.session.turn.completed", "turn": {"subagent_id": None}},
    ]
    hosted = browser(tmp_path, transport)
    hosted.session_id = "sess_test"
    hosted.state = "CONNECTED"
    hosted.environment_alive = True
    hosted.kalshi_loaded = True
    text = await hosted.observe("Open https://kalshi.com/markets/kxatpmatch/e and read the score. Do not click Buy.")
    assert text is not None and "5-7" in text
    creates = [call for call in transport.calls if call[0] == "POST" and call[1] == "/v1/agents/sessions"]
    assert creates == []
    messages = [
        call for call in transport.calls
        if call[2] and call[2].get("events", [{}])[0].get("type") == "agent.session.input.message"
    ]
    assert messages
    assert "Buy" in messages[0][2]["events"][0]["input"][0]["content"][0]["text"]


@pytest.mark.asyncio
async def test_disconnect_recovers_the_same_session(tmp_path):
    transport = FakeTransport()
    hosted = browser(tmp_path, transport)
    hosted.session_id = "sess_test"
    hosted._write()
    hosted.session_id = None
    hosted.kalshi_loaded = False
    assert await hosted.recover() is True
    assert hosted.session_id == "sess_test"
    assert [call for call in transport.calls if call[1] == "/v1/agents/sessions"] == []
    transport.get_status = 404
    hosted.environment_alive = False
    assert await hosted.recover() is False
    assert hosted.session_id is None
