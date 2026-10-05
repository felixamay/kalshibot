"""Helpers for a read-only Kalshi page reading.

The live app does not start this session. Prices and orders use the Kalshi API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from types import SimpleNamespace
from urllib.request import Request, urlopen

from app.services.signals.ai_patterns import pattern_result_label
from app.services.tennis.service_games import ServiceGameTracker

logger = logging.getLogger(__name__)

_ALLOWED_ACTIONS = {"screenshot", "scroll", "move", "wait"}
_ORDER_WORDS = ("buy", "sell", "submit", "place order", "confirm order")
_KALSHI_HOSTS = ("https://kalshi.com/", "https://www.kalshi.com/", "https://kalshi.com", "https://www.kalshi.com")


def action_allowed(action: dict) -> bool:
    """Read-only computer actions. Clicks, typing, and order controls are refused."""
    if not isinstance(action, dict):
        return False
    kind = str(action.get("type") or "").lower()
    blob = json.dumps(action).casefold()
    if any(word in blob for word in _ORDER_WORDS):
        return False
    if kind in {"click", "double_click", "drag", "type", "keypress"}:
        return False
    if kind == "navigate":
        url = str(action.get("url") or "")
        return url.startswith("https://kalshi.com/") or url.startswith("https://www.kalshi.com/")
    return kind in _ALLOWED_ACTIONS


_POINT_RANK = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4}
_POINT_LABEL = {0: "0", 1: "15", 2: "30", 3: "40", 4: "AD"}


def parse_kalshi_board(text: str):
    """Sets and point score from the Kalshi match page text."""
    if not text:
        return None
    point = re.search(r"\b(0|15|30|40|AD)\s*[-–−]\s*(0|15|30|40|AD)\b", text, re.I)
    numeric = []
    for left, right in re.findall(r"\b(\d{1,2})\s*[-–−]\s*(\d{1,2})\b", text):
        if int(left) > 7 or int(right) > 7:
            continue
        if point and {left, right} <= {"0", "15", "30", "40"} and point.group(1) == left and point.group(2) == right:
            continue
        numeric.append((int(left), int(right)))
    sets = numeric[0] if numeric else None
    games = numeric[-1] if len(numeric) > 1 else None
    if point is None and sets is None:
        return None
    points = None
    if point:
        points = (_POINT_RANK[point.group(1).upper()], _POINT_RANK[point.group(2).upper()])
    return SimpleNamespace(sets=sets, points=points, games=games)


def _mentions_server(text: str, name: str) -> bool:
    if not text or not name:
        return False
    names = [name]
    last = name.split()[-1]
    if len(last) >= 3 and last.casefold() != name.casefold():
        names.append(last)
    for candidate in names:
        if re.search(rf"(?i)(?:server|serving)[:\s]+{re.escape(candidate)}\b", text):
            return True
    return False


def visible_server(text: str, player_a: str, player_b: str):
    """A or B when the Kalshi page names the server."""
    a = _mentions_server(text, player_a)
    b = _mentions_server(text, player_b)
    if a and not b:
        return "A"
    if b and not a:
        return "B"
    return None


def format_visual_score(board) -> str:
    parts = []
    if board.sets:
        parts.append(f"{board.sets[0]}-{board.sets[1]}")
    games = getattr(board, "games", None)
    if games:
        parts.append(f"{games[0]}-{games[1]}")
    if board.points:
        parts.append(f"{_POINT_LABEL[board.points[0]]}-{_POINT_LABEL[board.points[1]]}")
    return " ".join(parts)


def apply_visual_score(ctx, board, counter, page_text: str) -> None:
    """Attach the Kalshi page reading. Does not call ESPN."""
    from app.services.tennis.provider import TennisLiveState

    score = format_visual_score(board)
    server = visible_server(page_text, ctx.player_a, ctx.player_b)
    server_name = ctx.player_a if server == "A" else ctx.player_b if server == "B" else "unavailable"
    logger.info("GPT SCORE READ: %s", score)
    logger.info("CURRENT SERVER: %s", server_name)
    logger.info("SERVICE GAME COUNT: %s", counter.label)
    sets = f"{board.sets[0]}-{board.sets[1]}" if board.sets else None
    points = None
    if board.points:
        points = f"{_POINT_LABEL[board.points[0]]}-{_POINT_LABEL[board.points[1]]}"
    games = getattr(board, "games", None) or (counter.games if counter.games != (0, 0) else None)
    game_score = f"{games[0]}-{games[1]}" if games else None
    existing = getattr(ctx, "tennis", None)
    state = TennisLiveState(
        match_external_id=str(getattr(ctx, "match_id", "") or "kalshi-visual"),
        player_a=ctx.player_a,
        player_b=ctx.player_b,
        tournament=getattr(ctx, "tournament", None),
        server=server,
        point_score=points,
        game_score=game_score,
        set_score=score or None,
        match_score=sets,
        available=bool(score),
        source="kalshi_visual",
        source_url=None,
    )
    kept = existing is not None and getattr(existing, "source", "") not in ("espn", "kalshi_visual", "none", "")
    kept = kept and "espn.com" not in str(getattr(existing, "source_url", "") or "")
    if kept:
        state.point_events = list(getattr(existing, "point_events", []) or [])
        state.point_feed_available = bool(getattr(existing, "point_feed_available", False))
        state.point_feed_note = getattr(existing, "point_feed_note", state.point_feed_note)
        state.point_feed_quality = getattr(existing, "point_feed_quality", state.point_feed_quality)
        state.point_feed_basis = getattr(existing, "point_feed_basis", state.point_feed_basis)
        if state.server is None and getattr(existing, "server", None) in ("A", "B"):
            state.server = existing.server
    ctx.tennis = state


def mark_score_unavailable(ctx) -> None:
    """The page was read and has no score. Do not call ESPN."""
    from app.services.tennis.provider import TennisLiveState

    existing = getattr(ctx, "tennis", None)
    source = str(getattr(existing, "source", "") or "")
    url = str(getattr(existing, "source_url", "") or "")
    if existing is not None and getattr(existing, "available", False) and source not in ("espn", "kalshi_visual", "none", "") and "espn.com" not in url:
        return
    ctx.tennis = TennisLiveState(
        match_external_id=str(getattr(ctx, "match_id", "") or "kalshi-visual"),
        player_a=getattr(ctx, "player_a", "") or "",
        player_b=getattr(ctx, "player_b", "") or "",
        available=False,
        source="kalshi_visual",
        source_url=None,
    )


def _service_game_winner(previous, current):
    """A point score that returns to the start of the next game."""
    if not previous or not current:
        return None
    if current not in ((0, 0), (1, 0), (0, 1)):
        return None
    left, right = previous
    if left >= 3 and left >= right + 1:
        return "A"
    if right >= 3 and right >= left + 1:
        return "B"
    return None


def parse_visible_score(text: str, player_a: str, player_b: str):
    """Games and sets from text the browser is actually showing."""
    if not text:
        return None
    pairs = []
    for a, b in re.findall(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b", text):
        left, right = int(a), int(b)
        if left <= 7 and right <= 7:
            pairs.append((left, right))
    if not pairs:
        return None
    games = pairs[-1]
    completed = pairs[:-1]
    sets_a = sum(1 for left, right in completed if left > right)
    sets_b = sum(1 for left, right in completed if right > left)
    server = None
    if player_a and re.search(rf"(?i)server[:\s]+{re.escape(player_a)}", text):
        server = "A"
    elif player_b and re.search(rf"(?i)server[:\s]+{re.escape(player_b)}", text):
        server = "B"
    return SimpleNamespace(
        available=True,
        game_score=f"{games[0]}-{games[1]}",
        match_score=f"{sets_a}-{sets_b}",
        server=server,
        is_tiebreak=games == (6, 6),
    )


class VisualServiceCounter:
    def __init__(self):
        self.tracker = ServiceGameTracker("visual")
        self.shown = 0
        self.games = (0, 0)
        self.sets = None
        self.points = None
        self._board_games = None
        self._seeded = False

    def observe_board(self, board, now):
        if board is None:
            return [], []
        if not self._seeded:
            self._seeded = True
            self.sets = board.sets
            self.points = board.points
            self._board_games = board.games
            # The first visible game score is the baseline. Games already
            # finished before this reading are not invented.
            if board.games:
                self.games = board.games
            return self.note(self._board_state(board), now)
        self._advance(board)
        self.sets = board.sets
        self.points = board.points
        self._board_games = board.games
        return self.note(self._board_state(board), now)

    def _advance(self, board):
        """One completed game from the displayed game score, or from the point score."""
        if board.games and self._board_games:
            delta = (board.games[0] - self._board_games[0], board.games[1] - self._board_games[1])
            if board.sets == self.sets and delta in ((1, 0), (0, 1)):
                self.games = board.games
                return
            if (board.sets and self.sets and sum(board.sets) == sum(self.sets) + 1
                    and board.games in ((0, 0), (1, 0), (0, 1))):
                self.games = board.games
                return
        winner = None
        if board.sets and self.sets and board.sets != self.sets and not (board.games and self._board_games):
            winner = "A" if board.sets[0] > self.sets[0] else "B" if board.sets[1] > self.sets[1] else None
        elif not (board.games and self._board_games and board.games != self._board_games):
            winner = _service_game_winner(self.points, board.points)
        if winner:
            self.games = (self.games[0] + (winner == "A"), self.games[1] + (winner == "B"))

    def _board_state(self, board):
        sets = board.sets or (0, 0)
        return SimpleNamespace(
            available=True,
            game_score=f"{self.games[0]}-{self.games[1]}",
            match_score=f"{sets[0]}-{sets[1]}",
            server=None,
            is_tiebreak=False,
        )

    def note(self, state, now):
        before = len(self.tracker.games)
        blocks = self.tracker.update(state, now)
        added = len(self.tracker.games) - before
        lines = []
        if added:
            logger.info("SERVICE GAME EVENTS: %s", len(self.tracker.games))
        if added >= 1 and (not blocks or added >= 2):
            lines.append("SERVICE GAME 1/2")
            self.shown = max(self.shown, 1)
        if blocks:
            lines.append("SERVICE GAME 2/2")
            lines.append("GPT PATTERN ANALYSIS STARTED")
            self.shown = 2
        elif added and self.shown < 2:
            self.shown = min(2, len(self.tracker.pending) or self.shown)
        return blocks, lines

    @property
    def label(self) -> str:
        return f"{self.shown}/2"


class KalshiVisualSession:
    def __init__(self, settings, engine=None):
        self.settings = settings
        self.engine = engine
        self.gpt_connected = False
        self.browser_open = False
        self.viewing = None
        self.viewing_id = None
        self.reason = "GPT browser is not connected"
        self.last_analysis_ms = None
        self.last_result = "WAIT"
        self.no_signal_reason = "Waiting for two completed service games"
        self.counter = VisualServiceCounter()
        self._proc = None
        self._cdp = None
        self._port = 9341
        self._stop = asyncio.Event()
        self._page_text = ""
        self._screenshot = ""
        self._cursor = 0
        from app.services.tennis.openai_browser import OpenAIHostedBrowser
        self.hosted = OpenAIHostedBrowser(settings)

    def diagnostics(self) -> dict:
        status = self.hosted.public_status()
        self.gpt_connected = status["connected"]
        if not status["connected"] and status.get("error"):
            self.reason = status["error"]
        return {
            "gpt_browser_connected": "YES" if status["connected"] else "NO",
            "gpt_browser_error": status.get("error"),
            "gpt_viewing": self.viewing or "—",
            "service_games_counted": self.counter.label,
            "last_gpt_analysis_ms": self.last_analysis_ms,
            "last_pattern_result": self.last_result or "WAIT",
            "no_signal_reason": self.no_signal_reason or "Waiting for two completed service games",
        }

    async def start(self) -> None:
        if not getattr(self.settings, "openai_api_key", ""):
            self.gpt_connected = False
            self.browser_open = False
            self.reason = "OPENAI_API_KEY is not configured"
            return
        try:
            await self.hosted.start()
        except Exception as exc:
            logger.warning("OpenAI browser: %s", type(exc).__name__)
        status = self.hosted.public_status()
        self.gpt_connected = status["connected"]
        self.browser_open = status["connected"]
        if not status["connected"]:
            self.reason = status.get("error") or self.reason
        elif not self.viewing:
            self.viewing = "kalshi.com"

    async def stop(self) -> None:
        self._stop.set()
        if self._cdp is not None:
            try:
                await self._cdp.close()
            except Exception:
                pass
            self._cdp = None
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                self._proc.kill()
        self.browser_open = False
        self.gpt_connected = False
        try:
            await self.hosted.aclose()
        except Exception:
            pass

    async def watch(self, matches) -> None:
        if not matches:
            return
        status = self.hosted.public_status()
        self.gpt_connected = status["connected"]
        self.browser_open = status["connected"]
        if not self.browser_open:
            await self.start()
        if not self.browser_open:
            self.no_signal_reason = self.reason or "GPT browser is not connected"
            return
        await self._watch_one(matches)

    async def _watch_one(self, matches) -> bool:
        ctx = matches[self._cursor % len(matches)]
        label = f"{ctx.player_a} vs {ctx.player_b}"
        if self.viewing_id != ctx.match_id:
            self.counter = VisualServiceCounter()
            self.viewing_id = ctx.match_id
            self.viewing = label
            if self.browser_open:
                self.last_result = "WAIT"
                self.no_signal_reason = "Waiting for two completed service games"
        event = getattr(ctx, "event_id", None) or str(getattr(ctx, "market_ticker", "")).rsplit("-", 1)[0]
        series = event.split("-")[0].lower()
        url = f"https://kalshi.com/markets/{series}/{event.lower()}"
        text = await self.hosted.observe(
            "Open "
            + url
            + " in this read-only browser. The match is "
            + label
            + ". Do not click Buy or Sell. Do not type. Do not submit an order. "
            + "Read the visible live score and reply with the set score, game score, and point score "
            + "as digits separated by hyphens, plus a line Server: and the server's name."
        )
        if not text:
            return False
        self._page_text = text
        if re.search(r"Begins (?:on|in)|not started", self._page_text or "", re.I) and not parse_kalshi_board(self._page_text):
            self._cursor += 1
            self.viewing_id = None
            self.no_signal_reason = "Opened match has not started; moving to the next live market"
            return True
        board = parse_kalshi_board(self._page_text)
        if board is not None:
            blocks, lines = self.counter.observe_board(board, time.time() * 1000)
            apply_visual_score(ctx, board, self.counter, self._page_text)
            for line in lines:
                logger.info("%s", line)
            if "GPT PATTERN ANALYSIS STARTED" in lines:
                label, reason = await self.conclude(ctx, blocks[-1] if blocks else None)
                self.last_result = label
                self.last_analysis_ms = time.time() * 1000
                self.no_signal_reason = reason
            elif self.counter.shown == 0:
                self.no_signal_reason = "Waiting for two completed service games"
            if self.engine is not None and getattr(ctx, "market_ticker", None):
                await self.engine.on_tennis_update(ctx.market_ticker)
            return
        state = parse_visible_score(self._page_text, ctx.player_a, ctx.player_b)
        if state is None:
            if getattr(getattr(ctx, "tennis", None), "source", None) != "kalshi_visual":
                mark_score_unavailable(ctx)
            self.no_signal_reason = "Score is not visible in the ChatGPT browser view yet"
            return False
        await self.note_score(state, ctx)

    async def note_score(self, state, ctx=None):
        blocks, lines = self.counter.note(state, time.time() * 1000)
        for line in lines:
            logger.info("%s", line)
        if "GPT PATTERN ANALYSIS STARTED" not in lines:
            return None
        label, reason = await self.conclude(ctx, blocks[-1] if blocks else None)
        self.last_result = label
        self.last_analysis_ms = time.time() * 1000
        self.no_signal_reason = reason
        return label

    async def conclude(self, ctx, block) -> tuple[str, str]:
        """Always returns YES — player, WATCH, WAIT, or NO RELIABLE PATTERN."""
        visual = None
        if getattr(self.settings, "openai_api_key", "") and self.browser_open:
            visual = await self.inspect(ctx)
        if not visual:
            reason = self.reason if not self.gpt_connected else "The computer-use session did not return a match reading"
            if not getattr(self.settings, "openai_api_key", ""):
                reason = "OPENAI_API_KEY is not configured"
            return "NO RELIABLE PATTERN", reason
        favored = visual.get("favored_side")
        if not visual.get("pattern_name") or favored not in ("PLAYER_A", "PLAYER_B"):
            return "NO RELIABLE PATTERN", visual.get("reason") or "No reliable pattern on the displayed match"
        if visual.get("recommendation") == "WAIT" or visual.get("pattern_stage") == "BROKEN":
            return "WAIT" if visual.get("recommendation") == "WAIT" else "NO RELIABLE PATTERN", visual.get("reason") or "GPT did not confirm a current pattern"
        if ctx is None or self.engine is None:
            return _watch_label(visual, ctx), "GPT sees a pattern; live Kalshi numbers are not attached"
        market = self._market(ctx)
        if market is None:
            return _watch_label(visual, ctx), "GPT sees a pattern; live Kalshi numbers are not attached"
        if market.data_age_ms > self.settings.max_data_age_ms or str(market.status).upper() != "OPEN":
            return "WATCH", "GPT sees a pattern; the live Kalshi quote is stale"
        now = time.time() * 1000
        output = {
            "pattern_name": visual.get("pattern_name"),
            "favored_side": favored,
            "pattern_stage": visual.get("pattern_stage") if visual.get("pattern_stage") in ("EARLY", "DEVELOPING", "MATURE", "LATE", "BROKEN") else "DEVELOPING",
            "pattern_confidence": float(visual.get("pattern_confidence") or 0),
            "recommendation": visual.get("recommendation") or "WATCH",
            "ideal_entry_low": visual.get("ideal_entry_low"),
            "ideal_entry_high": visual.get("ideal_entry_high"),
            "maximum_entry": visual.get("maximum_entry"),
            "reasons": list(visual.get("reasons") or [])[:8],
            "risks": list(visual.get("risks") or [])[:8],
            "entry_score": float(visual.get("pattern_confidence") or 0),
            "pattern_invalidation_price": visual.get("pattern_invalidation_price"),
        }
        from app.services.signals.engine import market_yes_player
        from app.services.signals.patterns import PatternAssessment
        yes_player = market_yes_player(ctx, market)
        yes_side = "PLAYER_A" if yes_player == ctx.player_a else "PLAYER_B"
        view = ctx.last_pattern or PatternAssessment(decision="SEARCHING", entry_score=0)
        ai = {
            "status": "VALID",
            "output": output,
            "timestamp": now,
            "gpt_snapshot_time": now,
            "gpt_response_time": now,
            "kalshi_price_at_snapshot": market.mid / 100,
        }
        decision = self.engine.hybrid_engine.decide(
            view, ai, market, yes_side, match_id=ctx.match_id, player_a=ctx.player_a, player_b=ctx.player_b,
        )
        ctx.hybrid_decision = decision
        ctx.ai_analysis = ai
        signal = decision.get("signal") or {}
        label = pattern_result_label(signal.get("final") or "", signal.get("player_name") or "")
        if label.startswith("YES —"):
            return label, "—"
        if label.startswith("WATCH"):
            return label, "GPT sees a pattern; live Kalshi numbers do not confirm the direction"
        if label == "WAIT":
            return label, signal.get("message") or "GPT recommended waiting"
        return "NO RELIABLE PATTERN", signal.get("message") or "No reliable pattern on the displayed match"

    def _market(self, ctx):
        analyzers = getattr(getattr(self.engine, "snap", None), "analyzers", {})
        analyzer = analyzers.get(ctx.market_ticker)
        return analyzer.state if analyzer else None

    async def inspect(self, ctx) -> dict | None:
        """One read-only pass over the ChatGPT browser's current Kalshi page."""
        if not self.hosted.public_status()["connected"]:
            self.gpt_connected = False
            self.reason = "GPT browser is not connected"
            return None
        players = ""
        if ctx is not None:
            players = f" Player A is {ctx.player_a}. Player B is {ctx.player_b}."
        prompt = (
            "You are inspecting the Kalshi page already open in this read-only browser. "
            "Do not click Buy or Sell. Do not type. Do not submit an order. "
            "Look only. Return JSON with players, live_score, set_game_state, current_server, "
            "visible_market_prices, chart_movement, pattern_name, favored_side, pattern_stage, "
            "pattern_confidence, recommendation, ideal_entry_low, ideal_entry_high, maximum_entry, "
            "reasons, risks, reason. favored_side is PLAYER_A, PLAYER_B, or NEITHER. "
            "Use NEITHER when no current pattern is visible." + players
        )
        from app.services.signals.ai_patterns import note_gpt_analysis_call
        note_gpt_analysis_call()
        text = await self.hosted.observe(prompt)
        if not text:
            self.gpt_connected = False
            self.reason = self.hosted.error or "The ChatGPT browser did not return a match reading"
            return None
        self.gpt_connected = True
        self.reason = ""
        self._page_text = f"{self._page_text}\n{text}"
        parsed = _json_object(text)
        if parsed:
            score = parsed.get("live_score") or parsed.get("set_game_state")
            if score and ctx is not None:
                self._page_text = f"{self._page_text}\n{score}\nServer: {parsed.get('current_server') or ''}"
        return parsed

    async def _computer_loop(self, prompt: str) -> str:
        raise RuntimeError("OpenAI computer use is disabled")

    async def _perform(self, action: dict) -> None:
        kind = str(action.get("type") or "").lower()
        if kind == "navigate":
            await self._navigate(str(action.get("url")))
        elif kind == "scroll" and self._cdp is not None:
            dy = int(action.get("scroll_y") or action.get("y") or 400)
            await self._cdp.call("Runtime.evaluate", {"expression": f"window.scrollBy(0,{dy})", "returnByValue": True})
        elif kind == "wait":
            await asyncio.sleep(min(float(action.get("ms") or 500) / 1000, 1.0))

    async def _launch_browser(self) -> None:
        import os
        args = [
            "google-chrome",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--remote-debugging-port={self._port}",
            "--user-data-dir=/tmp/kalshi-readonly-browser",
            "--no-first-run",
            "--window-size=1280,800",
            "about:blank",
        ]
        if not os.environ.get("DISPLAY"):
            args[1:1] = ["--headless=new", "--disable-gpu"]
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        version = None
        for _ in range(30):
            if self._proc.returncode is not None:
                raise RuntimeError("chrome exited")
            try:
                with urlopen(Request(f"http://127.0.0.1:{self._port}/json/version"), timeout=1) as resp:
                    version = json.loads(resp.read().decode())
                    break
            except Exception:
                await asyncio.sleep(0.2)
        if not version:
            raise RuntimeError("chrome debugger did not open")
        await self._attach_page()

    async def _attach_page(self) -> None:
        with urlopen(Request(f"http://127.0.0.1:{self._port}/json/list"), timeout=2) as resp:
            pages = json.loads(resp.read().decode())
        page = next((item for item in pages if item.get("type") == "page" and item.get("webSocketDebuggerUrl")), None)
        if page is None:
            raise RuntimeError("chrome has no page")
        import websockets
        if self._cdp is not None:
            await self._cdp.close()
        self._cdp = _Cdp(await websockets.connect(page["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024))
        await self._cdp.call("Page.enable")
        await self._cdp.call("Runtime.enable")

    async def _navigate(self, url: str) -> None:
        if not (url.startswith("https://kalshi.com") or url.startswith("https://www.kalshi.com")):
            return
        if self._cdp is None:
            await self._attach_page()
        try:
            await self._cdp.call("Page.navigate", {"url": url})
        except Exception:
            await self._attach_page()
            await self._cdp.call("Page.navigate", {"url": url})
        await asyncio.sleep(1.5)
        await self._attach_page()

    async def _read_text(self) -> str:
        if self._cdp is None:
            await self._attach_page()
        try:
            result = await self._cdp.call("Runtime.evaluate", {
                "expression": "document.body ? document.body.innerText.slice(0, 8000) : ''",
                "returnByValue": True,
            })
        except Exception:
            await self._attach_page()
            result = await self._cdp.call("Runtime.evaluate", {
                "expression": "document.body ? document.body.innerText.slice(0, 8000) : ''",
                "returnByValue": True,
            })
        return str(((result or {}).get("result") or {}).get("value") or "")

    async def _capture(self) -> str:
        if self._cdp is None:
            return self._screenshot
        result = await self._cdp.call("Page.captureScreenshot", {"format": "png"})
        data = (result or {}).get("data") or ""
        if data:
            self._screenshot = data
        return data or self._screenshot


class _Cdp:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def call(self, method: str, params: dict | None = None):
        self._id += 1
        ident = self._id
        await self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        while True:
            raw = json.loads(await self.ws.recv())
            if raw.get("id") == ident:
                if raw.get("error"):
                    raise RuntimeError(raw["error"].get("message") or "cdp error")
                return raw.get("result") or {}

    async def close(self):
        await self.ws.close()


def _watch_label(visual: dict, ctx) -> str:
    side = visual.get("favored_side")
    name = ""
    if ctx is not None and side == "PLAYER_A":
        name = ctx.player_a
    elif ctx is not None and side == "PLAYER_B":
        name = ctx.player_b
    return f"WATCH — {name}" if name else "WATCH"


def _json_object(text: str) -> dict | None:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
