"""Tennis data provider — never invents missing statistics."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class TennisLiveState:
    match_external_id: str
    player_a: str
    player_b: str
    tournament: Optional[str] = None
    server: Optional[str] = None  # "A" | "B"
    point_score: Optional[str] = None
    game_score: Optional[str] = None
    set_score: Optional[str] = None
    match_score: Optional[str] = None
    aces_a: Optional[int] = None
    aces_b: Optional[int] = None
    double_faults_a: Optional[int] = None
    double_faults_b: Optional[int] = None
    break_points_a: Optional[int] = None
    break_points_b: Optional[int] = None
    first_serve_pct_a: Optional[float] = None
    first_serve_pct_b: Optional[float] = None
    # Leading side, 0–100. When set, this is the game percentage used for the 85% gate.
    game_percent: Optional[float] = None
    # Points left if the provider already knows. Last 5 serves blocks a clear winner.
    serves_remaining: Optional[int] = None
    recent_points: list[str] = field(default_factory=list)
    available: bool = False
    source: str = "none"
    source_url: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_external_id": self.match_external_id,
            "player_a": self.player_a,
            "player_b": self.player_b,
            "tournament": self.tournament,
            "server": self.server,
            "point_score": self.point_score,
            "game_score": self.game_score,
            "set_score": self.set_score,
            "match_score": self.match_score,
            "aces_a": self.aces_a,
            "aces_b": self.aces_b,
            "double_faults_a": self.double_faults_a,
            "double_faults_b": self.double_faults_b,
            "break_points_a": self.break_points_a,
            "break_points_b": self.break_points_b,
            "first_serve_pct_a": self.first_serve_pct_a,
            "first_serve_pct_b": self.first_serve_pct_b,
            "game_percent": self.game_percent,
            "serves_remaining": self.serves_remaining,
            "recent_points": self.recent_points,
            "available": self.available,
            "source": self.source,
            "source_url": self.source_url,
            "analysis_note": None if self.available else "MARKET-ONLY ANALYSIS",
        }


class TennisDataProvider(ABC):
    @abstractmethod
    async def get_live_match(self, player_a: str, player_b: str) -> Optional[TennisLiveState]:
        ...

    async def list_live_matches(self) -> Optional[list[TennisLiveState]]:
        """In-progress matches. None means the feed failed and the last board should stand."""
        return []

    @abstractmethod
    async def close(self) -> None:
        ...


class NullTennisProvider(TennisDataProvider):
    """Used when no tennis API key is configured — explicitly MARKET-ONLY."""

    async def get_live_match(self, player_a: str, player_b: str) -> Optional[TennisLiveState]:
        return TennisLiveState(
            match_external_id="unknown",
            player_a=player_a,
            player_b=player_b,
            available=False,
            source="none",
        )

    async def close(self) -> None:
        return None


class HttpTennisProvider(TennisDataProvider):
    """
    Generic HTTP tennis provider.

    Configure TENNIS_PROVIDER=custom|api_tennis|sportradar with
    TENNIS_API_BASE_URL and TENNIS_API_KEY.

    Expected endpoint (customizable): GET {base}/live?player_a=&player_b=
    Response JSON fields mapped when present; missing fields left as None.
    Never invents stats.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = httpx.AsyncClient(timeout=15.0)

    async def get_live_match(self, player_a: str, player_b: str) -> Optional[TennisLiveState]:
        if not self.settings.tennis_api_base_url or not self.settings.tennis_api_key:
            return TennisLiveState(
                match_external_id="unknown",
                player_a=player_a,
                player_b=player_b,
                available=False,
                source=self.settings.tennis_provider,
            )
        try:
            url = f"{self.settings.tennis_api_base_url.rstrip('/')}/live"
            resp = await self._client.get(
                url,
                params={"player_a": player_a, "player_b": player_b},
                headers={"Authorization": f"Bearer {self.settings.tennis_api_key}"},
            )
            if resp.status_code != 200:
                logger.warning("Tennis provider returned %s", resp.status_code)
                return TennisLiveState(
                    match_external_id="unknown",
                    player_a=player_a,
                    player_b=player_b,
                    available=False,
                    source=self.settings.tennis_provider,
                )
            data = resp.json()
            return TennisLiveState(
                match_external_id=str(data.get("id") or data.get("match_id") or "unknown"),
                player_a=data.get("player_a") or player_a,
                player_b=data.get("player_b") or player_b,
                tournament=data.get("tournament"),
                server=data.get("server"),
                point_score=data.get("point_score"),
                game_score=data.get("game_score"),
                set_score=data.get("set_score"),
                match_score=data.get("match_score"),
                aces_a=data.get("aces_a"),
                aces_b=data.get("aces_b"),
                double_faults_a=data.get("double_faults_a"),
                double_faults_b=data.get("double_faults_b"),
                break_points_a=data.get("break_points_a"),
                break_points_b=data.get("break_points_b"),
                first_serve_pct_a=data.get("first_serve_pct_a"),
                first_serve_pct_b=data.get("first_serve_pct_b"),
                game_percent=data.get("game_percent"),
                serves_remaining=data.get("serves_remaining"),
                recent_points=list(data.get("recent_points") or []),
                available=True,
                source=self.settings.tennis_provider,
            )
        except Exception as exc:
            logger.error("Tennis provider error: %s", exc)
            return TennisLiveState(
                match_external_id="unknown",
                player_a=player_a,
                player_b=player_b,
                available=False,
                source=self.settings.tennis_provider,
            )

    async def close(self) -> None:
        await self._client.aclose()


def create_tennis_provider(settings: Settings | None = None) -> TennisDataProvider:
    settings = settings or get_settings()
    name = (settings.tennis_provider or "espn").strip().lower()
    if name == "espn":
        from app.services.tennis.espn import EspnTennisProvider

        return EspnTennisProvider(settings)
    if name in ("none", "", "null"):
        return NullTennisProvider()
    if not settings.tennis_api_key:
        from app.services.tennis.espn import EspnTennisProvider

        return EspnTennisProvider(settings)
    return HttpTennisProvider(settings)
