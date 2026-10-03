"""Application runtime — discovers markets, polls Kalshi, drives signal engine."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional
from uuid import uuid4

from app.config import Settings, get_settings
from app.core.enums import ConnectionStatus
from app.services.kalshi.client import KalshiReadOnlyClient
from app.services.kalshi.websocket import KalshiWebSocketClient
from app.services.signals.engine import SignalEngine
from app.services.tennis.provider import TennisDataProvider, create_tennis_provider

logger = logging.getLogger(__name__)


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price_to_cents(v: Any) -> Optional[float]:
    """Normalize Kalshi prices to cents (0-100).

    Supports legacy integer/float cents and new *_dollars fields (0-1).
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if 0 <= f <= 1.0:
        return round(f * 100.0, 2)
    return f


def extract_market_prices(m: dict[str, Any]) -> dict[str, Optional[float | str]]:
    yes_bid = _price_to_cents(
        m.get("yes_bid_dollars") if m.get("yes_bid_dollars") is not None else m.get("yes_bid")
    )
    yes_ask = _price_to_cents(
        m.get("yes_ask_dollars") if m.get("yes_ask_dollars") is not None else m.get("yes_ask")
    )
    last = _price_to_cents(
        m.get("last_price_dollars")
        if m.get("last_price_dollars") is not None
        else (m.get("last_price") or m.get("yes_price"))
    )
    volume = _to_float(m.get("volume_fp") or m.get("volume") or m.get("volume_24h_fp"))
    status_raw = (m.get("status") or "OPEN").upper()
    if status_raw in ("ACTIVE", "OPEN"):
        status = "OPEN"
    elif status_raw in ("INITIALIZED", "INACTIVE"):
        status = "SUSPENDED"
    else:
        status = status_raw
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "last_trade": last,
        "volume": volume,
        "status": status,
    }


def parse_players_from_market(market: dict[str, Any]) -> tuple[str, str, Optional[str]]:
    """Best-effort player extraction from Kalshi market fields."""
    title = (market.get("title") or "").strip()
    yes = (market.get("yes_sub_title") or "").strip()
    subtitle = (market.get("subtitle") or "").strip()
    event = (market.get("event_ticker") or "").strip()
    rules = (market.get("rules_primary") or "").strip()

    # Match label from rules: "Sakkari vs Svitolina"
    matchup = None
    m = re.search(
        r"\b([A-Z][a-zA-Z.\-']+)\s+vs\.?\s+([A-Z][a-zA-Z.\-']+)\b",
        rules,
    )
    if m:
        matchup = f"{m.group(1)} vs {m.group(2)}"

    def opponent_from_matchup(player: str) -> Optional[str]:
        if not matchup:
            return None
        left, right = matchup.split(" vs ", 1)
        last = player.split()[-1].lower()
        if last in left.lower() or left.lower() in player.lower():
            return right
        if last in right.lower() or right.lower() in player.lower():
            return left
        return right

    # Primary: yes_sub_title is the YES player (Kalshi often duplicates it on no_sub_title)
    if yes:
        return yes, opponent_from_matchup(yes) or "Opponent", matchup or title or event

    # Title like "Elina Svitolina wins"
    wins = re.match(r"^(.+?)\s+wins\b", title, re.I)
    if wins:
        player = wins.group(1).strip()
        return player, opponent_from_matchup(player) or "Opponent", matchup or title

    will = re.search(r"Will\s+(.+?)\s+win", title, re.I)
    if will:
        player = will.group(1).strip()
        return player, opponent_from_matchup(player) or "Opponent", matchup or title

    ticker = market.get("ticker") or "UNKNOWN"
    return ticker, "Opponent", matchup or title or ticker


