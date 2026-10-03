"""Signal engine — observation, confirmation, emission, early invalidation."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional
from uuid import uuid4

from app.config import Settings, get_settings
from app.core.enums import (
    EXPIRATION_REASON_MESSAGES,
    AnalysisMode,
    ConnectionStatus,
    ExpirationReason,
    SignalStatus,
    SignalType,
)
from app.services.market.analyzer import MarketState, RollingMarketAnalyzer
from app.services.signals.confidence import SignalConfidenceCalculator
from app.services.signals.live_signal import LiveSignal
from app.services.signals.mispricing import (
    MarketRead,
    assess_market,
    dynamic_min_edge,
    liquidity_quality,
    model_uncertainty,
    spread_quality,
)
from app.services.signals.ttl import SignalTTLCalculator
from app.services.tennis.probability import ProbabilityResult, TennisProbabilityModel
from app.services.tennis.provider import TennisLiveState

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


def _slug(name: str) -> str:
    parts = re.sub(r"[^A-Za-z0-9]+", " ", name).strip().split()
    if not parts:
        return "UNK"
    if len(parts) == 1:
        return parts[0][:3].upper()
    return (parts[0][:1] + parts[-1][:2]).upper()


@dataclass
class MatchContext:
    match_id: str
    player_a: str
    player_b: str
    tournament: Optional[str]
    market_ticker: str
    market_db_id: str
    observation_started_ms: float
    observation_ends_ms: float
    signals_emitted: int = 0
    cooldown_until_ms: float = 0.0
    confirmation_count: int = 0
    last_edge_positive: bool = False
    version_counter: int = 0
    active_signal_id: Optional[str] = None
    analysis_mode: str = AnalysisMode.OBSERVING.value
    display_state: SignalType = SignalType.STUDYING_MATCH
    tennis: Optional[TennisLiveState] = None
    last_model: Optional[ProbabilityResult] = None
    last_confidence: Optional[float] = None
    last_read: Optional[MarketRead] = None
    hold_reason: str = "Waiting for the first Kalshi quote."
    chase_blocked_until_ms: float = 0.0
    best_seen_ask: Optional[float] = None  # for anti-chase


@dataclass
class EngineSnapshot:
    connection_status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    matches: dict[str, MatchContext] = field(default_factory=dict)
    analyzers: dict[str, RollingMarketAnalyzer] = field(default_factory=dict)
    signals: dict[str, LiveSignal] = field(default_factory=dict)
    signal_history: list[dict[str, Any]] = field(default_factory=list)


class SignalEngine:
    """
    Core real-time analyst.

    NEVER places orders. Only emits advisory signals with TTL and early cancel.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        broadcast: BroadcastFn | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.broadcast = broadcast
        self.ttl_calc = SignalTTLCalculator(self.settings)
        self.conf_calc = SignalConfidenceCalculator(self.settings)
        self.prob_model = TennisProbabilityModel(self.settings)
        self.snap = EngineSnapshot()
        self._lock = asyncio.Lock()
        self._seq = 0

    def set_connection_status(self, status: ConnectionStatus) -> None:
        self.snap.connection_status = status
        if status == ConnectionStatus.DISCONNECTED:
            asyncio.create_task(self._cancel_all(ExpirationReason.CONNECTION_LOST))

    async def _cancel_all(self, reason: ExpirationReason) -> None:
        async with self._lock:
            now = time.time() * 1000.0
            for sig in list(self.snap.signals.values()):
                if sig.status == SignalStatus.ACTIVE:
                    sig.cancel(reason, server_now_ms=now)
                    await self._emit_signal_update(sig)

    def register_match(
        self,
        *,
        match_id: str,
        player_a: str,
        player_b: str,
        tournament: Optional[str],
        market_ticker: str,
        market_db_id: str,
        now_ms: float | None = None,
    ) -> MatchContext:
        now = now_ms or time.time() * 1000.0
        obs = self.settings.initial_observation_seconds * 1000
        ctx = MatchContext(
            match_id=match_id,
            player_a=player_a,
            player_b=player_b,
            tournament=tournament,
            market_ticker=market_ticker,
            market_db_id=market_db_id,
            observation_started_ms=now,
            observation_ends_ms=now + obs,
            analysis_mode=AnalysisMode.OBSERVING.value,
            display_state=SignalType.STUDYING_MATCH,
        )
        self.snap.matches[market_ticker] = ctx
        if market_ticker not in self.snap.analyzers:
            self.snap.analyzers[market_ticker] = RollingMarketAnalyzer(
                market_ticker, self.settings.window_sizes
            )
        return ctx

    async def on_market_update(
        self,
        ticker: str,
        *,
        yes_bid: Optional[float] = None,
        yes_ask: Optional[float] = None,
        last_trade: Optional[float] = None,
        volume: Optional[float] = None,
        imbalance: Optional[float] = None,
        depth_yes: Optional[float] = None,
        depth_no: Optional[float] = None,
        status: Optional[str] = None,
        ts_ms: Optional[float] = None,
    ) -> None:
        async with self._lock:
            analyzer = self.snap.analyzers.get(ticker)
            if not analyzer:
                analyzer = RollingMarketAnalyzer(ticker, self.settings.window_sizes)
                self.snap.analyzers[ticker] = analyzer
            market = analyzer.update(
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                last_trade=last_trade,
                volume=volume,
                imbalance=imbalance,
                depth_yes=depth_yes,
                depth_no=depth_no,
                status=status,
                ts_ms=ts_ms,
            )
            await self._evaluate_market(ticker, market)

    async def _evaluate_market(self, ticker: str, market: MarketState) -> None:
        now = time.time() * 1000.0
        ctx = self.snap.matches.get(ticker)
        if not ctx:
            return

        # Connection / stale / market status checks invalidate active signals
        await self._validate_active_signal(ctx, market, now)

        # Always score a quoted market so the dashboard shows a real read
        # during observation. Scoring never places a bet and never emits BET NOW.
        if market.yes_bid or market.yes_ask or market.last_trade:
            self._score_market(ctx, market)

        if self.snap.connection_status != ConnectionStatus.CONNECTED:
            ctx.display_state = SignalType.DATA_DELAY
            ctx.hold_reason = "Live connection is down. No bet on a disconnected feed."
            return

        status_u = (market.status or "").upper()
        if status_u in ("CLOSED", "SETTLED"):
            ctx.display_state = SignalType.NO_BET
            ctx.hold_reason = "Market is closed. No bet."
            return
        if status_u == "SUSPENDED":
            ctx.display_state = SignalType.NO_BET
            ctx.hold_reason = "Market is suspended. No bet."
            return

        # Observation period — publish the read, but never a BET NOW.
        if now < ctx.observation_ends_ms:
            ctx.analysis_mode = AnalysisMode.OBSERVING.value
            ctx.display_state = SignalType.STUDYING_MATCH
            ctx.confirmation_count = 0
            if ctx.last_model is not None and ctx.last_confidence is not None:
                preview = assess_market(
                    self.settings,
                    market,
                    ctx.last_model,
                    confidence=ctx.last_confidence,
                    confirmation_count=0,
                    tennis_available=bool(ctx.tennis and ctx.tennis.available)
                    or ctx.last_model.source == "tennis_enhanced",
                )
                ctx.last_read = preview
                ctx.hold_reason = (
                    "No BET SIGNAL until the observation clock ends. " + preview.explanation
                )
            else:
                ctx.hold_reason = "No BET SIGNAL until the observation clock ends."
            return

        if market.data_age_ms > self.settings.max_data_age_ms:
            ctx.display_state = SignalType.DATA_DELAY
            ctx.hold_reason = (
                f"Quote is {market.data_age_ms / 1000:.1f}s old. "
                f"Data older than {self.settings.max_data_age_ms / 1000:.0f}s is not betable."
            )
            return

        # Cooldown after exit
        if now < ctx.cooldown_until_ms:
            ctx.analysis_mode = AnalysisMode.COOLDOWN.value
            ctx.display_state = SignalType.KEEP_WATCHING
            ctx.hold_reason = "Cooldown after the last manual exit. Full reassessment when it ends."
            return

        if ctx.signals_emitted >= self.settings.max_signals_per_match:
            ctx.display_state = SignalType.NO_BET
            ctx.hold_reason = (
                f"This match already produced {ctx.signals_emitted} signals. "
                f"The cap is {self.settings.max_signals_per_match}."
            )
            return

        tennis = ctx.tennis
        ctx.analysis_mode = (
            AnalysisMode.TENNIS_ENHANCED.value
            if tennis and tennis.available
            else AnalysisMode.MARKET_ONLY.value
        )

        # Evaluate YES on player_a (primary market convention)
        if ctx.last_model is None:
            self._score_market(ctx, market)
        prob = ctx.last_model
        if prob is None or ctx.last_confidence is None:
            ctx.display_state = SignalType.NO_BET
            ctx.hold_reason = "Waiting for a usable Kalshi quote."
            return

        read = self._read_market(ctx, market, prob, ctx.last_confidence)
        ask = market.executable_yes_price()
        if (
            read.dynamic_min_edge is not None
            and read.uncertainty_adjusted_edge >= read.dynamic_min_edge
            and (ctx.best_seen_ask is None or ask < ctx.best_seen_ask)
        ):
            ctx.best_seen_ask = ask
        ctx.hold_reason = read.explanation

        if read.decision == "MATCH_TOO_VOLATILE":
            ctx.display_state = SignalType.MATCH_TOO_VOLATILE
            ctx.confirmation_count = 0
            return
        if read.decision in ("OPPORTUNITY_MISSED", "DO_NOT_CHASE"):
            ctx.display_state = (
                SignalType.OPPORTUNITY_MISSED
                if read.decision == "OPPORTUNITY_MISSED"
                else SignalType.DO_NOT_CHASE
            )
            ctx.confirmation_count = 0
            return
        if read.decision in ("BET_SIGNAL", "STRONG_BET_SIGNAL"):
            active = self._active_signal_for(ticker)
            if active and active.is_actionable(now):
                return
            await self._emit_bet_signal(
                ctx=ctx,
                market=market,
                prob=prob,
                confidence=ctx.last_confidence,
                strong=read.decision == "STRONG_BET_SIGNAL",
                now_ms=now,
                read=read,
            )
            return
        if read.decision == "CLOSE_TO_SIGNAL":
            ctx.display_state = SignalType.CLOSE_TO_SIGNAL
            return
        if read.decision == "WATCH":
            ctx.display_state = SignalType.WATCH
            ctx.confirmation_count = max(0, ctx.confirmation_count)
            return

        ctx.confirmation_count = max(0, ctx.confirmation_count - 1)
        if read.uncertainty_adjusted_edge < -0.02:
            ctx.display_state = SignalType.NO_BET
        elif read.uncertainty_adjusted_edge > 0:
            ctx.display_state = SignalType.WAIT
        else:
            ctx.display_state = SignalType.NO_BET

    def _score_market(self, ctx: MatchContext, market: MarketState) -> ProbabilityResult:
        player = market_yes_player(ctx, market)
        prob = self.prob_model.estimate(
            player=player, market=market, tennis=ctx.tennis, direction="YES"
        )
        tennis_ok = bool(ctx.tennis and ctx.tennis.available) or prob.source == "tennis_enhanced"
        preview = assess_market(
            self.settings,
            market,
            prob,
            confidence=0,
            confirmation_count=ctx.confirmation_count,
            tennis_available=tennis_ok,
            best_seen_ask=ctx.best_seen_ask,
        )
        prob.model_uncertainty = preview.model_uncertainty
        prob.uncertainty_adjusted_edge = preview.uncertainty_adjusted_edge
        conf = self.conf_calc.calculate(market, prob, confirmation_count=ctx.confirmation_count)
        ctx.last_model = prob
        ctx.last_confidence = conf.confidence
        return prob

    def _read_market(
        self,
        ctx: MatchContext,
        market: MarketState,
        prob: ProbabilityResult,
        confidence: float,
    ) -> MarketRead:
        tennis_ok = bool(ctx.tennis and ctx.tennis.available) or prob.source == "tennis_enhanced"
        # Count a passing update toward confirmation before classifying.
        prospective = ctx.confirmation_count
        read_now = assess_market(
            self.settings,
            market,
            prob,
            confidence=confidence,
            confirmation_count=prospective,
            tennis_available=tennis_ok,
            best_seen_ask=ctx.best_seen_ask,
        )
        if read_now.decision in ("BET_SIGNAL", "STRONG_BET_SIGNAL", "CLOSE_TO_SIGNAL", "WATCH") and (
            read_now.dynamic_min_edge is not None
            and read_now.uncertainty_adjusted_edge >= read_now.dynamic_min_edge
            and confidence >= self.settings.min_bet_confidence
        ):
            prospective = ctx.confirmation_count + 1
            ctx.confirmation_count = prospective
        read = assess_market(
            self.settings,
            market,
            prob,
            confidence=confidence,
            confirmation_count=ctx.confirmation_count,
            tennis_available=tennis_ok,
            best_seen_ask=ctx.best_seen_ask,
        )
        ctx.last_read = read
        return read

    def _hold_reason(
        self,
        ctx: MatchContext,
        market: MarketState,
        now: float,
        *,
        observing: bool,
    ) -> str:
        """Why this market is not a BET NOW. Uses the latest score."""
        s = self.settings
        parts: list[str] = []
        prob = ctx.last_model
        confidence = ctx.last_confidence
        if observing:
            parts.append("No BET NOW until the observation clock ends.")
        if prob is None or confidence is None:
            parts.append("Waiting for a usable Kalshi quote.")
            return " ".join(parts)
        if confidence < s.min_bet_confidence:
            parts.append(f"Confidence {confidence:.0f} is below {s.min_bet_confidence:.0f}.")
        if prob.estimated_net_edge < s.min_net_edge:
            parts.append(
                f"Net edge {prob.estimated_net_edge * 100:+.1f}pp is below "
                f"+{s.min_net_edge * 100:.0f}pp after fees, slippage, and the safety margin."
            )
        if market.spread > s.max_spread_cents:
            parts.append(
                f"Spread {market.spread:.1f}¢ is wider than {s.max_spread_cents:.0f}¢."
            )
        liquidity = market.depth_yes + market.depth_no
        if liquidity < s.min_liquidity_contracts:
            parts.append(
                f"Visible liquidity is {liquidity:.0f} contracts. Need {s.min_liquidity_contracts:.0f}."
            )
        if not parts:
            parts.append("Conditions are being checked. No bet has been cleared yet.")
        return " ".join(parts)

    def _entry_conditions_ok(
        self,
        market: MarketState,
        prob: ProbabilityResult,
        confidence: float,
    ) -> bool:
        if confidence < self.settings.min_bet_confidence:
            return False
        if prob.estimated_net_edge < self.settings.min_net_edge:
            return False
        if market.spread > self.settings.max_spread_cents:
            return False
        if (market.depth_yes + market.depth_no) < self.settings.min_liquidity_contracts:
            # Allow when depth unknown (0) only if we have last trade — still require some depth
            if market.depth_yes + market.depth_no <= 0:
                return False
            return False
        if market.data_age_ms > self.settings.max_data_age_ms:
            return False
        # Momentum confirmation: slight supportive or flat, not strongly against
        w5 = market.windows.get(5000)
        mom = (w5.momentum if w5 else 0.0) or 0.0
        if prob.direction == "YES" and mom < -1.5:
            return False
        if prob.direction == "NO" and mom > 1.5:
            return False
        # Order book confirmation
        imb = market.imbalance
        if prob.direction == "YES" and imb < -0.4:
            return False
        if prob.direction == "NO" and imb > 0.4:
            return False
        status_u = (market.status or "OPEN").upper()
        if status_u not in ("OPEN", "ACTIVE", ""):
            return False
        return True

    async def _validate_active_signal(
        self, ctx: MatchContext, market: MarketState, now: float
    ) -> None:
        sig = self._active_signal_for(ctx.market_ticker)
        if not sig or sig.status != SignalStatus.ACTIVE:
            return

        # TTL
        if now >= sig.expires_at_ms:
            sig.cancel(ExpirationReason.TTL_EXPIRED, price=market.executable_yes_price(), server_now_ms=now)
            await self._emit_signal_update(sig)
            ctx.display_state = SignalType.REANALYZING
            ctx.confirmation_count = 0
            return

        # Stale
        if market.data_age_ms > self.settings.max_data_age_ms:
            sig.cancel(ExpirationReason.STALE_DATA, price=market.executable_yes_price(), server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        # Connection
        if self.snap.connection_status != ConnectionStatus.CONNECTED:
            sig.cancel(ExpirationReason.CONNECTION_LOST, price=market.executable_yes_price(), server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        status_u = (market.status or "").upper()
        if status_u == "SUSPENDED":
            sig.cancel(ExpirationReason.MARKET_SUSPENDED, price=market.executable_yes_price(), server_now_ms=now)
            await self._emit_signal_update(sig)
            return
        if status_u in ("CLOSED", "SETTLED"):
            sig.cancel(ExpirationReason.MARKET_CLOSED, price=market.executable_yes_price(), server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        # Price beyond max entry
        current = market.executable_yes_price() if sig.direction == "YES" else market.executable_no_price()
        if current > sig.maximum_entry_price:
            msg = (
                f"The market moved from {sig.creation_price:.0f}¢ to {current:.0f}¢ "
                f"and exceeded maximum entry {sig.maximum_entry_price:.0f}¢. Do not chase."
            )
            sig.cancel(ExpirationReason.PRICE_MOVED, price=current, message=msg, server_now_ms=now)
            await self._emit_signal_update(sig)
            ctx.display_state = SignalType.DO_NOT_CHASE
            ctx.confirmation_count = 0
            return

        # Recompute edge against the current book's dynamic threshold.
        player = sig.player
        prob = self.prob_model.estimate(
            player=player, market=market, tennis=ctx.tennis, direction=sig.direction
        )
        tennis_ok = bool(ctx.tennis and ctx.tennis.available) or prob.source == "tennis_enhanced"
        uncertainty, penalty = model_uncertainty(tennis_ok, prob.source, self.settings)
        adjusted_preview = prob.estimated_net_edge - penalty
        prob.model_uncertainty = uncertainty
        prob.uncertainty_adjusted_edge = adjusted_preview
        conf = self.conf_calc.calculate(market, prob, confirmation_count=self.settings.entry_confirmation_count)
        liquidity = liquidity_quality(market, self.settings)
        spread = spread_quality(market, self.settings)
        w5 = market.windows.get(5000)
        vol = (w5.volatility if w5 else 0.0) or 0.0
        extreme = vol > self.settings.extreme_volatility
        required = dynamic_min_edge(
            liquidity, spread, extreme_volatility=extreme, settings=self.settings
        )
        if extreme or required is None:
            reason = (
                ExpirationReason.LIQUIDITY_LOSS
                if liquidity == "VERY_LOW"
                else ExpirationReason.SPREAD_EXPANSION
                if spread == "VERY_POOR"
                else ExpirationReason.RISK_LIMIT
            )
            sig.cancel(reason, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            ctx.display_state = SignalType.MATCH_TOO_VOLATILE if extreme else SignalType.NO_BET
            return
        if adjusted_preview < required:
            sig.cancel(
                ExpirationReason.EDGE_DISAPPEARED,
                price=current,
                server_now_ms=now,
            )
            await self._emit_signal_update(sig)
            ctx.display_state = SignalType.EDGE_DISAPPEARING
            return

        if conf.confidence < self.settings.min_bet_confidence:
            sig.cancel(ExpirationReason.CONFIDENCE_DROPPED, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        if market.spread > self.settings.max_spread_cents:
            sig.cancel(ExpirationReason.SPREAD_EXPANSION, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        if (market.depth_yes + market.depth_no) < self.settings.min_liquidity_contracts * 0.5:
            sig.cancel(ExpirationReason.LIQUIDITY_LOSS, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        w5 = market.windows.get(5000)
        mom = (w5.momentum if w5 else 0.0) or 0.0
        if sig.direction == "YES" and mom < -2.0:
            sig.cancel(ExpirationReason.MOMENTUM_REVERSAL, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            return
        if sig.direction == "NO" and mom > 2.0:
            sig.cancel(ExpirationReason.MOMENTUM_REVERSAL, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        imb = market.imbalance
        if sig.direction == "YES" and imb < -0.55:
            sig.cancel(ExpirationReason.ORDERBOOK_REVERSAL, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        # Soft update of live fields
        sig.market_price = current
        sig.net_edge = adjusted_preview
        sig.confidence = conf.confidence
        sig.model_probability = prob.model_win_probability
        sig.lifecycle.append(
            {
                "event_type": "updated",
                "ts_ms": now,
                "market_price": current,
                "confidence": conf.confidence,
                "net_edge": prob.estimated_net_edge,
            }
        )

    async def _emit_bet_signal(
        self,
        *,
        ctx: MatchContext,
        market: MarketState,
        prob: ProbabilityResult,
        confidence: float,
        strong: bool,
        now_ms: float,
        read: MarketRead | None = None,
    ) -> None:
        # Supersede previous
        prev = self._active_signal_for(ctx.market_ticker)
        if prev and prev.status == SignalStatus.ACTIVE:
            prev.cancel(ExpirationReason.SUPERSEDED, price=market.executable_yes_price(), server_now_ms=now_ms)
            await self._emit_signal_update(prev)

        ttl = self.ttl_calc.calculate(market, confidence=confidence)
        ctx.version_counter += 1
        self._seq += 1
        signal_id = f"SIG-{_slug(prob.player)}-{_slug(ctx.tournament or 'TEN')}-{self._seq % 100000:05d}"

        ask = market.executable_yes_price() if prob.direction == "YES" else market.executable_no_price()
        model_cap = read.max_entry_price_cents if read else ask + self.settings.max_entry_slippage_cents
        max_entry = min(model_cap, ask + self.settings.max_entry_slippage_cents)
        # The model cap is the economic maximum. Do not allow a looser ask+slippage cap above it.
        max_entry = model_cap

        sig = LiveSignal(
            signal_id=signal_id,
            signal_version=ctx.version_counter,
            match_id=ctx.match_id,
            market_id=ctx.market_db_id,
            market_ticker=ctx.market_ticker,
            signal_type=SignalType.STRONG_BET_SIGNAL if strong else SignalType.BET_SIGNAL,
            player=prob.player,
            direction=prob.direction,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + ttl.ttl_ms,
            original_ttl_ms=ttl.ttl_ms,
            market_price=ask,
            target_entry_price=ask,
            maximum_entry_price=max_entry,
            model_probability=prob.model_win_probability,
            net_edge=read.uncertainty_adjusted_edge if read else prob.estimated_net_edge,
            confidence=confidence,
            creation_price=ask,
            tournament=ctx.tournament,
            analysis_mode=ctx.analysis_mode,
            confirmation_count=ctx.confirmation_count,
            lifecycle=[
                {
                    "event_type": "created",
                    "ts_ms": now_ms,
                    "market_price": ask,
                    "confidence": confidence,
                    "net_edge": prob.estimated_net_edge,
                    "ttl_ms": ttl.ttl_ms,
                    "ttl_reason": ttl.reason,
                }
            ],
        )
        self.snap.signals[signal_id] = sig
        ctx.active_signal_id = signal_id
        ctx.signals_emitted += 1
        ctx.display_state = sig.signal_type
        ctx.confirmation_count = 0
        self.snap.signal_history.append(sig.to_public_dict(now_ms))
        if len(self.snap.signal_history) > 500:
            self.snap.signal_history = self.snap.signal_history[-500:]
        await self._emit_signal_update(sig)
        logger.info(
            "Emitted %s %s ttl=%.1fs edge=%.3f conf=%.1f",
            sig.signal_type.value,
            signal_id,
            ttl.ttl_seconds,
            prob.estimated_net_edge,
            confidence,
        )

    def _active_signal_for(self, ticker: str) -> Optional[LiveSignal]:
        ctx = self.snap.matches.get(ticker)
        if not ctx or not ctx.active_signal_id:
            return None
        return self.snap.signals.get(ctx.active_signal_id)

    async def _emit_signal_update(self, sig: LiveSignal) -> None:
        if self.broadcast:
            await self.broadcast(
                {
                    "type": "signal",
                    "payload": sig.to_public_dict(),
                }
            )

    def start_cooldown(self, ticker: str, now_ms: float | None = None) -> None:
        now = now_ms or time.time() * 1000.0
        ctx = self.snap.matches.get(ticker)
        if ctx:
            ctx.cooldown_until_ms = now + self.settings.reentry_cooldown_seconds * 1000
            ctx.analysis_mode = AnalysisMode.COOLDOWN.value
            ctx.display_state = SignalType.KEEP_WATCHING

    def tick_expirations(self) -> list[LiveSignal]:
        """Called periodically to expire TTL without waiting for market tick."""
        now = time.time() * 1000.0
        expired: list[LiveSignal] = []
        for sig in self.snap.signals.values():
            if sig.status == SignalStatus.ACTIVE and now >= sig.expires_at_ms:
                sig.cancel(ExpirationReason.TTL_EXPIRED, price=sig.market_price, server_now_ms=now)
                expired.append(sig)
        return expired

    def dashboard_payload(self) -> dict[str, Any]:
        now = time.time() * 1000.0
        cards = []
        for ticker, ctx in self.snap.matches.items():
            analyzer = self.snap.analyzers.get(ticker)
            market = analyzer.state if analyzer else None
            active = self._active_signal_for(ticker)
            obs_remaining = max(0.0, ctx.observation_ends_ms - now)
            cards.append(
                {
                    "match_id": ctx.match_id,
                    "player_a": ctx.player_a,
                    "player_b": ctx.player_b,
                    "tournament": ctx.tournament,
                    "market_ticker": ticker,
                    "market_status": market.status if market else "UNKNOWN",
                    "kalshi_probability": (market.mid / 100.0) if market else None,
                    "yes_bid": market.yes_bid if market else None,
                    "yes_ask": market.yes_ask if market else None,
                    "spread": market.spread if market else None,
                    "liquidity": (market.depth_yes + market.depth_no) if market else None,
                    "momentum": (market.windows.get(5000).momentum if market and market.windows.get(5000) else None),
                    "orderbook_pressure": market.imbalance if market else None,
                    "model_probability": ctx.last_model.model_win_probability if ctx.last_model else None,
                    "estimated_edge": ctx.last_model.estimated_net_edge if ctx.last_model else None,
                    "raw_edge": ctx.last_model.raw_edge if ctx.last_model else None,
                    "confidence": ctx.last_confidence,
                    "hold_reason": ctx.hold_reason,
                    "read": ctx.last_read.as_dict() if ctx.last_read else None,
                    "signals_today": ctx.signals_emitted,
                    "max_signals_per_match": self.settings.max_signals_per_match,
                    "analysis_mode": ctx.analysis_mode,
                    "display_state": ctx.display_state.value,
                    "observation_ends_ms": ctx.observation_ends_ms,
                    "observation_remaining_ms": obs_remaining,
                    "observation_remaining_display": format_mmss(obs_remaining),
                    "signals_emitted": ctx.signals_emitted,
                    "cooldown_until_ms": ctx.cooldown_until_ms,
                    "cooldown_remaining_ms": max(0.0, ctx.cooldown_until_ms - now),
                    "quote_updated_at_ms": (
                        market.last_update_ms if market and market.last_update_ms else None
                    ),
                    "data_age_ms": market.data_age_ms if market else None,
                    "tennis": ctx.tennis.to_dict() if ctx.tennis else {"available": False, "analysis_note": "MARKET-ONLY ANALYSIS"},
                    "active_signal": active.to_public_dict(now) if active else None,
                    "market_snapshot": analyzer.snapshot() if analyzer else None,
                }
            )

        actionable = [
            s.to_public_dict(now)
            for s in self.snap.signals.values()
            if s.is_actionable(now)
        ]
        # Safety: filter any that somehow aren't actionable
        actionable = [s for s in actionable if s["actionable"] and s["remaining_ms"] > 0]

        return {
            "server_time_ms": now,
            "connection_status": self.snap.connection_status.value,
            "live_match_count": len(self.snap.matches),
            "matches": cards,
            "actionable_signals": actionable,
            "signal_history": self.snap.signal_history[-50:],
            "no_live_markets": len(self.snap.matches) == 0,
            "message": "NO LIVE TENNIS MARKETS" if len(self.snap.matches) == 0 else None,
            "max_data_age_ms": self.settings.max_data_age_ms,
        }


def format_mmss(ms: float) -> str:
    total = int(max(0, ms) / 1000)
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def market_yes_player(ctx: MatchContext, market: MarketState) -> str:
    return ctx.player_a
