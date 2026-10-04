"""Application runtime — discovers markets, polls Kalshi, drives signal engine."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4, uuid5, NAMESPACE_URL

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


from app.services.kalshi.tennis_detector import TennisMarketDetector


def _kalshi_match_is_trading(market, now):
    return TennisMarketDetector().state(market, now * 1000) == "LIVE"


def occurrence_start_ms(market):
    return TennisMarketDetector().start_ms(market)


def parse_players_from_market(market):
    a, b = TennisMarketDetector().players(market)
    return a, b, market.get("_event", {}).get("sub_title") or market.get("_series", {}).get("title")


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
        self._reconcile = asyncio.Event()
        self._books = {}
        self._ws_sequences = {}
        self._live_updates = 0
        self._last_live_update: dict[str, str] = {}
        self._last_live_log = 0.0
        from app.services.tennis.visual_session import KalshiVisualSession
        self.visual = KalshiVisualSession(self.settings, self.engine)
        self._visual_enabled = False

    async def start(self) -> None:
        self._stop.clear()
        self.ws = KalshiWebSocketClient(
            self.settings,
            on_message=self._on_ws_message,
            on_status=self._on_ws_status,
        )
        await self.ws.start()
        self._visual_enabled = True
        self._tasks = [
            asyncio.create_task(self._discovery_loop(), name="discovery"),
            asyncio.create_task(self._poll_loop(), name="poll"),
            asyncio.create_task(self._expiry_loop(), name="expiry"),
            asyncio.create_task(self._tennis_loop(), name="tennis"),
            asyncio.create_task(self.visual.start(), name="kalshi-visual"),
        ]
        logger.info("MarketOrchestrator started")

    async def stop(self) -> None:
        self._stop.set()
        if self.ws:
            await self.ws.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.visual.stop()
        await self.client.close()
        await self.tennis.close()

    def _note_live_update(self, ticker: str) -> None:
        self._live_updates += 1
        self._last_live_update[ticker] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        now = time.time()
        if now - self._last_live_log >= 30:
            self._last_live_log = now
            logger.info("LIVE UPDATES RECEIVED: %s", self._live_updates)

    async def _on_ws_status(self, status: ConnectionStatus) -> None:
        # REST still serves quotes while the Kalshi socket retries.
        # A reconnect loop is not an exchange outage and must not turn the
        # badge off on every browser refresh.
        if status != ConnectionStatus.CONNECTED:
            return
        self._books.clear()
        self._ws_sequences.clear()
        self._reconcile.set()
        self.engine.set_connection_status(status)
        if self.engine.broadcast:
            await self.engine.broadcast(
                {"type": "connection", "payload": {"status": status.value}}
            )

    async def _on_ws_message(self, data: dict[str, Any]) -> None:
        if "lifecycle" in str(data.get("type", "")):
            self._reconcile.set()
        msg_type = data.get("type") or data.get("msg", {}).get("channel")
        payload = data.get("msg") or data.get("data") or data

        if isinstance(payload, dict):
            ticker = payload.get("market_ticker") or payload.get("ticker")
            if not ticker:
                return
            event_type = payload.get("event_type")
            if event_type in ("closed", "settled", "determined", "deactivated"):
                status = "SUSPENDED" if event_type == "deactivated" else "CLOSED"
                if ticker in self._market_meta:
                    self._market_meta[ticker]["status"] = status.lower()
                ctx = self.engine.snap.matches.get(ticker)
                if ctx:
                    ctx.match_status = "SUSPENDED" if status == "SUSPENDED" else "ENDED"
                    ctx.score_confirmed = False
                await self.engine.on_market_update(ticker, status=status)
                return
            # Normalize price fields (Kalshi uses cents as ints often)
            def _c(v: Any) -> Optional[float]:
                if v is None:
                    return None
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None

            seq = data.get("seq")
            sid = data.get("sid")
            if seq is not None and sid is not None:
                previous = self._ws_sequences.get(sid)
                if previous is not None and seq <= previous:
                    return
                if previous is not None and seq != previous + 1:
                    self._books.clear()
                    self._reconcile.set()
                    return
                self._ws_sequences[sid] = seq
            prices = extract_market_prices(payload)
            yes_bid, yes_ask = prices["yes_bid"], prices["yes_ask"]
            last = prices["last_trade"]
            if payload.get("price_dollars") is not None:
                last = float(payload["price_dollars"]) * 100
            elif payload.get("price") is not None:
                last = float(payload["price"])
            imbalance = depth_yes = depth_no = None
            if "orderbook" in str(msg_type):
                if "snapshot" in str(msg_type):
                    book = {}
                    for side in ("yes", "no"):
                        dollars = payload.get(f"{side}_dollars")
                        levels = dollars if dollars is not None else payload.get(side, [])
                        book[side] = {float(p) * (100 if dollars is not None else 1): float(q) for p, q in levels}
                    self._books[ticker] = book
                elif ticker in self._books:
                    book = self._books[ticker]
                    side = payload.get("side")
                    raw = payload.get("price_dollars", payload.get("price"))
                    delta = payload.get("delta_fp", payload.get("delta"))
                    if side in book and raw is not None and delta is not None:
                        price = float(raw) * (100 if "price_dollars" in payload else 1)
                        book[side][price] = max(0, book[side].get(price, 0) + float(delta))
                else:
                    return
                book = self._books[ticker]
                depth_yes, depth_no = (sum(q for _, q in sorted(book[side].items(), reverse=True)[:5]) for side in ("yes", "no"))
                total = depth_yes + depth_no
                imbalance = (depth_yes - depth_no) / total if total else 0
                yes_bid = max((p for p, q in book["yes"].items() if q > 0), default=0)
                best_no = max((p for p, q in book["no"].items() if q > 0), default=0)
                yes_ask = 100 - best_no
                last = None
            self._note_live_update(ticker)
            await self.engine.on_market_update(
                ticker,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                last_trade=last,
                volume=_c(payload.get("volume")),
                imbalance=imbalance,
                depth_yes=depth_yes,
                depth_no=depth_no,
                status=str(prices["status"]) if payload.get("status") else None,
            )

    async def _discovery_loop(self) -> None:
        # Restore current match contracts promptly while the exhaustive scan
        # reconciles every sport and discovers additional tennis series.
        await self._prime_match_markets()
        while not self._stop.is_set():
            try:
                await self._discover()
            except Exception as exc:
                self.engine.discovery_health.update(complete=False, errors=[f"Discovery: {type(exc).__name__}"])
                logger.error("Discovery error: %s", exc)
            try:
                await asyncio.wait_for(self._reconcile.wait(), timeout=300)
                self._reconcile.clear()
            except asyncio.TimeoutError:
                pass

    async def _prime_match_markets(self) -> None:
        detector = TennisMarketDetector()
        series_tickers = []
        try:
            data = await self.client.get_series_list()
            series_tickers = [s["ticker"] for s in data.get("series", []) if s.get("ticker") and detector.is_match_series(s)]
        except Exception as exc:
            logger.warning('Initial tennis series: %s', type(exc).__name__)
        for series in series_tickers:
            cursor = None
            await asyncio.sleep(0.2)
            try:
                while True:
                    data = await self.client.get_markets(status='open', series_ticker=series, limit=200, cursor=cursor)
                    for market in data.get('markets', []):
                        if market.get('ticker'):
                            self._market_meta[market['ticker']] = market
                    cursor = data.get('cursor')
                    if not cursor:
                        break
            except Exception as exc:
                logger.warning('Initial tennis discovery %s: %s', series, type(exc).__name__)
        if self._market_meta:
            await self._link_scoreboard()

    async def _discover(self) -> None:
        markets = await self.client.search_tennis_markets()
        self._market_meta = {m["ticker"]: m for m in markets if m.get("ticker")}
        self.engine.discovery_health = getattr(self.client, "discovery_health", {})
        await self._link_scoreboard()
        # REST reached Kalshi. Keep the public badge on even if the socket is retrying.
        if self.engine.snap.connection_status != ConnectionStatus.CONNECTED:
            self.engine.set_connection_status(ConnectionStatus.CONNECTED)
        if not markets:
            logger.info("NO LIVE TENNIS MARKETS")

    async def _poll_loop(self) -> None:
        """Keep quotes fresh with bulk series reads; rotate order books."""
        book_cursor = 0
        while not self._stop.is_set():
            try:
                await self._bulk_refresh_quotes()
                book_cursor = await self._refresh_orderbooks(book_cursor)
                if self.engine.snap.connection_status != ConnectionStatus.CONNECTED:
                    # REST is delivering live quotes even if the authenticated WS is down
                    self.engine.set_connection_status(ConnectionStatus.CONNECTED)
            except Exception as exc:
                logger.error("Poll loop error: %s", exc)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=max(self.settings.market_poll_interval_seconds, 20 if self.ws and self.ws.status == ConnectionStatus.CONNECTED else 5)
                )
            except asyncio.TimeoutError:
                pass

    async def _bulk_refresh_quotes(self) -> None:
        """One list call per series updates every tracked market's bid/ask."""
        if not self.engine.snap.matches:
            return
        series_tickers = {m.get("_event", {}).get("series_ticker") or m.get("series_ticker") or ticker.split("-")[0]
                          for ticker, m in self._market_meta.items() if ticker in self._linked_tickers}
        for series in series_tickers:
            try:
                data = await self.client.get_markets(status="open", series_ticker=series, limit=200)
            except Exception as exc:
                logger.debug("Bulk refresh %s failed: %s", series, exc)
                continue
            pages = data.get("markets", [])[:]
            while data.get("cursor"):
                await asyncio.sleep(0.15)
                data = await self.client.get_markets(status="open", series_ticker=series, limit=200, cursor=data["cursor"])
                pages.extend(data.get("markets", []))
            for m in pages:
                ticker = m.get("ticker")
                if not ticker or ticker not in self.engine.snap.matches:
                    continue
                prices = extract_market_prices(m)
                self._note_live_update(ticker)
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
                self._note_live_update(ticker)
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
                    for ticker, ctx in list(self.engine.snap.matches.items()):
                        analyzer = self.engine.snap.analyzers.get(ticker)
                        if ctx.position and analyzer and analyzer.state.data_age_ms > self.settings.max_data_age_ms:
                            await self.engine._evaluate_position(ctx, analyzer.state, now * 1000, emergency_forced=True)
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
                if self._visual_enabled:
                    await self._observe_visual()
            except Exception as exc:
                logger.warning("Tennis loop: %s", exc)
            try:
                await asyncio.wait_for(
                    # Live evidence expires after 45s. A legacy quota-saving
                    # interval must not prevent fresh fallback game updates.
                    self._stop.wait(), timeout=min(30.0, max(5.0, self.settings.tennis_poll_interval_seconds))
                )
            except asyncio.TimeoutError:
                pass

    async def _link_scoreboard(self) -> None:
        """Reconcile all Kalshi matches independently of score-provider coverage."""
        from app.services.tennis.espn import same_match
        detector = TennisMarketDetector()
        try:
            live = await self.tennis.list_live_matches()
        except Exception:
            live = None
        self.engine.discovery_health["live_verification_available"] = live is not None
        live = [
            item for item in (live or [])
            if getattr(item, "source", "") != "espn" and "espn.com" not in str(getattr(item, "source_url", "") or "")
        ]
        groups = {}
        for ticker, meta in self._market_meta.items():
            a, b, tournament = parse_players_from_market(meta)
            state = next((item for item in live if item.available and same_match(a, b, item.player_a, item.player_b)), None)
            match_state = detector.state(meta, time.time() * 1000, state is not None)
            if match_state != "LIVE":
                continue
            # Tournament futures belong to discovery health, not physical-match cards.
            if b == "Opponent unavailable" and not any(k in ticker.upper() for k in ("MATCH", "DOUBLES")):
                continue
            key = detector.group_key(meta)
            groups.setdefault(key, []).append((ticker, meta, state, a, b, tournament))
        linked = set()
        subscribe = []
        for key, contracts in groups.items():
            names = {row[3] for row in contracts if row[3] and row[4] == "Opponent unavailable"}
            if len(names) == 2:
                contracts = [(t, m, st, a, next(n for n in names if n != a), tr) for t, m, st, a, b, tr in contracts]
            # Prefer the already tracked contract so YES orientation stays stable.
            # Use sibling contract names to expand shortened rule names.
            names = [row[1].get("yes_sub_title") for row in contracts if row[1].get("yes_sub_title") and detector.is_match_winner(row[1])]
            expanded = []
            for t, m, st, a, b, tr in contracts:
                own = m.get("yes_sub_title")
                if own:
                    a = own
                    others = [n for n in names if n != own]
                    if len(set(others)) == 1:
                        b = others[0]
                expanded.append((t, m, st, a, b, tr))
            contracts = expanded
            contracts.sort(key=lambda row: (not detector.is_match_winner(row[1]), row[0] not in self.engine.snap.matches, row[0]))
            ticker, meta, state, a, b, tournament = contracts[0]
            if not detector.is_match_winner(meta):
                continue
            linked.add(ticker)
            subscribe.extend(row[0] for row in contracts if detector.is_match_winner(row[1]))
            created = ticker not in self.engine.snap.matches
            if created:
                self.engine.register_match(match_id=str(uuid5(NAMESPACE_URL, f"kalshi:{meta.get('event_ticker') or ticker}")), player_a=a, player_b=b,
                    tournament=tournament, market_ticker=ticker, market_db_id=str(uuid4()),
                    scheduled_start_ms=occurrence_start_ms(meta))
                await self.engine.restore_match_history(self.engine.snap.matches[ticker])
            ctx = self.engine.snap.matches[ticker]
            ctx.score_confirmed = True
            # Kalshi still lists this match open after the start. A 45s score
            # timestamp must not take it off the board.
            ctx.live_evidence_expires_at_ms = None
            ctx.match_status = "LIVE"
            ctx.event_id = meta.get("event_ticker")
            ctx.market_ids = [row[0] for row in contracts]
            if state:
                oriented = await self.tennis.get_live_match(a, b)
                # Do not attach a reversed score to the YES contract.
                ctx.tennis = oriented if oriented and oriented.available else None
                if ctx.tennis:
                    from app.services.tennis.points import orient_points
                    from app.services.tennis.espn import same_player
                    cursor = ctx.point_tracker.resume_seq if ctx.point_tracker.source_match_id == state.match_external_id else 0
                    point_feed = await self.tennis.get_live_points(state.match_external_id, cursor)
                    flip = same_player(a, state.player_b) and not same_player(a, state.player_a)
                    ctx.tennis.point_events = orient_points(point_feed.get('points', []), flip)
                    ctx.tennis.point_feed_available = point_feed.get('available', False)
                    ctx.tennis.point_feed_note = point_feed.get('note', 'Point history unavailable')
                    ctx.tennis.point_feed_quality = point_feed.get('quality', 'unknown')
                    ctx.tennis.point_feed_basis = point_feed.get('basis', 'live')
            else:
                # A Kalshi page reading stays until the next visual pass. ESPN is never filled in.
                if getattr(getattr(ctx, "tennis", None), "source", None) != "kalshi_visual":
                    ctx.tennis = None
            if created:
                prices = extract_market_prices(meta)
                await self.engine.on_market_update(ticker, yes_bid=prices["yes_bid"], yes_ask=prices["yes_ask"],
                    last_trade=prices["last_trade"], volume=prices["volume"], status=str(prices["status"]))
            await self.engine.on_tennis_update(ticker, status=str(extract_market_prices(meta)["status"]))
        for ticker, ctx in self.engine.snap.matches.items():
            if ticker not in linked:
                ctx.score_confirmed = False
                ctx.match_status = "ENDED" if ticker not in self._market_meta else detector.state(self._market_meta[ticker], time.time() * 1000)
                await self.engine.on_market_update(ticker, status="CLOSED" if ticker not in self._market_meta else "SUSPENDED")
        subscribe = sorted(set(subscribe))
        if set(subscribe) != self._linked_tickers and self.ws:
            await self.ws.subscribe_markets(subscribe)
        self._linked_tickers = set(subscribe)
        discovered = self.engine.discovery_health.get("tennis_markets_found")
        if discovered is None:
            discovered = len(self._market_meta)
        logger.info("TENNIS MARKETS DISCOVERED: %s", discovered)
        logger.info("LIVE TENNIS MATCHES CREATED: %s", len(linked))
        logger.info("LIVE TENNIS MATCHES: %s", len(linked))
        logger.info("WEBSOCKET SUBSCRIPTIONS: %s", len(subscribe))
        logger.info("LIVE UPDATES RECEIVED: %s", self._live_updates)
        for ticker in sorted(linked):
            ctx = self.engine.snap.matches.get(ticker)
            if ctx is None:
                continue
            logger.info("MATCH FOUND: %s vs %s", ctx.player_a, ctx.player_b)
            logger.info("MARKET TICKER: %s", ticker)
            logger.info("SUBSCRIBED: YES")
            logger.info("LAST LIVE UPDATE: %s", self._last_live_update.get(ticker, "none"))
        self.engine.discovery_health["live_matches_found"] = len(linked)
        self.engine.discovery_health["live_markets_found"] = len(subscribe)
        self._publish_diagnostics(len(linked))

    async def _link_traded_kalshi_matches(self) -> None:
        await self._link_scoreboard()

    def _publish_diagnostics(self, live_matches: int) -> None:
        health = self.engine.discovery_health
        if not isinstance(health, dict):
            health = {}
            self.engine.discovery_health = health
        health["kalshi_live_matches_found"] = live_matches
        health["ws_markets_subscribed"] = len(getattr(self.ws, "_subscribed_tickers", ()) or ())
        health.update(self.visual.diagnostics())

    async def _observe_visual(self) -> None:
        try:
            live = [ctx for ctx in self.engine.snap.matches.values() if ctx.score_confirmed is True]
            await self.visual.watch(live)
        except Exception as exc:
            logger.warning("Visual session: %s", type(exc).__name__)
            self.visual.no_signal_reason = f"Visual session error: {type(exc).__name__}"
        self._publish_diagnostics(sum(1 for ctx in self.engine.snap.matches.values() if ctx.score_confirmed is True))