class MarketOrchestrator:
    def __init__(
        self,
        engine: SignalEngine,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.engine = engine
        self.client = KalshiReadOnlyClient(self.settings)
        self.tennis: TennisDataProvider = create_tennis_provider(self.settings)
        self.ws: Optional[KalshiWebSocketClient] = None
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._market_meta: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        self._stop.clear()
        self.ws = KalshiWebSocketClient(
            self.settings,
            on_message=self._on_ws_message,
            on_status=self._on_ws_status,
        )
        await self.ws.start()
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="discovery"),
            asyncio.create_task(self._poll_loop(), name="poll"),
            asyncio.create_task(self._expiry_loop(), name="expiry"),
            asyncio.create_task(self._tennis_loop(), name="tennis"),
        ]
        logger.info("MarketOrchestrator started")

    async def stop(self) -> None:
        self._stop.set()
        if self.ws:
            await self.ws.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.close()
        await self.tennis.close()

    async def _on_ws_status(self, status: ConnectionStatus) -> None:
        self.engine.set_connection_status(status)
        if self.engine.broadcast:
            await self.engine.broadcast(
                {"type": "connection", "payload": {"status": status.value}}
            )

    async def _on_ws_message(self, data: dict[str, Any]) -> None:
        msg_type = data.get("type") or data.get("msg", {}).get("channel")
        payload = data.get("msg") or data.get("data") or data

        if isinstance(payload, dict):
            ticker = payload.get("market_ticker") or payload.get("ticker")
            if not ticker:
                return
            # Normalize price fields (Kalshi uses cents as ints often)
            def _c(v: Any) -> Optional[float]:
                if v is None:
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            yes_bid = _c(payload.get("yes_bid") or payload.get("bid"))
            yes_ask = _c(payload.get("yes_ask") or payload.get("ask"))
            last = _c(payload.get("price") or payload.get("yes_price") or payload.get("last_price"))
            # orderbook delta
            if "yes" in payload and isinstance(payload.get("yes"), dict):
                # sometimes nested
                pass
            imbalance = None
            depth_yes = depth_no = None
            if "orderbook" in str(msg_type).lower() or "yes" in payload and isinstance(payload.get("yes"), list):
                yes_levels = payload.get("yes") or []
                no_levels = payload.get("no") or []
                try:
                    depth_yes = float(sum(level[1] for level in yes_levels[:5]))
                    depth_no = float(sum(level[1] for level in no_levels[:5]))
                    total = depth_yes + depth_no
                    imbalance = ((depth_yes - depth_no) / total) if total else 0.0
                except Exception:
                    pass

            await self.engine.on_market_update(
                ticker,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                last_trade=last,
                volume=_c(payload.get("volume")),
                imbalance=imbalance,
                depth_yes=depth_yes,
                depth_no=depth_no,
                status=payload.get("status"),
            )

    async def _discovery_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._discover()
            except Exception as exc:
                logger.error("Discovery error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def _discover(self) -> None:
        markets = await self.client.search_tennis_markets()
        tickers: list[str] = []
        for m in markets:
            ticker = m.get("ticker")
            if not ticker:
                continue
            tickers.append(ticker)
            # Enrich only when list payload lacks rules (needed for opponent parsing)
            if not m.get("rules_primary"):
                try:
                    await asyncio.sleep(0.08)
                    full = await self.client.get_market(ticker)
                    m = {**m, **(full.get("market") or full)}
                except Exception:
                    pass
            self._market_meta[ticker] = m
            if ticker not in self.engine.snap.matches:
                player_a, player_b, tournament = parse_players_from_market(m)
                match_id = str(uuid4())
                market_db_id = str(uuid4())
                self.engine.register_match(
                    match_id=match_id,
                    player_a=player_a,
                    player_b=player_b,
                    tournament=tournament or m.get("event_ticker"),
                    market_ticker=ticker,
                    market_db_id=market_db_id,
                )
                # Seed initial prices
                prices = extract_market_prices(m)
                await self.engine.on_market_update(
                    ticker,
                    yes_bid=prices["yes_bid"],  # type: ignore[arg-type]
                    yes_ask=prices["yes_ask"],  # type: ignore[arg-type]
                    last_trade=prices["last_trade"],  # type: ignore[arg-type]
                    volume=prices["volume"],  # type: ignore[arg-type]
                    status=str(prices["status"]),
                )
            else:
                # Refresh player names if previously unknown
                ctx = self.engine.snap.matches[ticker]
                pa, pb, tourn = parse_players_from_market(m)
                if ctx.player_b == "Opponent" or ctx.player_a == ctx.player_b:
                    ctx.player_a, ctx.player_b = pa, pb
                if tourn:
                    ctx.tournament = tourn

        if self.ws and tickers:
            await self.ws.subscribe_markets(tickers)
        # If WS never got messages, still mark connected when REST works
        if markets and self.engine.snap.connection_status == ConnectionStatus.DISCONNECTED:
            # REST path is alive — use RECONNECTING until WS connects, but allow analysis via poll
            pass
        if not markets:
            logger.info("NO LIVE TENNIS MARKETS")

    async def _poll_loop(self) -> None:
        """REST polling backup + keeps data fresh when WS is quiet."""
        cursor = 0
        batch_size = 15
        while not self._stop.is_set():
            try:
                tickers = list(self.engine.snap.matches.keys())
                if not tickers:
                    await asyncio.sleep(self.settings.market_poll_interval_seconds)
                    continue
                batch = tickers[cursor : cursor + batch_size]
                cursor = (cursor + batch_size) % max(len(tickers), 1)
                for ticker in batch:
                    try:
                        data = await self.client.get_market(ticker)
                        m = data.get("market") or data
                        ob = {}
                        try:
                            ob_raw = await self.client.get_orderbook(ticker, depth=10)
                            ob = ob_raw.get("orderbook_fp") or ob_raw.get("orderbook") or ob_raw
                        except Exception:
                            ob = {}
                        depth_yes = depth_no = imbalance = None
                        yes_levels = ob.get("yes_dollars") or ob.get("yes") or []
                        no_levels = ob.get("no_dollars") or ob.get("no") or []
                        if yes_levels or no_levels:
                            try:
                                def _depth(levels: list) -> float:
                                    total = 0.0
                                    for level in levels[:5]:
                                        if isinstance(level, (list, tuple)) and len(level) >= 2:
                                            total += float(level[1])
                                        elif isinstance(level, dict):
                                            total += float(level.get("size") or level.get("quantity") or 0)
                                    return total

                                depth_yes = _depth(yes_levels)
                                depth_no = _depth(no_levels)
                                total = depth_yes + depth_no
                                imbalance = ((depth_yes - depth_no) / total) if total else 0.0
                            except Exception:
                                pass
                        prices = extract_market_prices(m)
                        await self.engine.on_market_update(
                            ticker,
                            yes_bid=prices["yes_bid"],  # type: ignore[arg-type]
                            yes_ask=prices["yes_ask"],  # type: ignore[arg-type]
                            last_trade=prices["last_trade"],  # type: ignore[arg-type]
                            volume=prices["volume"],  # type: ignore[arg-type]
                            depth_yes=depth_yes,
                            depth_no=depth_no,
                            imbalance=imbalance,
                            status=str(prices["status"]),
                        )
                        if self.engine.snap.connection_status != ConnectionStatus.CONNECTED:
                            self.engine.snap.connection_status = ConnectionStatus.CONNECTED
                    except Exception as exc:
                        logger.debug("Poll %s failed: %s", ticker, exc)
                    await asyncio.sleep(0.05)  # gentle rate limit
            except Exception as exc:
                logger.error("Poll loop error: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.market_poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _expiry_loop(self) -> None:
        interval = self.settings.signal_timer_refresh_ms / 1000.0
        while not self._stop.is_set():
            try:
                expired = self.engine.tick_expirations()
                for sig in expired:
                    await self.engine._emit_signal_update(sig)
                # Broadcast dashboard heartbeat with server time
                if self.engine.broadcast:
                    await self.engine.broadcast(
                        {
                            "type": "heartbeat",
                            "payload": {
                                "server_time_ms": time.time() * 1000.0,
                                "connection_status": self.engine.snap.connection_status.value,
                            },
                        }
                    )
            except Exception as exc:
                logger.error("Expiry loop error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _tennis_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for ticker, ctx in list(self.engine.snap.matches.items()):
                    state = await self.tennis.get_live_match(ctx.player_a, ctx.player_b)
                    ctx.tennis = state
            except Exception as exc:
                logger.debug("Tennis loop: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.tennis_poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
