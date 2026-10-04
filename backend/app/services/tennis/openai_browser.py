"""One persistent OpenAI-hosted browser for read-only Kalshi pages.

The session id stays on the server. Trading controls are never sent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_SESSION_FILE = Path("/tmp/courtedg-openai-browser-session.json")
_MODEL = "gpt-6-astra"
_TRADE_WORDS = ("buy", "sell", "submit", "place order", "confirm order", "confirm trade")
VERIFICATION_TASK = (
    "Open Kalshi and identify whether the page contains tennis markets. "
    "Do not interact with trading controls."
)
_INSTRUCTIONS = (
    "You are a read-only observer of public Kalshi pages. "
    "Open https://kalshi.com and tennis market pages. "
    "Read visible scores, player names, prices, and charts, and take screenshots. "
    "Never click Buy or Sell. Never submit an order. Never confirm a trade. "
    "Never access or move funds. Do not sign in and do not type into trading controls. "
    "If Kalshi asks for authentication, stop and say authentication is required."
)


def session_create_body(model: str = _MODEL) -> dict:
    return {
        "agent": {
            "model": model,
            "instructions": _INSTRUCTIONS,
            "tools": [{"type": "computer_use", "include_screenshots": True}],
        },
        "environment": {
            "type": "openai_hosted",
            "desktop": {"enabled": True},
            "network": {"access": "enabled"},
        },
    }


def user_message(text: str) -> dict:
    return {
        "type": "agent.session.input.message",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
    }


def origin_allowed(origin: str) -> bool:
    value = (origin or "").strip()
    if not value.startswith("https://"):
        return False
    host = value.split("/", 3)[2].split(":")[0].lower()
    return host == "kalshi.com" or host.endswith(".kalshi.com")


def approval_event(request: dict, request_id: str) -> dict | None:
    """Origin approval for kalshi.com, or a cancelled sign-in. Never a trade click."""
    kind = str((request or {}).get("type") or "")
    if kind == "browser_origin_access":
        decision = "approve" if origin_allowed(str(request.get("origin") or "")) else "deny"
        return {
            "type": "agent.session.input.computer_use_approval_request_result",
            "request_id": request_id,
            "response": {"type": "browser_origin_access", "decision": decision},
        }
    if kind == "browser_authentication":
        return {
            "type": "agent.session.input.computer_use_approval_request_result",
            "request_id": request_id,
            "response": {"type": "browser_authentication", "action": "cancel"},
        }
    return None


def action_is_trade(item: dict) -> bool:
    action = item.get("action") if isinstance(item, dict) else None
    if not isinstance(action, dict):
        return False
    kind = str(action.get("type") or "").lower()
    if kind not in {"click", "double_click", "drag", "type", "keypress"}:
        return False
    blob = json.dumps(action).casefold()
    return any(word in blob for word in _TRADE_WORDS)


def environment_alive_from(payload: dict) -> bool:
    env = payload.get("environment") if isinstance(payload, dict) else None
    env = env or {}
    status = str(env.get("status") or env.get("state") or "").lower()
    if status in {"failed", "disconnected", "deleted", "error", "cancelled"}:
        return False
    if status in {"provisioning", "pending", "starting"}:
        return False
    if status in {"connected", "ready", "running", "active"}:
        return True
    session_status = str((payload or {}).get("status") or "").lower()
    if session_status in {"failed", "cancelled", "deleted", "disconnected"}:
        return False
    return bool((payload or {}).get("id"))


def error_text(status: int, payload: dict) -> str:
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])[:300]
    if isinstance(err, str) and err:
        return err[:300]
    return f"OpenAI browser request failed ({status})"


class HttpxBrowserTransport:
    def __init__(self, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.openai.com",
            timeout=httpx.Timeout(240.0, connect=20.0),
        )
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "OpenAI-Beta": "agents=v1",
            "Content-Type": "application/json",
        }

    async def request(self, method: str, path: str, json_body=None) -> tuple[int, dict]:
        response = await self._client.request(method, path, json=json_body, headers=self._headers)
        try:
            body = response.json()
        except Exception:
            body = {"error": {"message": (response.text or "")[:200]}}
        if not isinstance(body, dict):
            body = {"error": {"message": str(body)[:200]}}
        return response.status_code, body

    async def stream_events(self, path: str, opened: asyncio.Event):
        headers = {**self._headers, "Accept": "text/event-stream"}
        async with self._client.stream("GET", path, headers=headers) as response:
            opened.set()
            response.raise_for_status()
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    raw, buffer = buffer.split("\n\n", 1)
                    for line in raw.splitlines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data and data != "[DONE]":
                            try:
                                yield json.loads(data)
                            except json.JSONDecodeError:
                                continue

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAIHostedBrowser:
    def __init__(self, settings, transport=None, session_file: Path | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self.session_file = session_file or _SESSION_FILE
        self.session_id: str | None = None
        self.state = "DISCONNECTED"
        self.environment_alive = False
        self.kalshi_loaded = False
        self.last_successful_observation: str | None = None
        self.error: str | None = "GPT browser is not connected"
        self.trade_controls_activated = False
        self._handled: set[str] = set()
        self._owns_transport = transport is None
        self._lock = asyncio.Lock()

    def public_status(self) -> dict:
        connected = (
            self.state == "CONNECTED"
            and bool(self.session_id)
            and self.environment_alive
            and self.kalshi_loaded
        )
        return {
            "connected": connected,
            "session_id_present": bool(self.session_id),
            "kalshi_loaded": self.kalshi_loaded,
            "last_successful_observation": self.last_successful_observation,
            "error": None if connected else (self.error or self.state),
        }

    async def start(self) -> None:
        if not getattr(self.settings, "openai_api_key", ""):
            self.state = "ERROR"
            self.environment_alive = False
            self.error = "OPENAI_API_KEY is not configured"
            return
        async with self._lock:
            if self.public_status()["connected"]:
                return
            self.state = "CONNECTING"
            self.error = None
            try:
                await self._ensure_session()
                if self.session_id and self.environment_alive and not self.kalshi_loaded:
                    await self._verify()
                if self.session_id and self.environment_alive and self.kalshi_loaded:
                    self.state = "CONNECTED"
                    self.error = None
                    logger.info("GPT VISUAL ANALYST READY")
            except Exception as exc:
                self.state = "ERROR" if self.state != "DISCONNECTED" else "DISCONNECTED"
                self.error = self.error or f"{type(exc).__name__}: {exc}"
                self.error = str(self.error)[:300]
                logger.warning("OpenAI browser: %s", type(exc).__name__)

    async def recover(self) -> bool:
        """Reconnect the stored session. Does not create a replacement."""
        if not self.session_id:
            self._load()
        if not self.session_id:
            self.state = "DISCONNECTED"
            self.error = "No OpenAI browser session to recover"
            return False
        self.state = "DISCONNECTED"
        status, payload = await self._request("GET", f"/v1/agents/sessions/{self.session_id}")
        if status == 404:
            self.session_id = None
            self.environment_alive = False
            self._write()
            self.error = "OpenAI browser session is gone"
            return False
        if status >= 400:
            self.state = "ERROR"
            self.error = error_text(status, payload)
            return False
        self._apply_session(payload)
        await self._drain_approvals(payload)
        if self.environment_alive:
            self.error = None
            if self.kalshi_loaded:
                self.state = "CONNECTED"
            return True
        self.error = self.error or "OpenAI browser environment is not connected"
        return False

    async def aclose(self) -> None:
        if self._owns_transport and self.transport is not None:
            await self.transport.aclose()

    async def _ensure_session(self) -> None:
        if self.session_id is None:
            self._load()
        if self.session_id:
            recovered = await self.recover()
            if recovered or self.session_id:
                return
        self.state = "CONNECTING"
        status, payload = await self._request("POST", "/v1/agents/sessions", session_create_body())
        if status >= 400 or not payload.get("id"):
            self.state = "ERROR"
            self.error = error_text(status, payload)
            raise RuntimeError(self.error)
        self.session_id = str(payload["id"])
        self._apply_session(payload)
        logger.info("GPT BROWSER SESSION CREATED")
        self._write()
        for _ in range(10):
            if self.environment_alive:
                return
            await asyncio.sleep(0.5)
            status, payload = await self._request("GET", f"/v1/agents/sessions/{self.session_id}")
            if status < 400:
                self._apply_session(payload)
        if not self.environment_alive:
            self.state = "ERROR"
            self.error = "OpenAI browser environment did not become ready"
            raise RuntimeError(self.error)

    async def _verify(self) -> None:
        self.state = "CONNECTING"
        opened = asyncio.Event()
        queue: asyncio.Queue = asyncio.Queue()

        async def reader() -> None:
            try:
                async for event in self.transport.stream_events(
                    f"/v1/agents/sessions/{self.session_id}/events", opened
                ):
                    await queue.put(event)
            except Exception as exc:
                await queue.put({"type": "error", "error": {"message": type(exc).__name__}})
            await queue.put(None)

        task = asyncio.create_task(reader())
        try:
            await asyncio.wait_for(opened.wait(), timeout=30)
            await self._post_events([user_message(VERIFICATION_TASK)])
            finished = False
            while not finished:
                event = await asyncio.wait_for(queue.get(), timeout=210)
                if event is None:
                    break
                finished = await self._handle_event(event)
        except Exception as exc:
            self.state = "DISCONNECTED"
            self.error = f"OpenAI browser stream disconnected: {type(exc).__name__}"
            await self.recover()
            raise
        finally:
            task.cancel()
        if not (self.session_id and self.environment_alive and self.kalshi_loaded):
            self.state = "ERROR"
            self.error = self.error or "Kalshi page did not load in the OpenAI browser"
            raise RuntimeError(self.error)

    async def _handle_event(self, event: dict) -> bool:
        kind = str(event.get("type") or "")
        if action_is_trade(event):
            self.trade_controls_activated = False
            self.state = "ERROR"
            self.error = "Trading control refused"
            return True
        if kind == "agent.session.requires_action":
            self.state = "WAITING_FOR_ORIGIN_APPROVAL"
            status, payload = await self._request("GET", f"/v1/agents/sessions/{self.session_id}")
            if status < 400:
                self._apply_session(payload)
                await self._drain_approvals(payload)
            return False
        if kind == "agent.session.turn.output_text.done":
            self.last_successful_observation = str(event.get("text") or "")[:500]
            return False
        if kind == "agent.session.turn.completed":
            turn = event.get("turn") or {}
            if turn.get("subagent_id") is not None:
                return False
            self._commit_observation()
            return True
        if kind in {"agent.session.turn.failed", "agent.session.turn.cancelled"}:
            turn = event.get("turn") or {}
            if turn.get("subagent_id") is None:
                self.error = f"OpenAI browser task ended: {kind}"
                raise RuntimeError(self.error)
            return False
        if kind in {"error", "agent.session.failed", "agent.session.environment.failed"}:
            message = ((event.get("error") or {}) if isinstance(event.get("error"), dict) else {}).get("message")
            self.error = str(message or kind)
            raise RuntimeError(self.error)
        return False

    def _commit_observation(self) -> None:
        text = self.last_successful_observation or ""
        lowered = text.casefold()
        blocked = any(phrase in lowered for phrase in (
            "authentication is required",
            "must sign in",
            "must log in",
            "login required",
            "sign-in required",
        ))
        saw_page = "kalshi.com" in lowered or "contains tennis" in lowered or "sports page" in lowered
        if saw_page:
            self.kalshi_loaded = True
            self.error = None
            self._write()
            logger.info("KALSHI PAGE LOADED")
            return
        if blocked:
            self.kalshi_loaded = False
            self.error = "Kalshi requested authentication; staying on public read-only access"

    async def _drain_approvals(self, payload: dict) -> None:
        for approval in payload.get("required_actions") or []:
            if not isinstance(approval, dict) or approval.get("type") != "computer_use_approval_request":
                continue
            request_id = str(approval.get("request_id") or "")
            if not request_id or request_id in self._handled:
                continue
            request = approval.get("request") or {}
            event = approval_event(request, request_id)
            if event is None:
                continue
            if request.get("type") == "browser_origin_access":
                self.state = "WAITING_FOR_ORIGIN_APPROVAL"
            await self._post_events([event])
            self._handled.add(request_id)
            if event["response"].get("decision") == "approve":
                logger.info("KALSHI ORIGIN APPROVED")

    def _apply_session(self, payload: dict) -> None:
        if payload.get("id"):
            self.session_id = str(payload["id"])
        self.environment_alive = environment_alive_from(payload)

    async def _post_events(self, events: list[dict]) -> None:
        for event in events:
            if action_is_trade(event):
                self.trade_controls_activated = False
                raise RuntimeError("Trading control refused")
        status, payload = await self._request(
            "POST", f"/v1/agents/sessions/{self.session_id}/events", {"events": events}
        )
        if status >= 400:
            self.error = error_text(status, payload)
            raise RuntimeError(self.error)

    async def _request(self, method: str, path: str, json_body=None) -> tuple[int, dict]:
        self._transport()
        return await self.transport.request(method, path, json_body)

    def _transport(self):
        if self.transport is None:
            self.transport = HttpxBrowserTransport(self.settings.openai_api_key)
            self._owns_transport = True
        return self.transport

    def _load(self) -> None:
        try:
            payload = json.loads(self.session_file.read_text())
        except Exception:
            return
        if isinstance(payload, dict) and payload.get("session_id"):
            self.session_id = str(payload["session_id"])
            self.kalshi_loaded = bool(payload.get("kalshi_loaded"))
            self.last_successful_observation = payload.get("last_successful_observation")

    def _write(self) -> None:
        if not self.session_id:
            return
        self.session_file.write_text(json.dumps({
            "session_id": self.session_id,
            "kalshi_loaded": self.kalshi_loaded,
            "last_successful_observation": self.last_successful_observation,
        }))
