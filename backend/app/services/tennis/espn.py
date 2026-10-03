"""ESPN tennis scoreboard.

Kalshi's occurrence time is not the on-court clock. A match is live only while
ESPN marks it in progress, then that match is linked to the Kalshi contracts
for those two players.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.services.tennis.provider import TennisDataProvider, TennisLiveState

logger = logging.getLogger(__name__)

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/tennis/{league}/scoreboard"
ESPN_MATCH_URL = "https://www.espn.com/tennis/match/_/id/{match_id}"
_LEAGUES = ("atp", "wta")
_NAME_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class EspnLiveMatch:
    match_id: str
    player_a: str
    player_b: str
    tournament: Optional[str]
    detail: str
    sets: list[tuple[int, int]] = field(default_factory=list)
    source_url: str = ""
    server_name: Optional[str] = None
    source: str = "espn"


def last_name(name: str) -> str:
    raw = (name or "").strip()
    if "," in raw:
        raw = raw.split(",", 1)[0]
    words = _NAME_WORD.findall(raw.lower())
    return words[-1] if words else ""


def _tokens(name: str) -> set[str]:
    return set(_NAME_WORD.findall((name or "").lower()))


def same_player(left: str, right: str) -> bool:
    """True when two feeds name the same player, including 'Last, First'."""
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return False
    if a == b or a <= b or b <= a:
        smaller = a if len(a) <= len(b) else b
        if len(smaller) >= 2:
            return True
    return bool(last_name(left)) and last_name(left) == last_name(right)


def _shares_long_token(left: str, right: str) -> bool:
    return any(len(token) >= 5 for token in (_tokens(left) & _tokens(right)))


def same_match(left_a: str, left_b: str, right_a: str, right_b: str) -> bool:
    straight = same_player(left_a, right_a) and same_player(left_b, right_b)
    swapped = same_player(left_a, right_b) and same_player(left_b, right_a)
    if straight or swapped:
        return True
    # A scoreboard sometimes shortens one player ("Martin") after the other already matched.
    loose = (
        same_player(left_a, right_a) and _shares_long_token(left_b, right_b)
        or same_player(left_b, right_b) and _shares_long_token(left_a, right_a)
        or same_player(left_a, right_b) and _shares_long_token(left_b, right_a)
        or same_player(left_b, right_a) and _shares_long_token(left_a, right_b)
    )
    return loose


def parse_scoreboard(payload: dict[str, Any], *, tournament: Optional[str] = None) -> list[EspnLiveMatch]:
    """Pull every competition ESPN currently marks in progress."""
    found: list[EspnLiveMatch] = []
    seen: set[str] = set()
    for comp, event_name in _competitions(payload):
        status = comp.get("status") if isinstance(comp.get("status"), dict) else {}
        kind = status.get("type") if isinstance(status.get("type"), dict) else {}
        if kind.get("state") != "in":
            continue
        match_id = str(comp.get("id") or "")
        if not match_id or match_id in seen:
            continue
        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            continue
        names: list[str] = []
        lines: list[list[int]] = []
        server_name: Optional[str] = None
        for competitor in competitors[:2]:
            athlete = competitor.get("athlete") if isinstance(competitor.get("athlete"), dict) else {}
            name = str(athlete.get("displayName") or athlete.get("fullName") or "")
            names.append(name)
            if competitor.get("possession") is True and name:
                server_name = name
            games: list[int] = []
            for score in competitor.get("linescores") or []:
                if isinstance(score, dict) and score.get("value") is not None:
                    games.append(int(float(score["value"])))
            lines.append(games)
        if not names[0] or not names[1]:
            continue
        width = min(len(lines[0]), len(lines[1]))
        sets = [(lines[0][i], lines[1][i]) for i in range(width)]
        seen.add(match_id)
        found.append(
            EspnLiveMatch(
                match_id=match_id,
                player_a=names[0],
                player_b=names[1],
                tournament=tournament or event_name,
                detail=str(kind.get("detail") or kind.get("shortDetail") or "In Progress"),
                sets=sets,
                source_url=ESPN_MATCH_URL.format(match_id=match_id),
                server_name=server_name,
            )
        )
    return found


def state_for_players(match: EspnLiveMatch, player_a: str, player_b: str) -> TennisLiveState:
    """Orient the set score to Kalshi's YES player."""
    flip = same_player(player_a, match.player_b) and not same_player(player_a, match.player_a)
    sets = [(b, a) if flip else (a, b) for a, b in match.sets]
    won_a = sum(1 for a, b in sets if _set_is_complete(a, b) and a > b)
    won_b = sum(1 for a, b in sets if _set_is_complete(a, b) and b > a)
    set_score = " ".join(f"{a}-{b}" for a, b in sets) or None
    current = sets[-1] if sets else None
    return TennisLiveState(
        match_external_id=match.match_id,
        player_a=player_a,
        player_b=player_b,
        tournament=match.tournament,
        game_score=f"{current[0]}-{current[1]}" if current else None,
        set_score=set_score,
        match_score=f"{won_a}-{won_b}",
        available=True,
        source=match.source or "espn",
        source_url=match.source_url,
        server=(
            "A"
            if match.server_name and same_player(match.server_name, player_a)
            else "B"
            if match.server_name
            else None
        ),
    )


