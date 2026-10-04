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
from app.services.signals.match_phase import (
    EntryView,
    MatchBaseline,
    SlipView,
    TrackedPosition,
    assess_entry,
    assess_slip,
    build_baseline,
    mark_position,
    side_mark_price,
)
from app.services.signals.patterns import (
    PatternAssessment,
    PatternEngine,
    PatternHealthView,
    book_from_market,
    bet_behind_copy,
    combine_exit_decision,
    series_from_ticks,
)
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
from app.services.tennis.service_games import ServiceGameTracker
from app.services.signals.tennis_reasoner import TennisPatternReasoner


logger = logging.getLogger(__name__)

_ENTRY_STATES = {
    "SEARCHING_FOR_ENTRY": SignalType.SEARCHING_FOR_ENTRY,
    "ENTRY_DEVELOPING": SignalType.ENTRY_DEVELOPING,
    "ENTRY_SIGNAL": SignalType.ENTRY_SIGNAL,
    "STRONG_ENTRY_SIGNAL": SignalType.STRONG_ENTRY_SIGNAL,
    "DO_NOT_ENTER": SignalType.DO_NOT_ENTER,
    "OPPORTUNITY_PASSED": SignalType.OPPORTUNITY_PASSED,
}
_SLIP_STATES = {
    "HOLD": SignalType.HOLD,
    "WATCH_CLOSELY": SignalType.WATCH_CLOSELY,
    "SLIPPING": SignalType.SLIPPING,
    "STOP_EXIT_SIGNAL": SignalType.STOP_EXIT_SIGNAL,
}
_PATTERN_STATES = {
    "SEARCHING": SignalType.SEARCHING_FOR_ENTRY,
    "PATTERN_DEVELOPING": SignalType.PATTERN_DEVELOPING,
    "PATTERN_WATCH": SignalType.PATTERN_WATCH,
    "PATTERN_ENTRY_SIGNAL": SignalType.PATTERN_ENTRY_SIGNAL,
    "STRONG_PATTERN_SIGNAL": SignalType.STRONG_PATTERN_SIGNAL,
    "PATTERN_ALREADY_ADVANCED": SignalType.PATTERN_ALREADY_ADVANCED,
    "FAILED_BREAKOUT": SignalType.FAILED_BREAKOUT,
    "PATTERN_HEALTHY": SignalType.PATTERN_HEALTHY,
    "PATTERN_WEAKENING": SignalType.PATTERN_WEAKENING,
    "PATTERN_AT_RISK": SignalType.PATTERN_AT_RISK,
    "PATTERN_BROKEN": SignalType.PATTERN_BROKEN,
}
_PATTERN_ENTRIES = {"PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL"}
_PATTERN_BET_TYPES = {
    SignalType.PATTERN_ENTRY_SIGNAL,
    SignalType.STRONG_PATTERN_SIGNAL,
}
# A live tennis quote moves a few cents during the countdown. The card stays
# up for that countdown unless the price runs this far past the entry.
_PATTERN_CHASE_CENTS = 8.0

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
    baseline: Optional[MatchBaseline] = None
    position: Optional[TrackedPosition] = None
    last_entry: Optional[EntryView] = None
    last_slip: Optional[SlipView] = None
    slip_confirmation: int = 0
    last_pattern: Optional[PatternAssessment] = None
    last_pattern_health: Optional[PatternHealthView] = None
    pattern_assessed_ms: float = 0.0
    pattern_cache_key: tuple = ()
    phase_events: list[dict[str, Any]] = field(default_factory=list)
    # 0 means the caller did not schedule a start (tests). None means unknown, so not live.
    scheduled_start_ms: Optional[float] = 0.0
    # Live evidence comes from the score feed or an explicit Kalshi live flag.
    score_confirmed: Optional[bool] = None
    live_evidence_expires_at_ms: Optional[float] = None
    # Verified completed service games observed since this match was linked.
    serves_seen: int = 0
    last_pattern_serve: int = 0
    serve_fingerprint: Optional[tuple[Any, ...]] = None
    points_were_visible: bool = False
    tracker: Any = None
    point_tracker: Any = None
    pending_point_records: list = field(default_factory=list)
    point_state_saved: Optional[str] = None
    reasoning_history: list = field(default_factory=list)
    pending_blocks: list = field(default_factory=list)
    latest_reasoning: Any = None
    persisted_game_ids: set = field(default_factory=set)
    baseline_service_strength: dict = field(default_factory=dict)
    last_feed_revision: int = 0
    invalidations_persisted: set = field(default_factory=set)
    match_status: str = "LIVE"
    event_id: Optional[str] = None
    market_ids: list = field(default_factory=list)
    ai_analysis: dict = field(default_factory=lambda: {"status": "WAITING", "message": "Waiting for two completed service games"})
    ai_history: list = field(default_factory=list)
    ai_request_id: str | None = None
    ai_task: Any = None
    hybrid_decision: dict = field(default_factory=dict)


def match_is_live(ctx: MatchContext, now_ms: float) -> bool:
    # The score feed is the authority when it has spoken. Kalshi's listed
    # start can be a day off, or still open long after the match ended.
    if ctx.score_confirmed is True:
        return ctx.live_evidence_expires_at_ms is None or now_ms < ctx.live_evidence_expires_at_ms
    if ctx.score_confirmed is False:
        return False
    start = ctx.scheduled_start_ms
    if start is None:
        return False
    if start <= 0:
        return True
    return False


