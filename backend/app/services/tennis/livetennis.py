"""Live Tennis API scoreboard.

Live matches come from GET {base}/matches?status=live. A match is on the
board only while that feed says it is live, then it is linked to the Kalshi
contracts for those players.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.services.tennis.espn import same_player
from app.services.tennis.provider import TennisDataProvider, TennisLiveState

logger = logging.getLogger(__name__)

# The free key allows 100 reads a day. A 20-second poll burns that quota in
# about half an hour and then the live board goes empty. One read every
# 15 minutes lasts the day.
_MIN_POLL_SECONDS = 900.0
_DEFAULT_BASE = "https://api.livetennisapi.com/api/public/v1"
# A restart during a rate limit used to forget the match that was already live.
_SLATE_FILE = Path("/tmp/courtedg-livetennis-slate.json")
_SLATE_MAX_AGE_SECONDS = 30 * 60


@dataclass
class LiveTennisMatch:
    match_id: str
    player_a: str
    player_b: str
    tournament: Optional[str]
    sets: list[tuple[int, int]] = field(default_factory=list)
    point_score: Optional[str] = None
    match_score: Optional[str] = None
    server: Optional[str] = None  # "A" | "B" oriented to player_a


def parse_live_matches(payload: dict[str, Any]) -> list[LiveTennisMatch]:
    """Keep rows the feed marks live. Missing scores stay empty."""
    found: list[LiveTennisMatch] = []
    seen: set[str] = set()
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").lower() != "live":
            continue
        match_id = str(row.get("id") or "")
        if not match_id or match_id in seen:
            continue
        players = row.get("players") if isinstance(row.get("players"), dict) else {}
        name_a = _player_name(players.get("p1"))
        name_b = _player_name(players.get("p2"))
        if not name_a or not name_b:
            continue
        score = row.get("score") if isinstance(row.get("score"), dict) else {}
        seen.add(match_id)
        found.append(
            LiveTennisMatch(
                match_id=match_id,
                player_a=name_a,
                player_b=name_b,
                tournament=_text(row.get("tournament")),
                sets=_set_pairs(score.get("games")),
                point_score=_point_score(score.get("points")),
                match_score=_match_score(score.get("sets")),
                server=_server(score.get("server")),
            )
        )
    return found


def state_for_players(match: LiveTennisMatch, player_a: str, player_b: str) -> TennisLiveState:
    """Orient the score to Kalshi's YES player."""
    flip = same_player(player_a, match.player_b) and not same_player(player_a, match.player_a)
    sets = [(b, a) if flip else (a, b) for a, b in match.sets]
    set_score = " ".join(f"{a}-{b}" for a, b in sets) or None
    current = sets[-1] if sets else None
    point = _flip_pair(match.point_score) if flip else match.point_score
    match_score = _flip_pair(match.match_score) if flip else match.match_score
    server = match.server
    if flip and server:
        server = "B" if server == "A" else "A"
    return TennisLiveState(
        match_external_id=match.match_id,
        player_a=player_a,
        player_b=player_b,
        tournament=match.tournament,
        server=server,
        point_score=point,
        game_score=f"{current[0]}-{current[1]}" if current else None,
        set_score=set_score,
        match_score=match_score,
        available=True,
        source="livetennis",
    )


def _player_name(player: Any) -> str:
    if not isinstance(player, dict):
        return ""
    return str(player.get("name") or "").strip()


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _set_pairs(games: Any) -> list[tuple[int, int]]:
    """games is [p1 games per set, p2 games per set]."""
    if not isinstance(games, list) or len(games) < 2:
        return []
    left, right = games[0], games[1]
    if not isinstance(left, list) or not isinstance(right, list):
        return []
    pairs: list[tuple[int, int]] = []
    for a, b in zip(left, right):
        if a is None or b is None:
            continue
        pairs.append((int(a), int(b)))
    return pairs


def _point_score(points: Any) -> Optional[str]:
    if not isinstance(points, list) or len(points) < 2:
        return None
    if points[0] is None or points[1] is None:
        return None
    return f"{points[0]}-{points[1]}"


def _match_score(sets: Any) -> Optional[str]:
    if not isinstance(sets, list) or len(sets) < 2:
        return None
    if sets[0] is None or sets[1] is None:
        return None
    return f"{int(sets[0])}-{int(sets[1])}"


def _server(server: Any) -> Optional[str]:
    if server == 1:
        return "A"
    if server == 2:
        return "B"
    return None


def _flip_pair(score: Optional[str]) -> Optional[str]:
    if not score or "-" not in score:
        return score
    left, right = score.split("-", 1)
    return f"{right}-{left}"