def _set_is_complete(games_a: int, games_b: int) -> bool:
    leader, trailer = (games_a, games_b) if games_a >= games_b else (games_b, games_a)
    if leader < 6:
        return False
    return leader - trailer >= 2 or leader >= 7


def _competitions(node: Any, event_name: Optional[str] = None):
    if isinstance(node, dict):
        name = event_name
        if node.get("shortName") and "groupings" in node:
            name = str(node.get("shortName") or node.get("name") or event_name or "")
        if "competitors" in node and isinstance(node.get("status"), dict):
            yield node, name
        for value in node.values():
            yield from _competitions(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from _competitions(value, event_name)


class EspnTennisProvider(TennisDataProvider):
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": "CourtEdge/1.0", "Accept": "application/json"},
        )
        self._live: list[EspnLiveMatch] = []
        self._fetched_at = 0.0
        self._ok = False

    async def list_live_matches(self) -> Optional[list[TennisLiveState]]:
        fresh = await self._refresh()
        if fresh is None and not self._ok:
            return None
        return [
            state_for_players(match, match.player_a, match.player_b)
            for match in self._live
        ]

    async def get_live_match(self, player_a: str, player_b: str) -> Optional[TennisLiveState]:
        await self._refresh()
        for match in self._live:
            if same_match(player_a, player_b, match.player_a, match.player_b):
                return state_for_players(match, player_a, player_b)
        return TennisLiveState(
            match_external_id="unknown",
            player_a=player_a,
            player_b=player_b,
            available=False,
            source="espn",
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _refresh(self) -> Optional[list[EspnLiveMatch]]:
        interval = max(5.0, float(self.settings.tennis_poll_interval_seconds))
        if self._ok and (time.time() - self._fetched_at) < interval:
            return self._live
        collected: list[EspnLiveMatch] = []
        seen: set[str] = set()
        try:
            for league in _LEAGUES:
                response = await self._client.get(ESPN_SCOREBOARD.format(league=league))
                response.raise_for_status()
                for match in parse_scoreboard(response.json()):
                    if match.match_id in seen:
                        continue
                    seen.add(match.match_id)
                    collected.append(match)
        except Exception as exc:
            logger.warning("ESPN scoreboard failed: %s", exc)
            if self._ok and (time.time() - self._fetched_at) < 180:
                return self._live
            return None
        extra = await self._challenger_matches()
        for match in extra:
            if any(
                same_match(match.player_a, match.player_b, item.player_a, item.player_b)
                for item in collected
            ):
                continue
            collected.append(match)
        self._live = collected
        self._fetched_at = time.time()
        self._ok = True
        logger.info("In-progress matches: %d", len(collected))
        return collected

    async def _challenger_matches(self) -> list[EspnLiveMatch]:
        """Challengers that are on court but missing from the ESPN tour scoreboard."""
        from app.services.tennis.livescore import LIVESCORE_URL, livescore_dates, parse_livescore

        found: list[EspnLiveMatch] = []
        seen: set[str] = set()
        try:
            for date in livescore_dates():
                response = await self._client.get(LIVESCORE_URL.format(date=date))
                response.raise_for_status()
                for match in parse_livescore(response.text):
                    if match.match_id in seen:
                        continue
                    seen.add(match.match_id)
                    found.append(match)
        except Exception as exc:
            logger.warning("Live score page failed: %s", exc)
            return []
        logger.info("Other in-progress matches: %d", len(found))
        return found