@dataclass
class EngineSnapshot:
    connection_status: ConnectionStatus = ConnectionStatus.CONNECTED
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
        session_factory: Any = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.broadcast = broadcast
        self.session_factory = session_factory
        self.ttl_calc = SignalTTLCalculator(self.settings)
        self.conf_calc = SignalConfidenceCalculator(self.settings)
        self.prob_model = TennisProbabilityModel(self.settings)
        self.pattern_engine = PatternEngine(self.settings)
        self.reasoner = TennisPatternReasoner(self.settings)
        from app.services.signals.ai_patterns import GPTPatternAnalyst, HybridPatternDecisionEngine
        self.ai_analyst = GPTPatternAnalyst(self.settings)
        self.hybrid_engine = HybridPatternDecisionEngine(self.settings)
        self.ai_tasks = set()
        self.discovery_health = {}
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
        scheduled_start_ms: float | None = 0.0,
    ) -> MatchContext:
        now = now_ms or time.time() * 1000.0
        # Preserve the initial baseline study before any new entry.
        ctx = MatchContext(
            match_id=match_id,
            player_a=player_a,
            player_b=player_b,
            tournament=tournament,
            market_ticker=market_ticker,
            market_db_id=market_db_id,
            observation_started_ms=now,
            observation_ends_ms=now,
            analysis_mode=AnalysisMode.OBSERVING.value,
            display_state=SignalType.STUDYING_MATCH,
            scheduled_start_ms=scheduled_start_ms,
        )
        ctx.tracker = ServiceGameTracker(match_id)
        from app.services.tennis.points import TennisPointTracker
        ctx.point_tracker = TennisPointTracker(match_id)
        self.snap.matches[market_ticker] = ctx
        if market_ticker not in self.snap.analyzers:
            self.snap.analyzers[market_ticker] = RollingMarketAnalyzer(
                market_ticker, self.settings.window_sizes
            )
        return ctx

    def note_serves(self, ctx: MatchContext) -> None:
        """Count only verified completed service games and queue every block."""
        now = time.time() * 1000
        completed = ctx.tracker.update(ctx.tennis, now)
        if ctx.tracker.revision != ctx.last_feed_revision:
            ctx.persisted_game_ids.clear()
            ctx.last_feed_revision = ctx.tracker.revision
        if ctx.latest_reasoning and any(b.invalidated and b.block_id == ctx.latest_reasoning["block_id"] for b in ctx.tracker.blocks):
            ctx.latest_reasoning.update(reliable=False, decision="WAIT", reason="FEED CORRECTION — WAIT")
        ctx.serves_seen = len(ctx.tracker.games)
        ctx.pending_blocks.extend(completed)

    async def _reason_blocks(self, ctx, market, now):
        for block in ctx.tracker.blocks:
            if block.invalidated and block.block_id not in ctx.invalidations_persisted:
                reasoning = next((r for r in ctx.reasoning_history if r["block_id"] == block.block_id), None)
                if reasoning:
                    reasoning.update(reliable=False, decision="WAIT", reason="FEED CORRECTION — WAIT")
                    await self._persist_block(ctx, block, reasoning)
                ctx.invalidations_persisted.add(block.block_id)
        if any(g.game_id not in ctx.persisted_game_ids for g in ctx.tracker.games):
            await self._persist_games(ctx)
        while ctx.pending_blocks:
            block = ctx.pending_blocks.pop(0)
            analyzer = self.snap.analyzers[ctx.market_ticker]
            ticks = [t for t in analyzer.ticks_since(block.start_time) if t.ts_ms <= block.end_time]
            prices = [t.mid for t in ticks]
            if prices:
                block.starting_market_price, block.ending_market_price = prices[0], prices[-1]
                block.high_price, block.low_price = max(prices), min(prices)
                block.price_change = prices[-1] - prices[0]
            block.spread = market.spread
            block.liquidity = market.depth_yes + market.depth_no
            block.orderbook_imbalance = market.imbalance
            window = market.windows.get(5000)
            if window:
                block.market_volatility = window.volatility
                block.momentum = window.momentum
            if now >= ctx.observation_ends_ms:
                self._ensure_baseline(ctx, ctx.market_ticker)
            ctx.pattern_cache_key = ()
            ctx.pattern_assessed_ms = 0
            ctx.confirmation_count = 0
            view = self._assess_pattern(ctx, market, now)
            ctx.last_pattern = view
            earlier = ctx.tracker.blocks[:ctx.tracker.blocks.index(block)]
            if not ctx.baseline_service_strength:
                from app.services.signals.tennis_reasoner import service_score
                initial_games = ctx.tracker.blocks[0].games
                ctx.baseline_service_strength = {side: service_score(initial_games, side) for side in ("A", "B")}
            reasoning = self.reasoner.analyze(block, earlier, ctx.baseline, market, view, ctx.position, ctx.baseline_service_strength)
            winner = block.games[-1]['winner']
            winner_name = ctx.player_a if winner == 'A' else ctx.player_b
            reasoning['summary'] = f"{winner_name} won the game. {reasoning['summary']}"
            reasoning.update(pattern_type=view.pattern_type, maximum_entry_price=view.maximum_entry_price,
                             entry_zone_low=view.entry_zone_low, entry_zone_high=view.entry_zone_high)
            block.return_pressure = {side: values["return_pressure"] for side, values in reasoning["players"].items()}
            block.pattern_features = {"players": reasoning["players"], "patterns": reasoning["patterns"], "divergence": reasoning["divergence"]}
            ctx.latest_reasoning = reasoning
            ctx.reasoning_history.append(reasoning)
            ctx.last_pattern_serve = ctx.serves_seen
            await self._persist_block(ctx, block, reasoning)
            self.schedule_ai_analysis(ctx, block, market)

    def schedule_ai_analysis(self, ctx, block, market, deep=False):
        from app.services.signals.ai_patterns import build_snapshot
        if block.invalidated or len(block.games) != 2 or market.data_age_ms > self.settings.max_data_age_ms:
            return False
        if not self.settings.openai_api_key:
            ctx.ai_analysis = {"status": "UNAVAILABLE", "message": "AI ANALYSIS TEMPORARILY UNAVAILABLE"}
            return False
        if deep and ctx.ai_task and not ctx.ai_task.done():
            return False
        if ctx.ai_task and not ctx.ai_task.done():
            ctx.ai_task.cancel()
        try:
            snapshot = build_snapshot(ctx, block, market, self.snap.analyzers[ctx.market_ticker], self.settings)
        except Exception:
            logger.exception("Could not build AI feature snapshot")
            ctx.ai_analysis = {"status": "UNAVAILABLE", "message": "AI ANALYSIS TEMPORARILY UNAVAILABLE"}
            return False
        snapshot['deep_requested'] = deep
        ctx.ai_request_id = snapshot['analysis_request_id']
        ctx.ai_analysis = {"status": "PENDING", "message": "Analyzing completed ServeBlock"}
        task = asyncio.create_task(self._run_ai(ctx, block, snapshot, deep))
        ctx.ai_task = task
        self.ai_tasks.add(task)
        task.add_done_callback(self.ai_tasks.discard)
        return True

    def _ai_is_fresh(self, ctx, snapshot):
        market = self.snap.analyzers[ctx.market_ticker].state
        return (ctx.ai_request_id == snapshot['analysis_request_id']
            and ctx.latest_reasoning and ctx.latest_reasoning['block_id'] == snapshot['serve_block_id']
            and not any(b.invalidated and b.block_id == snapshot['serve_block_id'] for b in ctx.tracker.blocks)
            and time.time()*1000-snapshot['requested_at_ms'] <= self.settings.openai_max_advice_age_seconds*1000
            and market.data_age_ms <= self.settings.max_data_age_ms
            and market.status.upper() == 'OPEN'
            and abs(market.mid/100-snapshot['market_price_at_request']) < self.settings.openai_stale_price_move)

    async def _persist_ai(self, ctx, row):
        import json
        from app.database import AsyncSessionLocal
        from app.models import GPTPatternRecord
        try:
            async with (self.session_factory or AsyncSessionLocal)() as session:
                await session.merge(GPTPatternRecord(id=row['id'], match_id=ctx.match_id, payload=json.dumps(row, allow_nan=False)))
                await session.commit()
        except Exception:
            logger.exception("Could not persist AI pattern evaluation")

    async def _run_ai(self, ctx, block, snapshot, deep):
        from app.services.signals.ai_patterns import needs_second_opinion
        models = [self.settings.openai_deep_model if deep else self.settings.openai_pattern_model]
        try:
            for model in models:
                output = await self.ai_analyst.analyze(snapshot, model)
                # No lock is held during HTTP requests or persistence.
                async with self._lock:
                    fresh = self._ai_is_fresh(ctx, snapshot)
                    favored_yes = output.favored_side == snapshot['match']['yes_side']
                    quote = self.snap.analyzers[ctx.market_ticker].state.mid/100
                    responded = time.time()*1000
                    row = {'id': str(uuid4()), 'model': model, 'timestamp': responded,
                           'input_features': snapshot.copy(), 'output': output.model_dump(),
                           'status': 'VALID' if fresh else 'STALE',
                           'message': None if fresh else 'GPT ANALYSIS STALE',
                           'gpt_snapshot_time': snapshot.get('requested_at_ms'),
                           'gpt_response_time': responded,
                           'kalshi_price_at_snapshot': snapshot.get('market_price_at_request'),
                           'price_at_analysis': quote if favored_yes else 1-quote,
                           'horizon_prices': {}, 'maximum_favorable_movement': 0.0,
                           'maximum_adverse_movement': 0.0, 'pattern_success': None, 'pattern_failure': None}
                    ctx.ai_history.append(row)
                    if ctx.ai_request_id == snapshot['analysis_request_id']:
                        ctx.ai_analysis = row
                    if fresh:
                        view = self._assess_pattern(ctx, self.snap.analyzers[ctx.market_ticker].state, time.time()*1000)
                        ctx.hybrid_decision = self.hybrid_engine.decide(
                            view, row, self.snap.analyzers[ctx.market_ticker].state, snapshot['match']['yes_side'],
                            match_id=ctx.match_id, player_a=ctx.player_a, player_b=ctx.player_b,
                        )
                await self._persist_ai(ctx, row)
                if not fresh:
                    return
                if not deep and model == self.settings.openai_pattern_model and needs_second_opinion(output, snapshot, self.settings):
                    models.append(self.settings.openai_deep_model)
                    snapshot = dict(snapshot, first_opinion=output.model_dump())
                    ctx.ai_analysis = {"status": "PENDING", "message": "Resolving disagreement with second opinion"}
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log response bodies or API credentials.
            if ctx.ai_request_id == snapshot['analysis_request_id']:
                ctx.ai_analysis = {"status": "UNAVAILABLE", "message": "AI ANALYSIS TEMPORARILY UNAVAILABLE"}

    async def close(self):
        tasks = list(self.ai_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _persist_games(self, ctx):
        from app.database import AsyncSessionLocal
        from app.models import ServiceGameRecord
        import json
        try:
            async with (self.session_factory or AsyncSessionLocal)() as session:
                for game in ctx.tracker.games:
                    if game.game_id not in ctx.persisted_game_ids:
                        from dataclasses import asdict
                        await session.merge(ServiceGameRecord(id=f"{ctx.match_id}:{game.game_id}", match_id=ctx.match_id, payload=json.dumps(asdict(game))))
                await session.commit()
            ctx.persisted_game_ids.update(g.game_id for g in ctx.tracker.games)
        except Exception:
            logger.exception("Could not persist completed service games")

    async def _persist_points(self, ctx):
        from app.database import AsyncSessionLocal
        from app.models import TennisPointRecord, TennisPointStateRecord
        from sqlalchemy import select
        from hashlib import sha256
        import json
        if not ctx.point_tracker.events:
            return
        state = ctx.point_tracker.to_dict()
        # Avoid writes on quote refreshes; history and point state change on tennis updates.
        durable = {k: v for k, v in state.items() if k != 'updated_at_ms'}
        encoded = json.dumps(durable, sort_keys=True)
        if encoded == ctx.point_state_saved:
            return
        try:
            async with (self.session_factory or AsyncSessionLocal)() as session:
                records = {}
                for event in ctx.pending_point_records:
                    identity = f"{ctx.match_id}:{event['source_match_id']}:{event['basis']}:{event['seq']}"
                    point_id = sha256(identity.encode()).hexdigest()
                    records[point_id] = TennisPointRecord(id=point_id, match_id=ctx.match_id, payload=json.dumps(event))
                if records:
                    existing = set((await session.execute(select(TennisPointRecord.id).where(TennisPointRecord.id.in_(records)))).scalars())
                    session.add_all(row for point_id, row in records.items() if point_id not in existing)
                await session.merge(TennisPointStateRecord(id=ctx.match_id, match_id=ctx.match_id, payload=encoded))
                await session.commit()
            ctx.pending_point_records.clear()
            ctx.point_state_saved = encoded
        except Exception:
            logger.exception('Could not persist tennis point history')

    async def restore_match_history(self, ctx):
        from app.database import AsyncSessionLocal
        from app.models import ServiceGameRecord, ServeBlockRecord, ReasoningHistoryRecord, TennisPointRecord, TennisPointStateRecord
        from app.services.tennis.service_games import ServeBlock, ServiceGame
        from sqlalchemy import select
        import json
        try:
            async with (self.session_factory or AsyncSessionLocal)() as session:
                blocks = (await session.execute(select(ServeBlockRecord).where(ServeBlockRecord.match_id == ctx.match_id))).scalars()
                ctx.tracker.blocks = sorted([ServeBlock(**json.loads(row.payload)) for row in blocks], key=lambda b: b.end_time)
                rows = (await session.execute(select(ServiceGameRecord).where(ServiceGameRecord.match_id == ctx.match_id))).scalars()
                ctx.tracker.games = sorted([ServiceGame(**json.loads(row.payload)) for row in rows], key=lambda g: g.end_time)
                rows = (await session.execute(select(ReasoningHistoryRecord).where(ReasoningHistoryRecord.match_id == ctx.match_id))).scalars()
                ctx.reasoning_history = sorted([json.loads(row.payload) for row in rows], key=lambda r: r["end_time"])
                from app.models import GPTPatternRecord
                ai_rows = (await session.execute(select(GPTPatternRecord).where(GPTPatternRecord.match_id == ctx.match_id))).scalars()
                ctx.ai_history = sorted([json.loads(row.payload) for row in ai_rows], key=lambda r: r['timestamp'])
                used_games = {g['game_id'] for b in ctx.tracker.blocks for g in b.games}
                ctx.tracker.pending = [g for g in ctx.tracker.games if g.game_id not in used_games][-1:]
                point_state = await session.get(TennisPointStateRecord, ctx.match_id)
                if point_state:
                    saved = json.loads(point_state.payload)
                    ctx.point_tracker.source_match_id = saved.get('source_match_id')
                    ctx.point_tracker.basis = saved.get('basis', 'live')
                    point_rows = (await session.execute(select(TennisPointRecord).where(TennisPointRecord.match_id == ctx.match_id))).scalars()
                    ctx.point_tracker.events = {p['seq']: p for row in point_rows if (p := json.loads(row.payload)).get('source_match_id') == ctx.point_tracker.source_match_id and p.get('basis') == ctx.point_tracker.basis}
                    ctx.point_tracker.analyses = saved.get('analysis_history', [])
                    ctx.point_tracker.quality = saved.get('quality', 'unknown')
                    ctx.point_tracker.note = 'Restored point history; waiting for a fresh tennis update'
                    ctx.point_tracker.analysis = ctx.point_tracker.analyze()
            ctx.tracker.seen = {g.game_id for g in ctx.tracker.games}
            ctx.persisted_game_ids = ctx.tracker.seen.copy()
            ctx.serves_seen = len(ctx.tracker.games)
            if ctx.reasoning_history:
                ctx.latest_reasoning = dict(ctx.reasoning_history[-1], reliable=False, decision="WAIT", reason="Restart: awaiting fresh service-game evidence")
        except Exception:
            logger.exception("Could not restore match reasoning history")

    async def _persist_block(self, ctx, block, reasoning):
        from app.database import AsyncSessionLocal
        from app.models import ServiceGameRecord, ServeBlockRecord, ServeBlockFeatureRecord, ServeBlockReasoningRecord, TennisMarketDivergenceRecord, ReasoningHistoryRecord
        import json
        try:
            async with (self.session_factory or AsyncSessionLocal)() as session:
                for game in block.games:
                    record_id = f"{ctx.match_id}:{game['game_id']}"
                    await session.merge(ServiceGameRecord(id=record_id, match_id=ctx.match_id, payload=json.dumps(game)))
                for model, payload in [(ServeBlockRecord, block.as_dict()), (ServeBlockFeatureRecord, block.pattern_features),
                    (ServeBlockReasoningRecord, reasoning), (TennisMarketDivergenceRecord, {k: reasoning[k] for k in ('tennis_momentum', 'market_momentum', 'divergence')}),
                    (ReasoningHistoryRecord, reasoning)]:
                    await session.merge(model(id=block.block_id, match_id=ctx.match_id, payload=json.dumps(payload)))
                await session.commit()
        except Exception:
            logger.exception("Could not persist serve-block reasoning")

    async def on_tennis_update(self, ticker, status=None):
        """Score changes trigger block reasoning without refreshing old quote timestamps."""
        async with self._lock:
            ctx = self.snap.matches.get(ticker)
            analyzer = self.snap.analyzers.get(ticker)
            if ctx and analyzer:
                point_state = ctx.tennis if match_is_live(ctx, time.time() * 1000) else None
                ctx.pending_point_records.extend(dict(point, source_match_id=ctx.point_tracker.source_match_id, basis=ctx.point_tracker.basis)
                                                for point in ctx.point_tracker.update(point_state, time.time() * 1000))
                await self._persist_points(ctx)
                if status is not None:
                    analyzer.state.status = status
                self.note_serves(ctx)
                await self._reason_blocks(ctx, analyzer.state, time.time() * 1000)
                await self._validate_active_signal(ctx, analyzer.state, time.time() * 1000)
                if ctx.position:
                    await self._evaluate_position(ctx, analyzer.state, time.time() * 1000, emergency_forced=analyzer.state.data_age_ms > self.settings.max_data_age_ms)
                elif ctx.latest_reasoning:
                    if not ctx.latest_reasoning['reliable']:
                        self._show_pattern_study(ctx, analyzer.state, time.time() * 1000)
                    else:
                        ctx.hold_reason = ctx.latest_reasoning['reason']

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

        from app.services.signals.ai_patterns import update_outcomes
        if market.data_age_ms <= self.settings.max_data_age_ms and market.status.upper() == 'OPEN':
            changed = update_outcomes(ctx.ai_history, market.mid/100, market.last_update_ms, self.settings.max_data_age_ms)
            for row in changed:
                task = asyncio.create_task(self._persist_ai(ctx, row.copy()))
                self.ai_tasks.add(task)
                task.add_done_callback(self.ai_tasks.discard)
        if ctx.ai_analysis.get('status') == 'VALID' and not self._ai_is_fresh(ctx, ctx.ai_analysis['input_features']):
            ctx.ai_analysis = dict(ctx.ai_analysis, status='STALE', message='GPT ANALYSIS STALE')
        self.note_serves(ctx)
        block_completed = bool(ctx.pending_blocks)
        await self._reason_blocks(ctx, market, now)
        # Connection / stale / market status checks invalidate active signals
        await self._validate_active_signal(ctx, market, now)

        # Always score a quoted market so the dashboard shows a real read
        # during observation. Scoring never places a bet and never emits BET NOW.
        if market.yes_bid or market.yes_ask or market.last_trade:
            self._score_market(ctx, market)

        if self.snap.connection_status != ConnectionStatus.CONNECTED:
            if ctx.position is not None:
                await self._evaluate_position(ctx, market, now, emergency_forced=True)
                ctx.hold_reason = (
                    "Live connection is down. STOP / EXIT SIGNAL. Nothing is sold automatically."
                )
            else:
                ctx.display_state = SignalType.DATA_DELAY
                ctx.hold_reason = "Live connection is down. No entry on a disconnected feed."
            return

        status_u = (market.status or "").upper()
        if status_u in ("CLOSED", "SETTLED", "FINALIZED", "DETERMINED"):
            ctx.display_state = SignalType.NO_BET
            ctx.hold_reason = "Market is closed. No entry."
            return
        if status_u == "SUSPENDED":
            if ctx.position is not None:
                await self._evaluate_position(ctx, market, now, emergency_forced=True)
            else:
                ctx.display_state = SignalType.NO_BET
                ctx.hold_reason = "Market is suspended. No entry."
            return

        if not match_is_live(ctx, now):
            active = self._active_signal_for(ticker)
            if active and active.status == SignalStatus.ACTIVE and active.is_actionable(now):
                price = market.executable_yes_price() or market.mid
                active.cancel(
                    ExpirationReason.PATTERN_INVALIDATED,
                    price=price,
                    message="Match is not live. No bet.",
                    server_now_ms=now,
                )
                await self._emit_signal_update(active)
            ctx.display_state = SignalType.STUDYING_MATCH
            ctx.hold_reason = (
                "This game is not live. A bet is suggested only after the match starts."
            )
            return

        if ctx.position is not None:
            await self._evaluate_position(ctx, market, now, emergency_forced=False)
            return

        if market.data_age_ms > self.settings.max_data_age_ms:
            ctx.display_state = SignalType.DATA_DELAY
            ctx.hold_reason = (
                f"Quote is {market.data_age_ms / 1000:.1f}s old. "
                f"Data older than {self.settings.max_data_age_ms / 1000:.0f}s is not betable."
            )
            return

        # Cooldown after a manual exit. Do not recommend a new entry yet.
        if now < ctx.cooldown_until_ms:
            ctx.analysis_mode = AnalysisMode.COOLDOWN.value
            ctx.display_state = SignalType.COOLDOWN
            ctx.hold_reason = (
                "Cooldown after the exit. The match is still being watched. "
                "No new entry until the cooldown ends."
            )
            self._remember_phase(ctx, SignalType.COOLDOWN, now)
            return

        if (
            not self.settings.pattern_engine_enabled
            and ctx.signals_emitted >= self.settings.max_signals_per_match
        ):
            ctx.display_state = SignalType.DO_NOT_ENTER
            ctx.hold_reason = (
                f"This match already produced {ctx.signals_emitted} signals. "
                f"The cap is {self.settings.max_signals_per_match}."
            )
            return

        if not ctx.tracker.available:
            ctx.display_state = SignalType.SEARCHING_FOR_ENTRY
            ctx.hold_reason = "SERVICE-GAME DATA UNAVAILABLE — WAIT"
            self._attach_hybrid(ctx, market)
            return
        if ctx.tracker.available:
            reasoning = ctx.latest_reasoning
            if not reasoning or not reasoning["reliable"]:
                self._show_pattern_study(ctx, market, now)
                return
        if block_completed:
            ctx.display_state = SignalType.ENTRY_DEVELOPING if ctx.latest_reasoning and ctx.latest_reasoning["reliable"] else SignalType.SEARCHING_FOR_ENTRY
            ctx.hold_reason = ctx.latest_reasoning["reason"] if ctx.latest_reasoning else "WAIT"
            return
        await self._evaluate_entry(ctx, market, now)

    def _attach_hybrid(self, ctx: MatchContext, market: MarketState, view=None) -> None:
        """Publish the combined YES/WATCH/WAIT/NO/DO NOT CHASE read on every quote."""
        if view is None:
            view = ctx.last_pattern
        if view is None:
            from app.services.signals.patterns import PatternAssessment
            view = PatternAssessment(decision="SEARCHING", entry_score=0)
        yes_side = "PLAYER_A" if market_yes_player(ctx, market) == ctx.player_a else "PLAYER_B"
        ctx.hybrid_decision = self.hybrid_engine.decide(
            view, ctx.ai_analysis, market, yes_side,
            match_id=ctx.match_id, player_a=ctx.player_a, player_b=ctx.player_b,
        )

    def _show_pattern_study(self, ctx: MatchContext, market: MarketState, now: float) -> None:
        """Expose developing evidence without authorizing a betting entry."""
        reasoning = ctx.latest_reasoning
        ctx.display_state = SignalType.SEARCHING_FOR_ENTRY
        ctx.hold_reason = reasoning['reason'] if reasoning else 'WAITING FOR A COMPLETED GAME — WAIT'
        if not reasoning:
            return
        view = self._assess_pattern(ctx, market, now)
        ctx.last_pattern = view
        if view.pattern_type:
            ctx.display_state = _PATTERN_STATES.get(view.decision, SignalType.PATTERN_DEVELOPING)
            if view.decision in _PATTERN_ENTRIES:
                ctx.display_state = SignalType.PATTERN_DEVELOPING
            ctx.hold_reason = f"{view.pattern_name}: {view.explanation} Game-win check: {reasoning['reason']}"
        self._attach_hybrid(ctx, market, view)

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

    def _remember_phase(self, ctx: MatchContext, state: SignalType, now: float) -> None:
        if ctx.phase_events and ctx.phase_events[-1].get("state") == state.value:
            return
        ctx.phase_events.append({"t_ms": now, "state": state.value})
        if len(ctx.phase_events) > 40:
            ctx.phase_events = ctx.phase_events[-40:]

    def _ensure_baseline(self, ctx: MatchContext, ticker: str) -> MatchBaseline:
        if ctx.baseline is not None:
            return ctx.baseline
        analyzer = self.snap.analyzers.get(ticker)
        cutoff = ctx.tracker.blocks[0].end_time if ctx.tracker.blocks else time.time() * 1000
        ticks = [t for t in analyzer.ticks_since(ctx.observation_started_ms) if t.ts_ms <= cutoff] if analyzer else []
        ctx.baseline = build_baseline(ticks, self.settings)
        return ctx.baseline

    def _pattern_points(self, ctx: MatchContext) -> list:
        analyzer = self.snap.analyzers.get(ctx.market_ticker)
        ticks = analyzer.ticks_since(ctx.observation_started_ms) if analyzer else []
        # Keep the recent path. Older completed patterns already sit in match memory.
        if len(ticks) > 400:
            ticks = ticks[-400:]
        return series_from_ticks(ticks)

    def _assess_pattern(self, ctx: MatchContext, market: MarketState, now: float) -> PatternAssessment:
        analyzer = self.snap.analyzers.get(ctx.market_ticker)
        tick_count = len(analyzer.ticks_since(ctx.observation_started_ms)) if analyzer else 0
        cache_key = (tick_count, round(market.mid, 2), ctx.confirmation_count, round(market.imbalance, 2))
        interval = self.settings.pattern_eval_interval_ms
        if ctx.last_pattern is not None and ctx.pattern_cache_key == cache_key:
            return ctx.last_pattern
        # A pattern that is still developing is rechecked on the next quote.
        # Everything else waits out the interval so scoring cannot stall login.
        if ctx.last_pattern is not None and interval > 0:
            developing = ctx.last_pattern.decision == "PATTERN_DEVELOPING"
            wait_ms = min(80, interval) if developing else interval
            if (
                now - ctx.pattern_assessed_ms < wait_ms
                and ctx.last_pattern.confirmation_count == ctx.confirmation_count
            ):
                return ctx.last_pattern
        edge = 0.0
        if ctx.last_read is not None:
            edge = ctx.last_read.uncertainty_adjusted_edge
        elif ctx.last_model is not None:
            edge = ctx.last_model.estimated_net_edge
        book = book_from_market(market, self.settings, edge)
        window = market.windows.get(5000)
        if window and (window.volatility or 0) > self.settings.extreme_volatility:
            book.extreme = True
        baseline = ctx.baseline.normal_volatility if ctx.baseline else None
        view = self.pattern_engine.assess(
            ticker=ctx.market_ticker,
            points=self._pattern_points(ctx),
            book=book,
            confirmation_count=ctx.confirmation_count,
            tournament=ctx.tournament,
            player_a=ctx.player_a,
            player_b=ctx.player_b,
            tennis=ctx.tennis,
            baseline_volatility_override=baseline,
            now_ms=now,
        )
        ctx.pattern_assessed_ms = now
        ctx.pattern_cache_key = cache_key
        return view

    async def _evaluate_entry(self, ctx: MatchContext, market: MarketState, now: float) -> None:
        ticker = ctx.market_ticker
        self._ensure_baseline(ctx, ticker)
        tennis = ctx.tennis
        ctx.analysis_mode = (
            AnalysisMode.TENNIS_ENHANCED.value
            if tennis and tennis.available
            else AnalysisMode.MARKET_ONLY.value
        )
        prob = ctx.last_model
        if prob is None or ctx.last_confidence is None:
            ctx.display_state = SignalType.SEARCHING_FOR_ENTRY
            ctx.hold_reason = "Waiting for a usable Kalshi quote."
            return
        tennis_ok = bool(tennis and tennis.available) or prob.source == "tennis_enhanced"
        read = assess_market(
            self.settings,
            market,
            prob,
            confidence=ctx.last_confidence,
            confirmation_count=ctx.confirmation_count,
            tennis_available=tennis_ok,
            best_seen_ask=ctx.best_seen_ask,
        )
        ctx.last_read = read
        if not self.settings.pattern_engine_enabled:
            await self._evaluate_legacy_entry(ctx, market, now, prob, read)
            return
        previous_reasoning_state = (ctx.latest_reasoning.get("decision"), ctx.latest_reasoning.get("confirmation_count")) if ctx.latest_reasoning else None
        view = self._assess_pattern(ctx, market, now)
        if ctx.latest_reasoning and view.pattern_type != ctx.latest_reasoning.get("pattern_type"):
            ctx.latest_reasoning.update(reliable=False, decision="WAIT", reason="PATTERN CHANGED — WAIT FOR NEXT SERVICE BLOCK")
            ctx.confirmation_count = 0
            view.confirming = False
            view.decision = "SEARCHING"
        if ctx.latest_reasoning:
            view.entry_score = min(view.entry_score, ctx.latest_reasoning["entry_score"])
            view.confidence = min(view.confidence, ctx.latest_reasoning["pattern_confidence"])
            if not ctx.latest_reasoning["reliable"] or view.entry_score < self.settings.pattern_entry_score:
                view.confirming = False
                view.decision = "PATTERN_WATCH"
            if view.decision == "STRONG_PATTERN_SIGNAL" and view.entry_score < self.settings.strong_pattern_entry_score:
                view.decision = "PATTERN_ENTRY_SIGNAL"
        if view.confirming:
            ctx.confirmation_count += 1
            view = self._assess_pattern(ctx, market, now)
        else:
            ctx.confirmation_count = 0
        if ctx.latest_reasoning:
            view.entry_score = min(view.entry_score, ctx.latest_reasoning["entry_score"])
            view.confidence = min(view.confidence, ctx.latest_reasoning["pattern_confidence"])
            if view.decision == "STRONG_PATTERN_SIGNAL" and (view.entry_score < self.settings.strong_pattern_entry_score or view.confidence < self.settings.strong_pattern_confidence):
                view.decision = "PATTERN_ENTRY_SIGNAL"
        ctx.last_pattern = view
        if ctx.latest_reasoning:
            ctx.latest_reasoning["confirmation_count"] = ctx.confirmation_count
            if ctx.latest_reasoning.get("questions"):
                ctx.latest_reasoning["questions"]["tradeable_window_now"] = view.decision in _PATTERN_ENTRIES
        ask = market.executable_yes_price() if view.player_side != "NO" else market.executable_no_price()
        view.current_price = ask
        if ctx.latest_reasoning and ctx.latest_reasoning.get("maximum_entry_price") is not None:
            view.maximum_entry_price = ctx.latest_reasoning["maximum_entry_price"]
            view.entry_zone_low = ctx.latest_reasoning["entry_zone_low"]
            view.entry_zone_high = ctx.latest_reasoning["entry_zone_high"]
            if ask > view.maximum_entry_price:
                view.decision = "PATTERN_ALREADY_ADVANCED"
                view.confirming = False
                ctx.confirmation_count = 0
        if view.entry_score >= self.settings.pattern_watch_score and (
            ctx.best_seen_ask is None or ask < ctx.best_seen_ask
        ):
            ctx.best_seen_ask = ask

        self._attach_hybrid(ctx, market, view)
        active = self._active_signal_for(ticker)
        if active and active.is_actionable(now):
            book_text = " ".join(view.blockers)
            book_broke = any(
                phrase in book_text
                for phrase in ("Liquidity", "Spread", "stale", "extreme")
            )
            still_valid = (
                view.pattern_type == (active.pattern_type or view.pattern_type)
                and view.decision not in ("FAILED_BREAKOUT", "PATTERN_ALREADY_ADVANCED", "SEARCHING")
                and view.stage not in ("LATE", "COMPLETED")
                and ask <= active.maximum_entry_price
                and not view.orderbook_only
                and bool(view.pattern_type)
                and not book_broke
                and not ctx.hybrid_decision.get("blockers")
            )
            if not still_valid:
                if ask > active.maximum_entry_price:
                    reason = ExpirationReason.PRICE_MOVED
                    message = "DO NOT ENTER. PRICE MOVED BEYOND ENTRY WINDOW."
                elif view.decision == "PATTERN_ALREADY_ADVANCED":
                    reason = ExpirationReason.PATTERN_INVALIDATED
                    message = "PATTERN INVALIDATED. Pattern progressed beyond ideal entry zone."
                else:
                    reason = ExpirationReason.PATTERN_INVALIDATED
                    message = "PATTERN INVALIDATED. " + (view.explanation or "The pattern is no longer confirmed.")
                self.pattern_engine.record_expiry(view.pattern_type or active.pattern_type or "")
                active.cancel(reason, price=ask, message=message, server_now_ms=now)
                await self._emit_signal_update(active)
                ctx.confirmation_count = 0
            else:
                ctx.display_state = active.signal_type
                ctx.hold_reason = view.explanation
                return

        if ctx.latest_reasoning:
            ctx.latest_reasoning["decision"] = {"PATTERN_DEVELOPING": "ENTRY DEVELOPING", "PATTERN_ENTRY_SIGNAL": "ENTRY", "STRONG_PATTERN_SIGNAL": "STRONG ENTRY", "PATTERN_ALREADY_ADVANCED": "DO NOT CHASE"}.get(view.decision, "WATCH" if ctx.latest_reasoning["reliable"] else "WAIT")
        if ctx.latest_reasoning and previous_reasoning_state != (ctx.latest_reasoning.get("decision"), ctx.latest_reasoning.get("confirmation_count")):
            block = next((b for b in ctx.tracker.blocks if b.block_id == ctx.latest_reasoning["block_id"]), None)
            if block:
                await self._persist_block(ctx, block, ctx.latest_reasoning)
        state = _PATTERN_STATES.get(view.decision, SignalType.SEARCHING_FOR_ENTRY)
        ctx.display_state = state
        ctx.hold_reason = view.explanation
        self._remember_phase(ctx, state, now)
        if view.decision in _PATTERN_ENTRIES:
            self.pattern_engine.record_signal(
                view.pattern_type,
                similarity=view.similarity,
                confidence=view.confidence,
                entry_score=view.entry_score,
                progress=view.progress,
            )
            await self._emit_bet_signal(
                ctx=ctx,
                market=market,
                prob=prob,
                confidence=view.entry_score,
                strong=view.decision == "STRONG_PATTERN_SIGNAL",
                now_ms=now,
                read=read,
                signal_kind=(
                    SignalType.STRONG_PATTERN_SIGNAL
                    if view.decision == "STRONG_PATTERN_SIGNAL"
                    else SignalType.PATTERN_ENTRY_SIGNAL
                ),
                max_entry_override=view.maximum_entry_price,
                player_name=view.player,
                trade_direction=view.player_side,
                pattern=view,
            )

    async def _evaluate_legacy_entry(
        self,
        ctx: MatchContext,
        market: MarketState,
        now: float,
        prob: ProbabilityResult,
        read: MarketRead,
    ) -> None:
        """Previous entry-score path, used only when the pattern engine is turned off."""
        baseline = ctx.baseline or self._ensure_baseline(ctx, ctx.market_ticker)
        view = assess_entry(
            self.settings,
            market,
            prob,
            read,
            baseline,
            confirmation_count=ctx.confirmation_count,
            best_seen_ask=ctx.best_seen_ask,
            player_a=ctx.player_a,
            player_b=ctx.player_b,
        )
        if view.qualifying:
            ctx.confirmation_count += 1
            view = assess_entry(
                self.settings,
                market,
                prob,
                read,
                baseline,
                confirmation_count=ctx.confirmation_count,
                best_seen_ask=ctx.best_seen_ask,
                player_a=ctx.player_a,
                player_b=ctx.player_b,
            )
        elif view.decision != "ENTRY_DEVELOPING":
            ctx.confirmation_count = max(0, ctx.confirmation_count - 1)
        ctx.last_entry = view
        ask = view.current_price
        active = self._active_signal_for(ctx.market_ticker)
        if active and active.is_actionable(now) and view.decision not in ("ENTRY_SIGNAL", "STRONG_ENTRY_SIGNAL"):
            reason = (
                ExpirationReason.PRICE_MOVED
                if ask > active.maximum_entry_price
                else ExpirationReason.EDGE_DISAPPEARED
            )
            active.cancel(reason, price=ask, server_now_ms=now)
            await self._emit_signal_update(active)
            ctx.confirmation_count = 0
        elif active and active.is_actionable(now):
            ctx.display_state = active.signal_type
            ctx.hold_reason = view.explanation
            return
        state = _ENTRY_STATES.get(view.decision, SignalType.SEARCHING_FOR_ENTRY)
        ctx.display_state = state
        ctx.hold_reason = view.explanation
        if view.decision in ("ENTRY_SIGNAL", "STRONG_ENTRY_SIGNAL"):
            await self._emit_bet_signal(
                ctx=ctx,
                market=market,
                prob=prob,
                confidence=view.entry_score,
                strong=view.decision == "STRONG_ENTRY_SIGNAL",
                now_ms=now,
                read=read,
                signal_kind=(
                    SignalType.STRONG_ENTRY_SIGNAL
                    if view.decision == "STRONG_ENTRY_SIGNAL"
                    else SignalType.ENTRY_SIGNAL
                ),
                max_entry_override=view.maximum_entry_price,
                player_name=view.player,
                trade_direction=view.direction,
            )

    async def _evaluate_position(
        self,
        ctx: MatchContext,
        market: MarketState,
        now: float,
        *,
        emergency_forced: bool,
    ) -> None:
        position = ctx.position
        if position is None:
            return
        baseline = self._ensure_baseline(ctx, ctx.market_ticker)
        price = side_mark_price(market, position.direction)
        mark_position(position, price)
        model_p = ctx.last_model.model_win_probability if ctx.last_model else position.entry_model_probability
        if position.direction == "NO" and ctx.last_model is not None:
            model_p = 1.0 - ctx.last_model.model_win_probability
        if emergency_forced:
            ctx.slip_confirmation = self.settings.exit_confirmation_count
        elif True:
            preview = assess_slip(
                self.settings,
                market,
                position,
                baseline,
                model_probability=model_p,
                confirmation_count=ctx.slip_confirmation,
                emergency_forced=False,
            )
            if preview.emergency:
                ctx.slip_confirmation = self.settings.exit_confirmation_count
            elif preview.slip_score >= self.settings.slipping_score:
                ctx.slip_confirmation += 1
            elif preview.slip_score < self.settings.watch_slip_score:
                ctx.slip_confirmation = 0
            else:
                ctx.slip_confirmation = max(0, ctx.slip_confirmation - 1)
        view = assess_slip(
            self.settings,
            market,
            position,
            baseline,
            model_probability=model_p,
            confirmation_count=ctx.slip_confirmation,
            emergency_forced=emergency_forced,
        )
        ctx.last_slip = view
        health = None
        if self.settings.pattern_engine_enabled and position.pattern_type:
            book = book_from_market(market, self.settings)
            if position.direction == "NO":
                book.price = price
                book.imbalance = -book.imbalance
            health = self.pattern_engine.monitor(
                ticker=ctx.market_ticker,
                points=self._pattern_points(ctx),
                book=book,
                entry_price=position.entry_price,
                peak_price=position.peak_price,
                entered_at_ms=position.entered_at_ms,
                now_ms=now,
                pattern_type=position.pattern_type,
                expected_move=position.expected_move,
                typical_duration_ms=position.typical_duration_ms,
                entry_imbalance=position.entry_imbalance,
                volatility=baseline.normal_volatility,
                tennis=ctx.tennis,
            )
            ctx.last_pattern_health = health
        if health is not None:
            combined = combine_exit_decision(
                pattern_health=health.health,
                pattern_broken=health.broken,
                slip_decision=view.decision,
                slip_score=view.slip_score,
                emergency=view.emergency or emergency_forced,
                settings=self.settings,
            )
            if combined == "STOP_EXIT_SIGNAL" and health.broken:
                state = SignalType.PATTERN_BROKEN
            else:
                state = _PATTERN_STATES.get(combined) or _SLIP_STATES.get(combined, SignalType.HOLD)
            ctx.hold_reason = (
                f"{health.explanation} Slip score {view.slip_score:.0f}. "
                "Nothing is sold automatically."
            )
        else:
            state = _SLIP_STATES.get(view.decision, SignalType.HOLD)
            ctx.hold_reason = view.explanation
        if ctx.latest_reasoning and ctx.latest_reasoning.get("pattern_health") is not None:
            if ctx.latest_reasoning["pattern_health"] <= 25:
                state = SignalType.STOP_EXIT_SIGNAL
                ctx.hold_reason = "PATTERN BROKEN — STOP / EXIT SIGNAL. Manual decision required."
            elif ctx.latest_reasoning["pattern_health"] <= 40 and state == SignalType.HOLD:
                state = SignalType.SLIPPING
        ctx.display_state = state
        ctx.analysis_mode = "POSITION"
        self._remember_phase(ctx, state, now)

    def open_position(
        self,
        ticker: str,
        *,
        position_id: str,
        player: str,
        direction: str,
        entry_price: float,
        amount: float,
        now_ms: float | None = None,
    ) -> None:
        """Switch this match from entry search to position protection. No order is sent."""
        ctx = self.snap.matches.get(ticker)
        if not ctx:
            return
        now = now_ms or time.time() * 1000.0
        analyzer = self.snap.analyzers.get(ticker)
        market = analyzer.state if analyzer else None
        mom = 0.0
        imb = 0.0
        depth = 0.0
        spread = 0.0
        if market is not None:
            w5 = market.windows.get(5000)
            mom = (w5.momentum if w5 else 0.0) or 0.0
            imb = market.imbalance
            depth = market.depth_yes + market.depth_no
            spread = market.spread
        model_p = ctx.last_model.model_win_probability if ctx.last_model else 0.5
        score = ctx.last_entry.entry_score if ctx.last_entry else (ctx.last_confidence or 0.0)
        pattern = ctx.last_pattern
        active = self._active_signal_for(ticker)
        if active and active.status == SignalStatus.ACTIVE:
            active.cancel(ExpirationReason.PATTERN_INVALIDATED, message="Manual entry recorded; position monitor active.", server_now_ms=now)
        ctx.position = TrackedPosition(
            position_id=position_id,
            player=player,
            direction=direction,
            entry_price=entry_price,
            amount=amount,
            entered_at_ms=now,
            entry_momentum=mom,
            entry_imbalance=imb if direction == "YES" else -imb,
            entry_liquidity=depth,
            entry_spread=spread,
            entry_model_probability=model_p if direction == "YES" else 1.0 - model_p,
            entry_signal_score=pattern.entry_score if pattern else score,
            entry_trade_flow=mom,
            peak_price=entry_price,
            current_price=entry_price,
            pattern_id=pattern.pattern_id if pattern else "",
            pattern_type=pattern.pattern_type if pattern else "",
            pattern_stage=pattern.stage if pattern else "",
            pattern_confidence=pattern.confidence if pattern else 0.0,
            pattern_entry_score=pattern.entry_score if pattern else score,
            expected_move=pattern.expected_move if pattern else 0.0,
            typical_duration_ms=pattern.typical_duration_ms if pattern else 0.0,
        )
        ctx.slip_confirmation = 0
        ctx.confirmation_count = 0
        ctx.cooldown_until_ms = 0.0
        ctx.display_state = SignalType.PATTERN_HEALTHY if pattern and pattern.pattern_type else SignalType.HOLD
        ctx.hold_reason = (
            "PATTERN MONITOR MODE. Is the pattern still valid? "
            f"Recorded {pattern.pattern_name if pattern else 'the position'} "
            f"at {entry_price:.0f}¢. No order was sent."
        )
        ctx.analysis_mode = "POSITION"
        self._remember_phase(ctx, ctx.display_state, now)

    def close_position(self, ticker: str, now_ms: float | None = None) -> None:
        ctx = self.snap.matches.get(ticker)
        if ctx:
            ctx.position = None
            ctx.last_slip = None
            ctx.slip_confirmation = 0
            self._remember_phase(ctx, SignalType.COOLDOWN, now_ms or time.time() * 1000.0)
        self.start_cooldown(ticker, now_ms)

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
            parts.append(
                "No pattern is suggested until the match is past the first serve "
                "and not in the last 5 serves with a clear winner."
            )
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

        if not match_is_live(ctx, now):
            sig.cancel(ExpirationReason.MARKET_SUSPENDED, price=sig.market_price, message="Live match evidence expired. Do not enter.", server_now_ms=now)
            await self._emit_signal_update(sig)
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
        if status_u in ("CLOSED", "SETTLED", "FINALIZED", "DETERMINED"):
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
            if sig.signal_type in (
                SignalType.ENTRY_SIGNAL,
                SignalType.STRONG_ENTRY_SIGNAL,
                SignalType.PATTERN_ENTRY_SIGNAL,
                SignalType.STRONG_PATTERN_SIGNAL,
            ):
                ctx.display_state = SignalType.DO_NOT_ENTER
                ctx.hold_reason = "DO NOT ENTER. PRICE MOVED BEYOND ENTRY WINDOW. DO NOT CHASE."
            else:
                ctx.display_state = SignalType.DO_NOT_CHASE
            ctx.confirmation_count = 0
            return

        direction_sign = 1 if sig.direction == "YES" else -1
        reason = None
        if market.spread > self.settings.max_spread_cents:
            reason = ExpirationReason.SPREAD_EXPANSION
        elif market.depth_yes + market.depth_no < self.settings.min_liquidity_contracts:
            reason = ExpirationReason.LIQUIDITY_LOSS
        elif market.imbalance * direction_sign < -0.55:
            reason = ExpirationReason.ORDERBOOK_REVERSAL
        elif ctx.latest_reasoning and not ctx.latest_reasoning["reliable"] and sig.signal_type in _PATTERN_BET_TYPES:
            reason = ExpirationReason.PATTERN_INVALIDATED
        if reason:
            sig.cancel(reason, price=current, server_now_ms=now)
            await self._emit_signal_update(sig)
            return

        if sig.signal_type in (
            SignalType.ENTRY_SIGNAL,
            SignalType.STRONG_ENTRY_SIGNAL,
            SignalType.PATTERN_ENTRY_SIGNAL,
            SignalType.STRONG_PATTERN_SIGNAL,
        ):
            # Hard safety already ran. Pattern and entry passes cancel the signal
            # when the setup breaks. Do not apply the old fixed-edge cancel here.
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
        signal_kind: SignalType | None = None,
        max_entry_override: float | None = None,
        player_name: str | None = None,
        trade_direction: str | None = None,
        pattern: PatternAssessment | None = None,
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

        direction = trade_direction or prob.direction
        ask = market.executable_yes_price() if direction == "YES" else market.executable_no_price()
        model_cap = read.max_entry_price_cents if read else ask + self.settings.max_entry_slippage_cents
        max_entry = max_entry_override if max_entry_override is not None else model_cap
        kind = signal_kind or (SignalType.STRONG_BET_SIGNAL if strong else SignalType.BET_SIGNAL)
        named_player = player_name or prob.player
        if pattern is not None:
            bet_instruction, market_instruction = bet_behind_copy(
                named_player,
                direction,
                pattern.pattern_type,
            )
            ttl_ms = 20_000
        else:
            bet_instruction, market_instruction = "", ""
            ttl_ms = 20_000

        sig = LiveSignal(
            signal_id=signal_id,
            signal_version=ctx.version_counter,
            match_id=ctx.match_id,
            market_id=ctx.market_db_id,
            market_ticker=ctx.market_ticker,
            signal_type=kind,
            player=named_player,
            direction=direction,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + ttl_ms,
            original_ttl_ms=ttl_ms,
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
            pattern_type=pattern.pattern_type if pattern else "",
            pattern_name=pattern.pattern_name if pattern else "",
            pattern_similarity=pattern.similarity if pattern else None,
            pattern_confidence=pattern.confidence if pattern else None,
            pattern_entry_score=pattern.entry_score if pattern else None,
            entry_zone_low=pattern.entry_zone_low if pattern else None,
            entry_zone_high=pattern.entry_zone_high if pattern else None,
            pattern_progress=pattern.progress if pattern else None,
            pattern_stage=pattern.stage if pattern else "",
            bet_instruction=bet_instruction,
            market_instruction=market_instruction,
            pattern_reasons=list(pattern.reasons) if pattern else [],
            lifecycle=[
                {
                    "event_type": "created",
                    "ts_ms": now_ms,
                    "market_price": ask,
                    "confidence": confidence,
                    "net_edge": prob.estimated_net_edge,
                    "ttl_ms": ttl_ms,
                    "ttl_reason": "20-second manual entry window; cancels when the setup breaks",
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
            ttl_ms / 1000.0,
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
            ctx.display_state = SignalType.COOLDOWN
            self._remember_phase(ctx, SignalType.COOLDOWN, now)

    def tick_expirations(self) -> list[LiveSignal]:
        """Called periodically to expire TTL without waiting for market tick."""
        now = time.time() * 1000.0
        expired: list[LiveSignal] = []
        for sig in self.snap.signals.values():
            if sig.status != SignalStatus.ACTIVE:
                continue
            analyzer = self.snap.analyzers.get(sig.market_ticker)
            market = analyzer.state if analyzer else None
            reason = None
            if now >= sig.expires_at_ms:
                reason = ExpirationReason.TTL_EXPIRED
            elif self.snap.matches.get(sig.market_ticker) and not match_is_live(self.snap.matches[sig.market_ticker], now):
                reason = ExpirationReason.MARKET_SUSPENDED
            elif market is None or market.data_age_ms > self.settings.max_data_age_ms:
                reason = ExpirationReason.STALE_DATA
            if reason:
                sig.cancel(reason, price=sig.market_price, server_now_ms=now)
                expired.append(sig)
        return expired

    def dashboard_payload(self) -> dict[str, Any]:
        now = time.time() * 1000.0
        cards = []
        live = [
            (ticker, ctx)
            for ticker, ctx in self.snap.matches.items()
            if match_is_live(ctx, now) and (not self.snap.analyzers.get(ticker) or self.snap.analyzers[ticker].state.status.upper() not in {"CLOSED", "SETTLED", "FINALIZED", "DETERMINED", "SUSPENDED"})
        ]
        live_tickers = {ticker for ticker, _ in live}
        for ticker, ctx in live:
            analyzer = self.snap.analyzers.get(ticker)
            market = analyzer.state if analyzer else None
            active = self._active_signal_for(ticker)
            obs_remaining = max(0.0, ctx.observation_ends_ms - now)
            cards.append(
                {
                    "match_id": ctx.match_id,
                    "event_id": ctx.event_id,
                    "market_ids": ctx.market_ids,
                    "match_status": ctx.match_status,
                    "live_evidence_expires_at_ms": ctx.live_evidence_expires_at_ms,
                    "service_games_analyzed": len(ctx.tracker.games),
                    "ai_pattern_analysis": {k: v for k, v in ctx.ai_analysis.items() if k in ('status', 'message', 'model', 'timestamp', 'output')},
                    "hybrid_decision": ctx.hybrid_decision,
                    "serve_block_progress": len(ctx.tracker.pending),
                    "service_game_data_available": ctx.tracker.available,
                    "service_game_data_note": ctx.tracker.note,
                    "latest_reasoning": ctx.latest_reasoning,
                    "reasoning_history": ctx.reasoning_history,
                    "points": ctx.point_tracker.to_dict(),

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
                    "baseline": ctx.baseline.as_dict() if ctx.baseline else None,
                    "entry": ctx.last_entry.as_dict() if ctx.last_entry else None,
                    "slip": ctx.last_slip.as_dict() if ctx.last_slip else None,
                    "pattern": _pattern_card(ctx.last_pattern),
                    "pattern_health": ctx.last_pattern_health.as_dict() if ctx.last_pattern_health else None,
                    "phase_events": ctx.phase_events[-12:],
                    "position": _position_payload(ctx),
                }
            )

        actionable = [
            s.to_public_dict(now)
            for s in self.snap.signals.values()
            if s.is_actionable(now) and s.market_ticker in self.snap.matches and not self.snap.matches[s.market_ticker].position
        ]
        # Safety: filter any that somehow aren't actionable
        actionable = [s for s in actionable if s["actionable"] and s["remaining_ms"] > 0]
        actionable = [s for s in actionable if s.get("market_ticker") in live_tickers]
        actionable.sort(key=lambda item: item.get("created_at_ms") or 0, reverse=True)

        return {
            "discovery_health": self.discovery_health,
            "server_time_ms": now,
            "connection_status": self.snap.connection_status.value,
            "live_match_count": len(cards),
            "matches": cards,
            "actionable_signals": actionable,
            "signal_history": self.snap.signal_history[-50:],
            "no_live_markets": len(cards) == 0 and self.discovery_health.get("complete", False) and self.discovery_health.get("live_verification_available", True),
            "message": ("LIVE MATCH VERIFICATION UNAVAILABLE" if self.discovery_health.get("live_verification_available") is False else "NO LIVE TENNIS MARKETS ON KALSHI" if self.discovery_health.get("complete", False) else "TENNIS DISCOVERY UNAVAILABLE — RECONCILIATION PENDING") if len(cards) == 0 else None,
            "max_data_age_ms": self.settings.max_data_age_ms,
        }


def _pattern_card(pattern: Any) -> dict[str, Any] | None:
    """Board fields only. Stored examples and the raw book stay off this payload."""
    if pattern is None:
        return None
    card = pattern.as_dict()
    card.pop("prior_examples", None)
    return card


def _position_payload(ctx: MatchContext) -> dict[str, Any] | None:
    pos = ctx.position
    if pos is None:
        return None
    slip = ctx.last_slip
    return {
        "position_id": pos.position_id,
        "player": pos.player,
        "direction": pos.direction,
        "entry_price": pos.entry_price,
        "amount": pos.amount,
        "current_price": pos.current_price,
        "peak_price": pos.peak_price,
        "drawdown_from_peak": pos.drawdown_from_peak,
        "slip_score": slip.slip_score if slip else None,
        "health": slip.health if slip else None,
        "pattern_health": ctx.last_pattern_health.health if ctx.last_pattern_health else None,
        "pattern_id": pos.pattern_id,
        "pattern_type": pos.pattern_type,
        "pattern_stage": pos.pattern_stage,
        "pattern_confidence": pos.pattern_confidence,
        "pattern_entry_score": pos.pattern_entry_score,
        "status": ctx.display_state.value,
        "entered_at_ms": pos.entered_at_ms,
    }


def format_mmss(ms: float) -> str:
    total = int(max(0, ms) / 1000)
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def market_yes_player(ctx: MatchContext, market: MarketState) -> str:
    return ctx.player_a