class LiveTennisProvider(TennisDataProvider):
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        base = (self.settings.tennis_api_base_url or _DEFAULT_BASE).rstrip("/")
        self._base = base
        key = self.settings.tennis_api_key or ""
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={
                "Accept": "application/json",
                "X-API-Key": key,
                "Authorization": f"Bearer {key}" if key else "",
                "User-Agent": "CourtEdge/1.0",
            },
        )
        self._live: list[LiveTennisMatch] = []
        self._fetched_at = 0.0
        self._ok = False
        self._blocked_until = 0.0

    async def list_live_matches(self) -> Optional[list[TennisLiveState]]:
        fresh = await self._refresh()
        if fresh is None and not self._ok:
            return None
        return [state_for_players(match, match.player_a, match.player_b) for match in self._live]

    async def get_live_match(self, player_a: str, player_b: str) -> Optional[TennisLiveState]:
        from app.services.tennis.espn import same_match

        await self._refresh()
        for match in self._live:
            if same_match(player_a, player_b, match.player_a, match.player_b):
                return state_for_players(match, player_a, player_b)
        return TennisLiveState(
            match_external_id="unknown",
            player_a=player_a,
            player_b=player_b,
            available=False,
            source="livetennis",
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _refresh(self) -> Optional[list[LiveTennisMatch]]:
        now = time.time()
        interval = max(_MIN_POLL_SECONDS, float(self.settings.tennis_poll_interval_seconds or 0))
        if self._ok and (now - self._fetched_at) < interval:
            return self._live
        if now < self._blocked_until:
            return self._live if self._ok else None
        # A restart must not spend a daily call when the last live slate is still fresh.
        if not self._ok:
            self._restore_saved_slate()
            if self._ok and (now - self._fetched_at) < interval:
                return self._live
        if not self.settings.tennis_api_key:
            logger.warning("Live Tennis API key is not set")
            return self._live if self._ok else None
        try:
            collected = await self._fetch_pages()
        except Exception as exc:
            logger.warning("Live Tennis API failed: %s", exc)
            if self._ok and (now - self._fetched_at) < 180:
                return self._live
            return None
        if collected is None:
            if not self._ok:
                self._restore_saved_slate()
            return self._live if self._ok else None
        self._live = collected
        self._fetched_at = time.time()
        self._ok = True
        self._save_slate()
        logger.info("Live Tennis in-progress matches: %d", len(collected))
        return collected

    async def _fetch_pages(self) -> Optional[list[LiveTennisMatch]]:
        collected: list[LiveTennisMatch] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(5):
            response = await self._client.get(
                f"{self._base}/matches",
                params={"status": "live", "limit": 200, "offset": offset},
            )
            if response.status_code == 429:
                self._note_rate_limit(response)
                return None
            if response.status_code != 200:
                logger.warning("Live Tennis API returned %s", response.status_code)
                return None
            payload = response.json()
            for match in parse_live_matches(payload):
                if match.match_id in seen:
                    continue
                seen.add(match.match_id)
                collected.append(match)
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            if not meta.get("has_more"):
                break
            offset = int(meta.get("offset") or 0) + int(meta.get("limit") or 200)
        return collected

    def _save_slate(self) -> None:
        try:
            payload = {
                "saved_at": time.time(),
                "matches": [
                    {
                        "match_id": match.match_id,
                        "player_a": match.player_a,
                        "player_b": match.player_b,
                        "tournament": match.tournament,
                        "sets": [list(pair) for pair in match.sets],
                        "point_score": match.point_score,
                        "match_score": match.match_score,
                        "server": match.server,
                    }
                    for match in self._live
                ],
            }
            _SLATE_FILE.write_text(json.dumps(payload))
        except OSError as exc:
            logger.warning("Could not save the Live Tennis slate: %s", exc)

    def _restore_saved_slate(self) -> None:
        try:
            payload = json.loads(_SLATE_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return
        saved_at = float(payload.get("saved_at") or 0)
        if time.time() - saved_at > _SLATE_MAX_AGE_SECONDS:
            return
        matches: list[LiveTennisMatch] = []
        for row in payload.get("matches") or []:
            if not isinstance(row, dict):
                continue
            sets = []
            for pair in row.get("sets") or []:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    sets.append((int(pair[0]), int(pair[1])))
            matches.append(
                LiveTennisMatch(
                    match_id=str(row.get("match_id") or ""),
                    player_a=str(row.get("player_a") or ""),
                    player_b=str(row.get("player_b") or ""),
                    tournament=row.get("tournament"),
                    sets=sets,
                    point_score=row.get("point_score"),
                    match_score=row.get("match_score"),
                    server=row.get("server"),
                )
            )
        if not matches:
            return
        self._live = matches
        self._ok = True
        self._fetched_at = saved_at
        logger.info("Restored %d Live Tennis matches from the last slate", len(matches))

    def _note_rate_limit(self, response: httpx.Response) -> None:
        retry_after = response.headers.get("Retry-After")
        hold = 60.0
        if retry_after and retry_after.isdigit():
            hold = float(retry_after)
        try:
            body = response.json()
        except Exception:
            body = {}
        now = datetime.now(timezone.utc)
        resets = body.get("resets_at") if isinstance(body, dict) else None
        if isinstance(resets, str):
            try:
                when = datetime.fromisoformat(resets.replace("Z", "+00:00"))
                hold = max(hold, (when - now).total_seconds())
            except ValueError:
                pass
        next_day = now.replace(hour=0, minute=1, second=0, microsecond=0) + timedelta(days=1)
        hold = min(max(hold, 30.0), (next_day - now).total_seconds())
        self._blocked_until = time.time() + hold
        logger.warning(
            "Live Tennis API rate limit. Holding the last slate for %.0fs",
            hold,
        )
