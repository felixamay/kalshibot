"""Application runtime — discovers markets, polls Kalshi, drives signal engine."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
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


def occurrence_start_ms(market: dict[str, Any]) -> Optional[float]:
    """Kalshi occurrence time is when the match is scheduled. None if we cannot tell."""
    raw = market.get("occurrence_datetime")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


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
        self._linked_tickers: set[str] = set()

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

            yes_bid = _c(payload.get("yes_bid_dollars") or payload.get("yes_bid") or payload.get("bid"))
            yes_ask = _c(payload.get("yes_ask_dollars") or payload.get("yes_ask") or payload.get("ask"))
            last = _c(
                payload.get("price_dollars")
                or payload.get("yes_price_dollars")
                or payload.get("price")
                or payload.get("yes_price")
                or payload.get("last_price")
            )
            # Dollar quotes (0-1) must become cents
            def _maybe_cents(v: Optional[float]) -> Optional[float]:
                if v is None:
                    return None
                return v * 100.0 if 0 <= v <= 1 else v

            yes_bid, yes_ask, last = _maybe_cents(yes_bid), _maybe_cents(yes_ask), _maybe_cents(last)
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
        for m in markets:
            ticker = m.get("ticker")
            if not ticker:
                continue
            # Enrich only when list payload lacks rules (needed for opponent parsing)
            if not m.get("rules_primary"):
                try:
                    await asyncio.sleep(0.08)
                    full = await self.client.get_market(ticker)
                    m = {**m, **(full.get("market") or full)}
                except Exception:
                    pass
            self._market_meta[ticker] = m

        await self._link_scoreboard()
        # If WS never got messages, still mark connected when REST works
        if markets and self.engine.snap.connection_status == ConnectionStatus.DISCONNECTED:
            # REST path is alive — use RECONNECTING until WS connects, but allow analysis via poll
            pass
        if not markets:
            logger.info("NO LIVE TENNIS MARKETS")

    async def _poll_loop(self) -> None:
        """Keep quotes fresh with bulk series reads; rotate order books."""
        book_cursor = 0
        while not self._stop.is_set():
            try:
                await self._bulk_refresh_quotes()
                book_cursor = await self._refresh_orderbooks(book_cursor)
                if self.engine.snap.matches and self.engine.snap.connection_status != ConnectionStatus.CONNECTED:
                    # REST is delivering live quotes even if the authenticated WS is down
                    self.engine.snap.connection_status = ConnectionStatus.CONNECTED
            except Exception as exc:
                logger.error("Poll loop error: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.market_poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _bulk_refresh_quotes(self) -> None:
        """One list call per series updates every tracked market's bid/ask."""
        if not self.engine.snap.matches:
            return
        for series in ("KXWTAMATCH", "KXATPMATCH", "KXWTACHALLENGERMATCH", "KXATPCHALLENGERMATCH"):
            try:
                data = await self.client.get_markets(status="open", series_ticker=series, limit=200)
            except Exception as exc:
                logger.debug("Bulk refresh %s failed: %s", series, exc)
                continue
            for m in data.get("markets", []):
                ticker = m.get("ticker")
                if not ticker or ticker not in self.engine.snap.matches:
                    continue
                prices = extract_market_prices(m)
                await self.engine.on_market_update(
                    ticker,
                    yes_bid=prices["yes_bid"],  # type: ignore[arg-type]
                    yes_ask=prices["yes_ask"],  # type: ignore[arg-type]
                    last_trade=prices["last_trade"],  # type: ignore[arg-type]
                    volume=prices["volume"],  # type: ignore[arg-type]
                    status=str(prices["status"]),
                )

    async def _refresh_orderbooks(self, cursor: int) -> int:
        tickers = list(self.engine.snap.matches.keys())
        if not tickers:
            return 0
        batch = tickers[cursor : cursor + 8]
        nxt = (cursor + 8) % max(len(tickers), 1)
        for ticker in batch:
            try:
                ob_raw = await self.client.get_orderbook(ticker, depth=5)
                ob = ob_raw.get("orderbook_fp") or ob_raw.get("orderbook") or ob_raw
                yes_levels = ob.get("yes_dollars") or ob.get("yes") or []
                no_levels = ob.get("no_dollars") or ob.get("no") or []
                depth_yes = depth_no = imbalance = None
                best_yes = best_no = None

                def _levels(levels: list) -> tuple[float, float | None]:
                    total = 0.0
                    best = None
                    for level in levels:
                        price = size = None
                        if isinstance(level, (list, tuple)) and len(level) >= 2:
                            price, size = float(level[0]), float(level[1])
                        elif isinstance(level, dict):
                            price = float(level.get("price") or 0)
                            size = float(level.get("size") or level.get("quantity") or 0)
                        if size:
                            total += size
                        if price is not None and (best is None or price > best):
                            best = price
                    return total, best

                if yes_levels or no_levels:
                    depth_yes, best_yes = _levels(yes_levels)
                    depth_no, best_no = _levels(no_levels)
                    total = (depth_yes or 0) + (depth_no or 0)
                    imbalance = ((depth_yes - depth_no) / total) if total else 0.0
                # Book prices are dollars (0-1) or cents. Normalize.
                yes_bid = _price_to_cents(best_yes) if best_yes is not None else None
                yes_ask = None
                if best_no is not None:
                    no_cents = _price_to_cents(best_no)
                    if no_cents is not None:
                        yes_ask = round(100.0 - no_cents, 2)
                await self.engine.on_market_update(
                    ticker,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    depth_yes=depth_yes,
                    depth_no=depth_no,
                    imbalance=imbalance,
                )
            except Exception as exc:
                logger.debug("Orderbook %s failed: %s", ticker, exc)
            await asyncio.sleep(0.05)
        return nxt

    async def _expiry_loop(self) -> None:
        interval = self.settings.signal_timer_refresh_ms / 1000.0
        last_beat = 0.0
        while not self._stop.is_set():
            try:
                expired = self.engine.tick_expirations()
                for sig in expired:
                    await self.engine._emit_signal_update(sig)
                # A heartbeat every tenth of a second crowded out the alert frames.
                now = time.time()
                if self.engine.broadcast and now - last_beat >= 1.0:
                    last_beat = now
                    await self.engine.broadcast(
                        {
                            "type": "heartbeat",
                            "payload": {
                                "server_time_ms": now * 1000.0,
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
                await self._link_scoreboard()
            except Exception as exc:
                logger.debug("Tennis loop: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.tennis_poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _link_scoreboard(self) -> None:
        """Show a Kalshi contract only while ESPN says that match is in progress."""
        from app.services.tennis.espn import last_name, same_match

        live = await self.tennis.list_live_matches()
        if live is None:
            return
        linked: set[str] = set()
        for ticker, meta in self._market_meta.items():
            player_a, player_b, tournament = parse_players_from_market(meta)
            state = next(
                (
                    item
                    for item in live
                    if item.available and same_match(player_a, player_b, item.player_a, item.player_b)
                ),
                None,
            )
            if state is None:
                continue
            # Point the set score at the YES player on this contract.
            oriented = await self.tennis.get_live_match(player_a, player_b)
            if oriented is None or not oriented.available:
                oriented = state
            linked.add(ticker)
            created = ticker not in self.engine.snap.matches
            if created:
                self.engine.register_match(
                    match_id=str(uuid4()),
                    player_a=player_a,
                    player_b=player_b,
                    tournament=tournament or meta.get("event_ticker"),
                    market_ticker=ticker,
                    market_db_id=str(uuid4()),
                    scheduled_start_ms=occurrence_start_ms(meta),
                )
            ctx = self.engine.snap.matches[ticker]
            ctx.score_confirmed = True
            ctx.tennis = oriented
            full_a, full_b = state.player_a, state.player_b
            if last_name(player_a) == last_name(full_b):
                full_a, full_b = full_b, full_a
            ctx.player_a, ctx.player_b = full_a or player_a, full_b or player_b
            if tournament:
                ctx.tournament = tournament
            if created:
                prices = extract_market_prices(meta)
                await self.engine.on_market_update(
                    ticker,
                    yes_bid=prices["yes_bid"],  # type: ignore[arg-type]
                    yes_ask=prices["yes_ask"],  # type: ignore[arg-type]
                    last_trade=prices["last_trade"],  # type: ignore[arg-type]
                    volume=prices["volume"],  # type: ignore[arg-type]
                    status=str(prices["status"]),
                )
        for ticker, ctx in self.engine.snap.matches.items():
            if ticker not in linked:
                ctx.score_confirmed = False
                ctx.tennis = None
        if linked and linked != self._linked_tickers and self.ws:
            await self.ws.subscribe_markets(sorted(linked))
        self._linked_tickers = linked
