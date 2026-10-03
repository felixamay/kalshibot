"""Match-specific pattern learning.

Raw prices become structures this match has already shown. A detected pattern
is not a bet. Entry still needs repetition, confirmation, and a tradable book.
Nothing in this module places an order.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from app.config import Settings

PATTERN_FAMILIES = (
    "PULLBACK_RECOVERY",
    "TREND_CONTINUATION",
    "BREAKOUT",
    "FAILED_BREAKOUT",
    "REVERSAL",
    "SUPPORT_BOUNCE",
    "RESISTANCE_REJECTION",
    "RANGE_TRADING",
    "MOMENTUM_EXPANSION",
    "MOMENTUM_EXHAUSTION",
    "ORDERBOOK_REVERSAL",
    "LIQUIDITY_ABSORPTION",
    "SERVE_CHANGE",
    "BREAK_POINT_REACTION",
)

PATTERN_NAMES = {
    "PULLBACK_RECOVERY": "Pullback + Recovery",
    "TREND_CONTINUATION": "Trend Continuation",
    "BREAKOUT": "Breakout",
    "FAILED_BREAKOUT": "Failed Breakout",
    "REVERSAL": "Reversal",
    "SUPPORT_BOUNCE": "Support Bounce",
    "RESISTANCE_REJECTION": "Resistance Rejection",
    "RANGE_TRADING": "Range Trading",
    "MOMENTUM_EXPANSION": "Momentum Expansion",
    "MOMENTUM_EXHAUSTION": "Momentum Exhaustion",
    "ORDERBOOK_REVERSAL": "Order-Book Reversal",
    "LIQUIDITY_ABSORPTION": "Liquidity Absorption",
    "SERVE_CHANGE": "Serve-Change Pattern",
    "BREAK_POINT_REACTION": "Break-Point Reaction",
    "BID_STACKING": "Bid Stacking",
    "ASK_ABSORPTION": "Ask Absorption",
    "BID_DISAPPEARANCE": "Bid Disappearance",
    "ORDERBOOK_FLIP": "Orderbook Flip",
    "LIQUIDITY_WALL": "Liquidity Wall",
    "LIQUIDITY_VACUUM": "Liquidity Vacuum",
}

# Order-book structures can support a price pattern. They cannot open an entry alone.
ORDERBOOK_ONLY = {
    "ORDERBOOK_REVERSAL",
    "LIQUIDITY_ABSORPTION",
    "BID_STACKING",
    "ASK_ABSORPTION",
    "BID_DISAPPEARANCE",
    "ORDERBOOK_FLIP",
    "LIQUIDITY_WALL",
    "LIQUIDITY_VACUUM",
}

PRICE_FAMILIES = [name for name in PATTERN_FAMILIES if name not in ORDERBOOK_ONLY]


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def pattern_name(pattern_type: str) -> str:
    return PATTERN_NAMES.get(pattern_type, pattern_type.replace("_", " ").title())


def bet_behind_copy(player: str, side: str, pattern_type: str) -> tuple[str, str]:
    """Tell the user which side the discovered pattern is for."""
    name = player.strip() or "this player"
    if pattern_type in {"SERVE_CHANGE", "BREAK_POINT_REACTION"} and side != "NO":
        return (
            f"Bet behind {name}",
            "That is this player's market. You place the bet manually on Kalshi.",
        )
    if side == "NO":
        return (
            "Bet behind the market",
            f"NO on this contract. That is against {name}. You place the bet manually on Kalshi.",
        )
    return (
        "Bet behind the market",
        f"YES on this market — {name}. You place the bet manually on Kalshi.",
    )


def normalized_move(price_change: float, baseline_volatility: float, floor: float = 0.75) -> float:
    """Scale a price change by this match's own volatility.

    A 4¢ move is large when normal volatility is 1¢ and ordinary when it is 5¢.
    """
    scale = baseline_volatility if baseline_volatility and baseline_volatility > 0 else floor
    return price_change / scale


@dataclass
class SeriesPoint:
    ts_ms: float
    price: float
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 1.0
    microprice: float = 0.0
    imbalance: float = 0.0
    depth_bid: float = 0.0
    depth_ask: float = 0.0
    volume: float = 0.0
    trade_direction: float = 0.0


@dataclass
class BookSnapshot:
    price: float
    spread: float = 1.0
    imbalance: float = 0.0
    depth_bid: float = 0.0
    depth_ask: float = 0.0
    momentum: float = 0.0
    acceleration: float = 0.0
    trade_velocity: float = 0.0
    liquidity_quality: str = "MEDIUM"
    spread_quality: str = "GOOD"
    fresh: bool = True
    adjusted_edge: float = 0.0
    microprice: float = 0.0
    extreme: bool = False


@dataclass
class PatternInstance:
    pattern_id: str
    pattern_type: str
    player_side: str
    start_timestamp: float
    end_timestamp: float
    start_price: float
    lowest_price: float
    highest_price: float
    recovery_price: float
    duration: float
    price_change: float
    volatility: float
    spread: float
    liquidity: float
    orderbook_before: dict[str, Any]
    orderbook_during: dict[str, Any]
    orderbook_after: dict[str, Any]
    trade_flow: float
    result: str
    success_or_failure: str
    normalized_pullback: float = 0.0
    normalized_recovery: float = 0.0
    tournament: Optional[str] = None
    cluster_id: Optional[int] = None
    cluster_name: str = ""
    ticker: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "pattern_name": pattern_name(self.pattern_type),
            "player_side": self.player_side,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "start_price": round(self.start_price, 2),
            "lowest_price": round(self.lowest_price, 2),
            "highest_price": round(self.highest_price, 2),
            "recovery_price": round(self.recovery_price, 2),
            "duration": round(self.duration, 1),
            "price_change": round(self.price_change, 2),
            "volatility": round(self.volatility, 3),
            "spread": round(self.spread, 2),
            "liquidity": round(self.liquidity, 1),
            "orderbook_before": self.orderbook_before,
            "orderbook_during": self.orderbook_during,
            "orderbook_after": self.orderbook_after,
            "trade_flow": round(self.trade_flow, 3),
            "result": self.result,
            "success_or_failure": self.success_or_failure,
            "normalized_pullback": round(self.normalized_pullback, 3),
            "normalized_recovery": round(self.normalized_recovery, 3),
            "tournament": self.tournament,
            "cluster_id": self.cluster_id,
            "cluster_name": self.cluster_name,
        }


@dataclass
class PatternAssessment:
    decision: str
    pattern_type: str = ""
    pattern_name: str = ""
    pattern_id: str = ""
    player: str = ""
    player_side: str = "YES"
    similarity: float = 0.0
    confidence: float = 0.0
    entry_score: float = 0.0
    confirmation_count: int = 0
    confirmation_needed: int = 3
    repetition_label: str = "NONE"
    occurrences: int = 0
    successes: int = 0
    failures: int = 0
    success_rate: Optional[float] = None
    low_sample_size: bool = True
    stage: str = "EARLY"
    progress: float = 0.0
    expected_move: float = 0.0
    current_move: float = 0.0
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    maximum_entry_price: float = 0.0
    current_price: float = 0.0
    tradeable: bool = False
    confirming: bool = False
    orderbook_only: bool = False
    support_zones: list[dict[str, Any]] = field(default_factory=list)
    resistance_zones: list[dict[str, Any]] = field(default_factory=list)
    orderbook_evidence: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    explanation: str = ""
    typical_pullback: float = 0.0
    typical_recovery: float = 0.0
    typical_duration_ms: float = 0.0
    timeline: list[dict[str, Any]] = field(default_factory=list)
    prior_examples: list[dict[str, Any]] = field(default_factory=list)
    cluster_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "pattern_type": self.pattern_type,
            "pattern_name": self.pattern_name,
            "pattern_id": self.pattern_id,
            "player": self.player,
            "player_side": self.player_side,
            "similarity": round(self.similarity, 1),
            "confidence": round(self.confidence, 1),
            "entry_score": round(self.entry_score, 1),
            "confirmation_count": self.confirmation_count,
            "confirmation_needed": self.confirmation_needed,
            "repetition_label": self.repetition_label,
            "occurrences": self.occurrences,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": None if self.success_rate is None else round(self.success_rate, 3),
            "low_sample_size": self.low_sample_size,
            "stage": self.stage,
            "progress": round(self.progress, 1),
            "expected_move": round(self.expected_move, 2),
            "current_move": round(self.current_move, 2),
            "entry_zone_low": round(self.entry_zone_low, 2),
            "entry_zone_high": round(self.entry_zone_high, 2),
            "maximum_entry_price": round(self.maximum_entry_price, 2),
            "current_price": round(self.current_price, 2),
            "tradeable": self.tradeable,
            "confirming": self.confirming,
            "orderbook_only": self.orderbook_only,
            "support_zones": self.support_zones,
            "resistance_zones": self.resistance_zones,
            "orderbook_evidence": self.orderbook_evidence,
            "reasons": self.reasons,
            "blockers": self.blockers,
            "explanation": self.explanation,
            "typical_pullback": round(self.typical_pullback, 2),
            "typical_recovery": round(self.typical_recovery, 2),
            "typical_duration_ms": round(self.typical_duration_ms, 0),
            "timeline": self.timeline[-12:],
            "prior_examples": self.prior_examples[:5],
            "cluster_name": self.cluster_name,
            "success_note": (
                "Observed continuation rate only. This is not a guaranteed future probability."
            ),
        }


@dataclass
class PatternHealthView:
    health: float
    decision: str
    time_decay_score: float
    broken: bool
    failures: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    explanation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "health": round(self.health, 1),
            "decision": self.decision,
            "time_decay_score": round(self.time_decay_score, 1),
            "broken": self.broken,
            "failures": self.failures,
            "reasons": self.reasons,
            "explanation": self.explanation,
        }


def repetition_label(same_match_count: int, effective_count: float, minimum: int) -> str:
    """One sighting is only an observation. History can fill a thin same-match sample."""
    if same_match_count >= 3:
        return "RECURRING"
    if same_match_count >= 2 or (same_match_count >= 1 and effective_count >= max(2.0, float(minimum))):
        return "POTENTIAL"
    if same_match_count == 1 or effective_count >= 1:
        return "OBSERVED ONLY"
    return "NONE"


def progress_stage(progress: float, late: float, completed: float) -> str:
    if progress >= completed:
        return "COMPLETED"
    if progress >= late:
        return "LATE"
    if progress >= 50:
        return "MATURE"
    if progress >= 25:
        return "DEVELOPING"
    return "EARLY"


def cluster_zones(levels: list[float], width: float) -> list[dict[str, Any]]:
    """Group repeated prices into zones. One cent is not a level by itself."""
    if width <= 0:
        width = 1.5
    zones: list[dict[str, Any]] = []
    for level in sorted(levels):
        if zones and level <= zones[-1]["anchor_high"] + width:
            bucket = zones[-1]
            bucket["prices"].append(level)
            bucket["anchor_high"] = max(bucket["anchor_high"], level)
            bucket["anchor_low"] = min(bucket["anchor_low"], level)
        else:
            zones.append({"anchor_low": level, "anchor_high": level, "prices": [level]})
    ready = []
    for zone in zones:
        if len(zone["prices"]) < 2:
            continue
        mid = sum(zone["prices"]) / len(zone["prices"])
        ready.append(
            {
                "low": round(mid - width / 2.0, 2),
                "high": round(mid + width / 2.0, 2),
                "touches": len(zone["prices"]),
                "prices": [round(p, 2) for p in zone["prices"]],
            }
        )
    return ready


def support_zones(lows: list[float], width: float) -> list[dict[str, Any]]:
    return cluster_zones(lows, width)


def resistance_zones(highs: list[float], width: float) -> list[dict[str, Any]]:
    return cluster_zones(highs, width)


def baseline_volatility(points: list[SeriesPoint], floor: float) -> float:
    if len(points) < 3:
        return floor
    returns = [points[i].price - points[i - 1].price for i in range(1, len(points))]
    mean = sum(returns) / len(returns)
    var = sum((item - mean) ** 2 for item in returns) / len(returns)
    return max(floor, var ** 0.5)


def find_swings(points: list[SeriesPoint], threshold: float) -> list[tuple[int, float, str]]:
    if len(points) < 2 or threshold <= 0:
        return []
    pivots: list[tuple[int, float, str]] = []
    mode: Optional[str] = None
    extreme_i = 0
    extreme_p = points[0].price
    start_i = 0
    for i in range(1, len(points)):
        delta = points[i].price - points[start_i].price
        if delta >= threshold:
            pivots.append((start_i, points[start_i].price, "LOW"))
            mode = "up"
            extreme_i = i
            extreme_p = points[i].price
            break
        if delta <= -threshold:
            pivots.append((start_i, points[start_i].price, "HIGH"))
            mode = "down"
            extreme_i = i
            extreme_p = points[i].price
            break
    if mode is None:
        return []
    for i in range(extreme_i + 1, len(points)):
        price = points[i].price
        if mode == "up":
            if price >= extreme_p:
                extreme_i, extreme_p = i, price
            elif extreme_p - price >= threshold:
                pivots.append((extreme_i, extreme_p, "HIGH"))
                mode = "down"
                extreme_i, extreme_p = i, price
        elif price <= extreme_p:
            extreme_i, extreme_p = i, price
        elif price - extreme_p >= threshold:
            pivots.append((extreme_i, extreme_p, "LOW"))
            mode = "up"
            extreme_i, extreme_p = i, price
    kind = "HIGH" if mode == "up" else "LOW"
    pivots.append((extreme_i, extreme_p, kind))
    return pivots


def _book_slice(point: SeriesPoint) -> dict[str, Any]:
    return {
        "imbalance": round(point.imbalance, 3),
        "depth_bid": round(point.depth_bid, 1),
        "depth_ask": round(point.depth_ask, 1),
        "spread": round(point.spread, 2),
        "microprice": round(point.microprice or point.price, 2),
    }


def pattern_similarity_score(
    left_pullback: float,
    right_pullback: float,
    left_recovery: float,
    right_recovery: float,
    duration_ratio: float = 1.0,
) -> float:
    """Compare normalized moves, not raw cents."""

    def _near(left: float, right: float) -> float:
        scale = max(abs(left), abs(right), 0.5)
        return 100.0 * (1.0 - min(1.0, abs(left - right) / scale))

    depth = _near(left_pullback, right_pullback)
    recovery = _near(left_recovery, right_recovery) if right_recovery or left_recovery else depth
    duration = 100.0 * (1.0 - min(1.0, abs(duration_ratio - 1.0) / 2.0))
    return _clamp(0.6 * depth + 0.25 * recovery + 0.15 * duration)


def time_decay_score(elapsed_ms: float, typical_ms: float, progress: float) -> float:
    """Long recoveries that have not actually recovered lose health."""
    if typical_ms <= 0 or progress >= 50:
        return 0.0
    ratio = elapsed_ms / typical_ms
    if ratio <= 1.25:
        return 0.0
    return _clamp((ratio - 1.25) * 45.0)


def detect_orderbook_evidence(points: list[SeriesPoint]) -> list[str]:
    if len(points) < 4:
        return []
    recent = points[-6:]
    first, last = recent[0], recent[-1]
    notes: list[str] = []
    if last.imbalance > 0.28 and last.depth_bid > first.depth_bid * 1.15:
        notes.append("BID_STACKING")
    if last.price >= first.price - 0.4 and last.depth_ask < first.depth_ask * 0.75 and first.depth_ask > 0:
        notes.append("ASK_ABSORPTION")
    if first.depth_bid > 0 and last.depth_bid < first.depth_bid * 0.55:
        notes.append("BID_DISAPPEARANCE")
    if first.imbalance * last.imbalance < 0 and abs(last.imbalance - first.imbalance) > 0.25:
        notes.append("ORDERBOOK_FLIP")
    total = last.depth_bid + last.depth_ask
    if total > 0 and max(last.depth_bid, last.depth_ask) / total > 0.78:
        notes.append("LIQUIDITY_WALL")
    if 0 < total < 40:
        notes.append("LIQUIDITY_VACUUM")
    if "ORDERBOOK_FLIP" in notes or (first.imbalance > 0.15 and last.imbalance < -0.15):
        notes.append("ORDERBOOK_REVERSAL")
    if "ASK_ABSORPTION" in notes or "BID_STACKING" in notes:
        notes.append("LIQUIDITY_ABSORPTION")
    return notes


class PatternFailureDetector:
    """Reasons a live pattern is no longer the one that was entered."""

    def detect(
        self,
        *,
        entry_price: float,
        current_price: float,
        peak_price: float,
        expected_move: float,
        elapsed_ms: float,
        typical_duration_ms: float,
        volatility: float,
        imbalance: float,
        entry_imbalance: float,
        momentum: float,
        support_low: Optional[float],
        failed_breakout: bool,
        tennis_invalid: bool,
    ) -> list[str]:
        reasons: list[str] = []
        vol = max(volatility, 0.75)
        if support_low is not None and current_price < support_low - vol * 0.4:
            reasons.append("Expected support broken")
        recovered = current_price - entry_price
        if expected_move > 0 and elapsed_ms > typical_duration_ms * 1.6 and recovered < expected_move * 0.25:
            reasons.append("Failed recovery")
        if momentum < -vol * 0.8:
            reasons.append("Momentum reversed")
        adverse = max(0.0, peak_price - current_price)
        if adverse >= vol * 2.2:
            reasons.append("Unexpected volatility")
        if entry_imbalance > 0.05 and imbalance < -0.12:
            reasons.append("Order-book reversal")
        if momentum < 0 and imbalance < 0:
            reasons.append("Trade-flow reversal")
        decay = time_decay_score(elapsed_ms, typical_duration_ms, (recovered / expected_move * 100.0) if expected_move else 0)
        if decay >= 35:
            reasons.append("Time-based pattern failure")
        if failed_breakout:
            reasons.append("Failed breakout")
        if tennis_invalid:
            reasons.append("Tennis-state invalidation")
        return reasons


def pattern_health_score(
    *,
    entry_price: float,
    current_price: float,
    peak_price: float,
    expected_move: float,
    elapsed_ms: float,
    typical_duration_ms: float,
    volatility: float,
    imbalance: float,
    momentum: float,
    failures: list[str],
) -> tuple[float, float]:
    vol = max(volatility, 0.75)
    move = current_price - entry_price
    progress = (move / expected_move * 100.0) if expected_move else 0.0
    decay = time_decay_score(elapsed_ms, typical_duration_ms, progress)
    health = 90.0
    if momentum >= 0 and imbalance >= 0:
        health += 6
    if momentum < 0:
        health -= min(24.0, abs(momentum) / vol * 12.0)
    if imbalance < 0:
        health -= min(18.0, abs(imbalance) * 40.0)
    drawdown = max(0.0, peak_price - current_price)
    health -= min(28.0, drawdown / vol * 10.0)
    health -= decay
    health -= min(36.0, 8.0 * len(failures))
    if "Expected support broken" in failures:
        health -= 8
    if "Failed breakout" in failures:
        health -= 14
    return _clamp(health), decay


def combine_exit_decision(
    *,
    pattern_health: float,
    pattern_broken: bool,
    slip_decision: str,
    slip_score: float,
    emergency: bool,
    settings: Settings,
) -> str:
    """Pattern health and the slip detector agree on the exit, or either can stop it."""
    if (
        emergency
        or pattern_broken
        or pattern_health < settings.pattern_broken_health
        or slip_decision == "STOP_EXIT_SIGNAL"
        or (
            pattern_health < settings.pattern_risk_health
            and slip_score >= settings.stop_exit_score
        )
    ):
        return "STOP_EXIT_SIGNAL"
    if pattern_health < settings.pattern_risk_health:
        return "PATTERN_AT_RISK"
    if pattern_health < settings.pattern_weaken_health or slip_decision == "SLIPPING":
        return "PATTERN_WEAKENING" if pattern_health < 75 else slip_decision
    if slip_decision == "WATCH_CLOSELY" and pattern_health < 80:
        return "PATTERN_WEAKENING"
    return "PATTERN_HEALTHY"


class PatternEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.match_memory: dict[str, list[PatternInstance]] = defaultdict(list)
        self.historical: list[PatternInstance] = []
        self._clusters: dict[tuple, int] = {}
        self._next_cluster = 1
        self._last_server: dict[str, Optional[str]] = {}
        self._last_break_points: dict[str, tuple[Optional[int], Optional[int]]] = {}
        self.performance: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "occurrences": 0,
                "successful_continuations": 0,
                "failed_patterns": 0,
                "entry_signals": 0,
                "late_entries": 0,
                "false_positives": 0,
                "expiries": 0,
                "favorable_sum": 0.0,
                "adverse_sum": 0.0,
                "duration_sum": 0.0,
                "entry_timing_sum": 0.0,
                "similarity_sum": 0.0,
                "confidence_sum": 0.0,
                "entry_score_sum": 0.0,
            }
        )

    def record_signal(self, pattern_type: str, *, similarity: float, confidence: float, entry_score: float, progress: float) -> None:
        row = self.performance[pattern_type or "UNKNOWN"]
        row["entry_signals"] += 1
        row["similarity_sum"] += similarity
        row["confidence_sum"] += confidence
        row["entry_score_sum"] += entry_score
        row["entry_timing_sum"] += progress
        if progress >= self.settings.pattern_late_stage_percent:
            row["late_entries"] += 1

    def record_expiry(self, pattern_type: str) -> None:
        self.performance[pattern_type or "UNKNOWN"]["expiries"] += 1

    def assess(
        self,
        *,
        ticker: str,
        points: list[SeriesPoint],
        book: BookSnapshot,
        confirmation_count: int = 0,
        tournament: Optional[str] = None,
        player_a: str = "Player A",
        player_b: str = "Player B",
        tennis: Any = None,
        baseline_volatility_override: Optional[float] = None,
        now_ms: Optional[float] = None,
    ) -> PatternAssessment:
        settings = self.settings
        floor = settings.baseline_volatility_floor
        vol = baseline_volatility_override or baseline_volatility(points, floor)
        vol = max(vol, floor)
        threshold = max(floor * 1.1, vol * 1.1)
        zone_width = max(1.5, vol * 1.5)
        swings = find_swings(points, threshold)
        self._store_completed(ticker, points, swings, vol, threshold, tournament, zone_width)
        lows = [price for _, price, kind in swings if kind == "LOW"]
        highs = [price for _, price, kind in swings if kind == "HIGH"]
        supports = support_zones(lows, zone_width)
        resistances = resistance_zones(highs, zone_width)
        evidence = detect_orderbook_evidence(points)
        tennis_type = self._tennis_pattern(ticker, tennis)
        setup = self._current_setup(
            points,
            swings,
            threshold,
            vol,
            supports,
            resistances,
            evidence,
            tennis_type,
            book,
        )
        needed = settings.pattern_confirmation_count
        if setup is None:
            return PatternAssessment(
                decision="SEARCHING",
                player=player_a,
                confirmation_count=confirmation_count,
                confirmation_needed=needed,
                support_zones=supports,
                resistance_zones=resistances,
                orderbook_evidence=evidence,
                orderbook_only=bool(evidence) and not setup,
                explanation=(
                    "Searching for the next repeating setup. "
                    + (
                        "Order-book structure is evidence only and cannot open an entry by itself."
                        if evidence
                        else "No repeated price structure is forming."
                    )
                ),
                current_price=book.price,
            )

        pattern_type = setup["pattern_type"]
        same = [item for item in self.match_memory[ticker] if item.pattern_type == pattern_type]
        historical = self._historical_like(ticker, pattern_type, setup, vol, tournament)
        effective = (
            len(same) * settings.weight_memory_current
            + historical["tournament"] * settings.weight_memory_tournament
            + historical["other"] * settings.weight_memory_historical
        )
        label = repetition_label(len(same), effective, settings.min_pattern_occurrences)
        successes = sum(1 for item in same if item.success_or_failure == "success")
        failures = sum(1 for item in same if item.success_or_failure == "failure")
        sample = len(same) + historical["count"]
        hist_successes = historical["successes"]
        hist_total = historical["count"]
        pooled_success = successes + hist_successes
        pooled_total = len(same) + hist_total
        success_rate = (pooled_success / pooled_total) if pooled_total else None
        low_sample = pooled_total < settings.min_pattern_sample
        typical_recovery = self._typical(same, historical["rows"], "recovery", setup["pullback"] * 1.8 or vol * 3)
        typical_pullback = self._typical(same, historical["rows"], "pullback", setup["pullback"] or vol * 2)
        typical_duration = self._typical(same, historical["rows"], "duration", 18_000)
        expected = max(typical_recovery, vol)
        current_move = max(0.0, setup["recovery"])
        progress = _clamp(current_move / expected * 100.0, 0, 150)
        stage = progress_stage(progress, settings.pattern_late_stage_percent, settings.pattern_completed_percent)
        similarity = self._similarity(setup, same, historical["rows"], vol)
        local_mom = 0.0
        if len(points) >= 2:
            local_mom = points[-1].price - points[max(0, len(points) - 4)].price
        momentum = local_mom if local_mom > 0 else book.momentum
        parts = self._component_scores(
            book,
            similarity=similarity,
            label=label,
            success_rate=success_rate,
            low_sample=low_sample,
            momentum=momentum,
            progress=progress,
            expected=expected,
        )
        confidence = self._weighted_confidence(parts)
        entry_score = self._weighted_entry(parts, confidence)
        signals = self._confirmation_signals(setup, book, momentum, points)
        confirming = (
            len(signals) >= 3
            and stage in ("EARLY", "DEVELOPING", "MATURE")
            and pattern_type not in ORDERBOOK_ONLY
            and pattern_type != "FAILED_BREAKOUT"
            and label in ("POTENTIAL", "RECURRING")
            and book.fresh
            and not book.extreme
            and book.liquidity_quality not in ("LOW", "VERY_LOW")
            and book.spread_quality not in ("POOR", "VERY_POOR")
        )
        zone_low = round(book.price - 1.0, 2)
        zone_high = round(book.price + 1.0, 2)
        maximum = round(book.price + settings.max_entry_slippage_cents, 2)
        cluster = self._cluster_name(pattern_type, setup, vol)
        timeline = [
            {"t_ms": setup["start_ts"], "kind": "pattern_start", "price": setup["start"]},
            {"t_ms": setup["low_ts"], "kind": "pattern_low", "price": setup["low"]},
            {"t_ms": points[-1].ts_ms if points else 0, "kind": "pattern_high", "price": setup["high"]},
        ]
        if confirmation_count > 0:
            timeline.append({"t_ms": points[-1].ts_ms if points else 0, "kind": "confirmation", "price": book.price})
        decision, tradeable, blockers, reasons = self._decide(
            pattern_type=pattern_type,
            label=label,
            stage=stage,
            progress=progress,
            confidence=confidence,
            entry_score=entry_score,
            confirmation_count=confirmation_count,
            confirming_signals=len(signals),
            book=book,
            evidence=evidence,
        )
        if decision in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL"):
            timeline.append({"t_ms": now_ms or (points[-1].ts_ms if points else 0), "kind": "entry_signal", "price": book.price})
            tradeable = True
        player = player_a if setup.get("side", "YES") == "YES" else player_b
        side = setup.get("side", "YES")
        headline, detail = bet_behind_copy(player, side, pattern_type)
        explanation = self._explain(
            decision=decision,
            pattern_type=pattern_type,
            similarity=similarity,
            confidence=confidence,
            entry_score=entry_score,
            confirmation_count=confirmation_count,
            needed=needed,
            label=label,
            occurrences=len(same),
            successes=successes,
            stage=stage,
            progress=progress,
            low_sample=low_sample,
            success_rate=success_rate,
            zone_low=zone_low,
            zone_high=zone_high,
            maximum=maximum,
            price=book.price,
            blockers=blockers,
            reasons=reasons,
            bet_headline=headline,
            bet_detail=detail,
        )
        return PatternAssessment(
            decision=decision,
            pattern_type=pattern_type,
            pattern_name=pattern_name(pattern_type),
            pattern_id=setup["pattern_id"],
            player=player_a if setup.get("side", "YES") == "YES" else player_b,
            player_side=setup.get("side", "YES"),
            similarity=similarity,
            confidence=confidence,
            entry_score=entry_score,
            confirmation_count=confirmation_count,
            confirmation_needed=needed,
            repetition_label=label,
            occurrences=len(same),
            successes=successes,
            failures=failures,
            success_rate=success_rate,
            low_sample_size=low_sample,
            stage=stage,
            progress=progress,
            expected_move=expected,
            current_move=current_move,
            entry_zone_low=zone_low,
            entry_zone_high=zone_high,
            maximum_entry_price=maximum,
            current_price=book.price,
            tradeable=tradeable,
            confirming=confirming,
            orderbook_only=pattern_type in ORDERBOOK_ONLY,
            support_zones=supports,
            resistance_zones=resistances,
            orderbook_evidence=evidence,
            reasons=reasons + signals,
            blockers=blockers,
            explanation=explanation,
            typical_pullback=typical_pullback,
            typical_recovery=typical_recovery,
            typical_duration_ms=typical_duration,
            timeline=timeline,
            prior_examples=[item.as_dict() for item in same[-5:]],
            cluster_name=cluster,
        )

    def monitor(
        self,
        *,
        ticker: str,
        points: list[SeriesPoint],
        book: BookSnapshot,
        entry_price: float,
        peak_price: float,
        entered_at_ms: float,
        now_ms: float,
        pattern_type: str,
        expected_move: float,
        typical_duration_ms: float,
        entry_imbalance: float,
        volatility: float,
        tennis: Any = None,
    ) -> PatternHealthView:
        vol = max(volatility, self.settings.baseline_volatility_floor)
        threshold = max(vol * 1.1, 0.75)
        swings = find_swings(points, threshold)
        lows = [price for _, price, kind in swings if kind == "LOW"]
        highs = [price for _, price, kind in swings if kind == "HIGH"]
        width = max(1.5, vol * 1.5)
        supports = support_zones(lows, width)
        resistances = resistance_zones(highs, width)
        support_low = supports[-1]["low"] if supports else None
        failed = self._failed_breakout(points, resistances, threshold)
        tennis_invalid = False
        if tennis is not None and getattr(tennis, "available", False):
            previous = self._last_server.get(ticker)
            server = getattr(tennis, "server", None)
            if previous and server and previous != server and pattern_type == "SERVE_CHANGE":
                tennis_invalid = False
            if pattern_type == "BREAK_POINT_REACTION" and not (
                getattr(tennis, "break_points_a", None) or getattr(tennis, "break_points_b", None)
            ):
                tennis_invalid = True
        local_mom = 0.0
        if len(points) >= 2:
            local_mom = points[-1].price - points[max(0, len(points) - 4)].price
        momentum = local_mom if abs(local_mom) > abs(book.momentum) else book.momentum
        failures = PatternFailureDetector().detect(
            entry_price=entry_price,
            current_price=book.price,
            peak_price=peak_price,
            expected_move=expected_move,
            elapsed_ms=max(0.0, now_ms - entered_at_ms),
            typical_duration_ms=typical_duration_ms or 15_000,
            volatility=vol,
            imbalance=book.imbalance,
            entry_imbalance=entry_imbalance,
            momentum=momentum,
            support_low=support_low,
            failed_breakout=failed,
            tennis_invalid=tennis_invalid,
        )
        health, decay = pattern_health_score(
            entry_price=entry_price,
            current_price=book.price,
            peak_price=peak_price,
            expected_move=expected_move,
            elapsed_ms=max(0.0, now_ms - entered_at_ms),
            typical_duration_ms=typical_duration_ms or 15_000,
            volatility=vol,
            imbalance=book.imbalance,
            momentum=momentum,
            failures=failures,
        )
        broken = health < self.settings.pattern_broken_health or len(failures) >= 3 or failed
        if health < self.settings.pattern_broken_health or broken:
            decision = "PATTERN_BROKEN"
        elif health < self.settings.pattern_risk_health:
            decision = "PATTERN_AT_RISK"
        elif health < self.settings.pattern_weaken_health:
            decision = "PATTERN_WEAKENING"
        else:
            decision = "PATTERN_HEALTHY"
        reasons = failures or ["Momentum healthy", "Order book supportive"]
        name = pattern_name(pattern_type or "PULLBACK_RECOVERY")
        explanation = (
            f"{decision.replace('_', ' ')}. Health {health:.0f}. "
            f"Entry {entry_price:.0f}¢. Current {book.price:.0f}¢. Peak {peak_price:.0f}¢. "
            f"Pattern {name}. Expected move {expected_move:+.1f}¢. "
            f"Current move {book.price - entry_price:+.1f}¢. Time decay {decay:.0f}."
        )
        if decision == "PATTERN_BROKEN":
            explanation += " PATTERN BROKEN. STOP / EXIT SIGNAL. You decide. Nothing is sold automatically."
        elif decision == "PATTERN_AT_RISK":
            explanation += " PREPARE TO EXIT."
        elif decision == "PATTERN_WEAKENING":
            explanation += " Pattern is weakening."
        else:
            explanation += " Pattern remains healthy."
        return PatternHealthView(
            health=health,
            decision=decision,
            time_decay_score=decay,
            broken=broken or decision == "PATTERN_BROKEN",
            failures=failures,
            reasons=reasons,
            explanation=explanation,
        )

    def _store_completed(
        self,
        ticker: str,
        points: list[SeriesPoint],
        swings: list[tuple[int, float, str]],
        vol: float,
        threshold: float,
        tournament: Optional[str],
        zone_width: float,
    ) -> None:
        if len(swings) < 4 or len(points) < 4:
            return
        confirmed = swings[:-1]
        # Only the newest swings are new information. Older setups are already stored.
        if len(confirmed) > 8:
            confirmed = confirmed[-8:]
        existing = {item.pattern_id for item in self.match_memory[ticker]}
        for index in range(len(confirmed) - 2):
            first, second, third = confirmed[index : index + 3]
            instance = self._instance_from_triple(ticker, points, first, second, third, vol, threshold, tournament)
            if instance is None or instance.pattern_id in existing:
                continue
            instance.cluster_id, instance.cluster_name = self._assign_cluster(instance)
            self.match_memory[ticker].append(instance)
            self.historical.append(instance)
            existing.add(instance.pattern_id)
            self._note_performance(instance)
        # Resistance failures and support bounces are stored once the swing is confirmed.
        supports = support_zones([price for _, price, kind in confirmed if kind == "LOW"], zone_width)
        resistances = resistance_zones([price for _, price, kind in confirmed if kind == "HIGH"], zone_width)
        self._store_zone_reactions(ticker, points, confirmed, supports, resistances, vol, tournament, existing)
        self._trim_memory(ticker)

    def _trim_memory(self, ticker: str) -> None:
        """Keep recent setups. An uncapped library makes every quote slower than login."""
        mem = self.match_memory.get(ticker)
        if mem and len(mem) > 40:
            self.match_memory[ticker] = mem[-40:]
        if len(self.historical) > 400:
            self.historical = self.historical[-400:]

    def _instance_from_triple(
        self,
        ticker: str,
        points: list[SeriesPoint],
        first: tuple[int, float, str],
        second: tuple[int, float, str],
        third: tuple[int, float, str],
        vol: float,
        threshold: float,
        tournament: Optional[str],
    ) -> Optional[PatternInstance]:
        if not (first[2] == "HIGH" and second[2] == "LOW" and third[2] == "HIGH"):
            return None
        pullback = first[1] - second[1]
        recovery = third[1] - second[1]
        if pullback < threshold * 0.8 or recovery < threshold * 0.8:
            return None
        start = points[first[0]]
        low = points[second[0]]
        end = points[third[0]]
        success = recovery >= pullback * 0.75
        flow = (start.trade_direction + low.trade_direction + end.trade_direction) / 3.0
        pattern_id = f"{ticker}:PULLBACK_RECOVERY:{int(start.ts_ms)}:{int(second[1] * 10)}"
        return PatternInstance(
            pattern_id=pattern_id,
            pattern_type="PULLBACK_RECOVERY",
            player_side="YES",
            start_timestamp=start.ts_ms,
            end_timestamp=end.ts_ms,
            start_price=first[1],
            lowest_price=second[1],
            highest_price=max(first[1], third[1]),
            recovery_price=third[1],
            duration=max(0.0, end.ts_ms - start.ts_ms),
            price_change=third[1] - first[1],
            volatility=vol,
            spread=(start.spread + low.spread + end.spread) / 3.0,
            liquidity=(
                start.depth_bid + start.depth_ask + low.depth_bid + low.depth_ask + end.depth_bid + end.depth_ask
            )
            / 3.0,
            orderbook_before=_book_slice(start),
            orderbook_during=_book_slice(low),
            orderbook_after=_book_slice(end),
            trade_flow=flow,
            result="success" if success else "failure",
            success_or_failure="success" if success else "failure",
            normalized_pullback=normalized_move(pullback, vol),
            normalized_recovery=normalized_move(recovery, vol),
            tournament=tournament,
            ticker=ticker,
        )

    def _store_zone_reactions(
        self,
        ticker: str,
        points: list[SeriesPoint],
        confirmed: list[tuple[int, float, str]],
        supports: list[dict[str, Any]],
        resistances: list[dict[str, Any]],
        vol: float,
        tournament: Optional[str],
        existing: set[str],
    ) -> None:
        for pivot_i, price, kind in confirmed:
            if kind == "LOW":
                zone = next((item for item in supports if item["low"] <= price <= item["high"]), None)
                if zone and pivot_i + 1 < len(points):
                    later = points[min(len(points) - 1, pivot_i + 3)]
                    if later.price >= price + vol:
                        self._store_simple(
                            ticker,
                            "SUPPORT_BOUNCE",
                            points,
                            pivot_i,
                            later_index=min(len(points) - 1, pivot_i + 3),
                            vol=vol,
                            tournament=tournament,
                            existing=existing,
                            success=True,
                            low=price,
                            high=later.price,
                        )
            if kind == "HIGH":
                zone = next((item for item in resistances if item["low"] <= price <= item["high"]), None)
                if not zone:
                    continue
                later_index = min(len(points) - 1, pivot_i + 3)
                later = points[later_index]
                if later.price < zone["low"]:
                    kind_name = "RESISTANCE_REJECTION"
                    success = True
                elif price > zone["high"] and later.price <= zone["high"]:
                    kind_name = "FAILED_BREAKOUT"
                    success = False
                else:
                    continue
                self._store_simple(
                    ticker,
                    kind_name,
                    points,
                    pivot_i,
                    later_index=later_index,
                    vol=vol,
                    tournament=tournament,
                    existing=existing,
                    success=success,
                    low=min(price, later.price),
                    high=max(price, later.price),
                )

    def _store_simple(
        self,
        ticker: str,
        pattern_type: str,
        points: list[SeriesPoint],
        start_index: int,
        *,
        later_index: int,
        vol: float,
        tournament: Optional[str],
        existing: set[str],
        success: bool,
        low: float,
        high: float,
    ) -> None:
        start = points[start_index]
        end = points[later_index]
        pattern_id = f"{ticker}:{pattern_type}:{int(start.ts_ms)}:{int(start.price * 10)}"
        if pattern_id in existing:
            return
        change = end.price - start.price
        instance = PatternInstance(
            pattern_id=pattern_id,
            pattern_type=pattern_type,
            player_side="YES",
            start_timestamp=start.ts_ms,
            end_timestamp=end.ts_ms,
            start_price=start.price,
            lowest_price=low,
            highest_price=high,
            recovery_price=end.price,
            duration=max(0.0, end.ts_ms - start.ts_ms),
            price_change=change,
            volatility=vol,
            spread=start.spread,
            liquidity=start.depth_bid + start.depth_ask,
            orderbook_before=_book_slice(start),
            orderbook_during=_book_slice(points[(start_index + later_index) // 2]),
            orderbook_after=_book_slice(end),
            trade_flow=end.trade_direction,
            result="success" if success else "failure",
            success_or_failure="success" if success else "failure",
            normalized_pullback=normalized_move(abs(min(0.0, change)), vol),
            normalized_recovery=normalized_move(max(0.0, change), vol),
            tournament=tournament,
            ticker=ticker,
        )
        instance.cluster_id, instance.cluster_name = self._assign_cluster(instance)
        self.match_memory[ticker].append(instance)
        self.historical.append(instance)
        existing.add(pattern_id)
        self._note_performance(instance)

    def _note_performance(self, instance: PatternInstance) -> None:
        row = self.performance[instance.pattern_type]
        row["occurrences"] += 1
        if instance.success_or_failure == "success":
            row["successful_continuations"] += 1
            row["favorable_sum"] += max(0.0, instance.recovery_price - instance.lowest_price)
        else:
            row["failed_patterns"] += 1
            row["false_positives"] += 1
        row["adverse_sum"] += max(0.0, instance.start_price - instance.lowest_price)
        row["duration_sum"] += instance.duration

    def _current_setup(
        self,
        points: list[SeriesPoint],
        swings: list[tuple[int, float, str]],
        threshold: float,
        vol: float,
        supports: list[dict[str, Any]],
        resistances: list[dict[str, Any]],
        evidence: list[str],
        tennis_type: Optional[str],
        book: BookSnapshot,
    ) -> Optional[dict[str, Any]]:
        if len(points) < 4:
            return None
        failed = self._failed_breakout(points, resistances, threshold)
        if failed:
            return self._setup_dict(points, "FAILED_BREAKOUT", points[0], points[len(points) // 2], points[-1], vol)
        breakout = self._breakout(points, resistances, threshold)
        if breakout:
            return breakout
        pullback = self._open_pullback(points, swings, threshold, vol)
        if pullback:
            return pullback
        bounce = self._zone_touch(points, supports, "SUPPORT_BOUNCE", book, bid_side=True)
        if bounce:
            return bounce
        reject = self._zone_touch(points, resistances, "RESISTANCE_REJECTION", book, bid_side=False)
        if reject:
            return reject
        trend = self._trend(points, swings, vol)
        if trend:
            return trend
        if self._momentum_exhaustion(points, book, vol):
            return self._setup_dict(points, "MOMENTUM_EXHAUSTION", points[-6], points[-3], points[-1], vol)
        if self._momentum_expansion(points, book, vol):
            return self._setup_dict(points, "MOMENTUM_EXPANSION", points[-6], points[-4], points[-1], vol)
        if supports and resistances and resistances[0]["low"] > supports[0]["high"]:
            price = points[-1].price
            if supports[0]["low"] <= price <= resistances[0]["high"]:
                return self._setup_dict(points, "RANGE_TRADING", points[0], points[len(points) // 2], points[-1], vol)
        if self._reversal(swings):
            return self._setup_dict(points, "REVERSAL", points[-6] if len(points) > 6 else points[0], points[-2], points[-1], vol)
        if tennis_type:
            return self._setup_dict(points, tennis_type, points[-4], points[-2], points[-1], vol)
        if evidence:
            kind = evidence[0]
            if kind == "ORDERBOOK_FLIP":
                kind = "ORDERBOOK_REVERSAL"
            if kind == "ASK_ABSORPTION":
                kind = "LIQUIDITY_ABSORPTION"
            return self._setup_dict(points, kind if kind in PATTERN_NAMES else "ORDERBOOK_REVERSAL", points[-4], points[-2], points[-1], vol)
        return None

    def _setup_dict(
        self,
        points: list[SeriesPoint],
        pattern_type: str,
        start: SeriesPoint,
        low: SeriesPoint,
        end: SeriesPoint,
        vol: float,
    ) -> dict[str, Any]:
        pullback = max(0.0, start.price - low.price)
        recovery = max(0.0, end.price - low.price)
        return {
            "pattern_type": pattern_type,
            "pattern_id": f"open:{pattern_type}:{int(start.ts_ms)}",
            "start": start.price,
            "low": min(low.price, end.price, start.price),
            "high": max(start.price, end.price, low.price),
            "pullback": pullback,
            "recovery": recovery,
            "start_ts": start.ts_ms,
            "low_ts": low.ts_ms,
            "norm_pullback": normalized_move(pullback, vol),
            "norm_recovery": normalized_move(recovery, vol),
            "duration": max(0.0, end.ts_ms - start.ts_ms),
            "side": "YES",
        }

    def _open_pullback(
        self,
        points: list[SeriesPoint],
        swings: list[tuple[int, float, str]],
        threshold: float,
        vol: float,
    ) -> Optional[dict[str, Any]]:
        if len(points) < 4:
            return None
        # A bounce that has not reversed yet still belongs to the prior high.
        if (
            len(swings) >= 3
            and swings[-1][2] == "HIGH"
            and swings[-2][2] == "LOW"
            and swings[-3][2] == "HIGH"
        ):
            high_i, high_p, _ = swings[-3]
            low_i, low_p, _ = swings[-2]
            pullback = high_p - low_p
            if pullback < threshold * 0.85:
                return None
            current = points[-1]
            recovery = max(0.0, current.price - low_p)
            setup = self._setup_dict(points, "PULLBACK_RECOVERY", points[high_i], points[low_i], current, vol)
            setup["pullback"] = pullback
            setup["recovery"] = recovery
            setup["norm_pullback"] = normalized_move(pullback, vol)
            setup["norm_recovery"] = normalized_move(recovery, vol)
            setup["start"] = high_p
            setup["low"] = low_p
            setup["high"] = max(high_p, current.price)
            setup["low_ts"] = points[low_i].ts_ms
            return setup
        highs = [item for item in swings if item[2] == "HIGH"]
        if not highs:
            return None
        high_i, high_p, _ = highs[-1]
        segment = points[high_i:]
        if len(segment) < 3:
            return None
        low_offset = min(range(len(segment)), key=lambda index: segment[index].price)
        if low_offset == 0:
            return None
        low = segment[low_offset]
        pullback = high_p - low.price
        if pullback < threshold * 0.85:
            return None
        current = points[-1]
        recovery = 0.0 if current.price <= low.price else max(0.0, current.price - low.price)
        setup = self._setup_dict(points, "PULLBACK_RECOVERY", points[high_i], low, current, vol)
        setup["pullback"] = pullback
        setup["recovery"] = recovery
        setup["norm_pullback"] = normalized_move(pullback, vol)
        setup["norm_recovery"] = normalized_move(recovery, vol)
        setup["start"] = high_p
        setup["low"] = low.price
        setup["high"] = max(high_p, current.price)
        return setup

    def _failed_breakout(
        self,
        points: list[SeriesPoint],
        resistances: list[dict[str, Any]],
        threshold: float,
    ) -> bool:
        if not resistances or len(points) < 5:
            return False
        recent = points[-10:]
        price = points[-1].price
        for zone in resistances:
            crossed = [point for point in recent if point.price > zone["high"]]
            if crossed and price <= zone["high"] and max(point.price for point in recent) - price >= threshold * 0.6:
                return True
        return False

    def _breakout(
        self,
        points: list[SeriesPoint],
        resistances: list[dict[str, Any]],
        threshold: float,
    ) -> Optional[dict[str, Any]]:
        if not resistances:
            return None
        price = points[-1].price
        for zone in resistances:
            if zone["touches"] < 2:
                continue
            if price > zone["high"] + min(0.4, threshold * 0.25):
                start = next((point for point in points if point.price >= zone["low"]), points[0])
                return self._setup_dict(points, "BREAKOUT", start, points[-3], points[-1], max(threshold, 1))
        return None

    def _zone_touch(
        self,
        points: list[SeriesPoint],
        zones: list[dict[str, Any]],
        pattern_type: str,
        book: BookSnapshot,
        *,
        bid_side: bool,
    ) -> Optional[dict[str, Any]]:
        if not zones:
            return None
        price = points[-1].price
        zone = next((item for item in zones if item["low"] - 0.4 <= price <= item["high"] + 0.4), None)
        if zone is None:
            return None
        if bid_side and book.imbalance <= 0:
            return None
        if not bid_side and book.imbalance >= 0:
            return None
        return self._setup_dict(points, pattern_type, points[0], points[-2], points[-1], 1)

    def _trend(
        self,
        points: list[SeriesPoint],
        swings: list[tuple[int, float, str]],
        vol: float,
    ) -> Optional[dict[str, Any]]:
        highs = [price for _, price, kind in swings if kind == "HIGH"]
        lows = [price for _, price, kind in swings if kind == "LOW"]
        if len(highs) < 2 or len(lows) < 2:
            return None
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return self._setup_dict(points, "TREND_CONTINUATION", points[0], points[len(points) // 2], points[-1], vol)
        return None

    def _momentum_exhaustion(self, points: list[SeriesPoint], book: BookSnapshot, vol: float) -> bool:
        if len(points) < 6:
            return False
        rise = points[-1].price - points[-6].price
        return rise >= vol and book.momentum <= 0 and book.imbalance < 0.05 and book.acceleration <= 0

    def _momentum_expansion(self, points: list[SeriesPoint], book: BookSnapshot, vol: float) -> bool:
        if len(points) < 6:
            return False
        rise = points[-1].price - points[-6].price
        return rise >= vol * 1.6 and book.momentum > 0 and book.acceleration > 0

    def _reversal(self, swings: list[tuple[int, float, str]]) -> bool:
        highs = [price for _, price, kind in swings if kind == "HIGH"]
        return len(highs) >= 3 and highs[-1] < highs[-2] < highs[-3]

    def _historical_like(
        self,
        ticker: str,
        pattern_type: str,
        setup: dict[str, Any],
        vol: float,
        tournament: Optional[str],
    ) -> dict[str, Any]:
        rows = []
        tournament_count = 0
        other = 0
        successes = 0
        for item in self.historical:
            if item.ticker == ticker or item.pattern_type != pattern_type:
                continue
            score = pattern_similarity_score(
                setup["norm_pullback"],
                item.normalized_pullback,
                setup["norm_recovery"],
                item.normalized_recovery,
            )
            if score < 55:
                continue
            rows.append(item)
            if tournament and item.tournament and item.tournament == tournament:
                tournament_count += 1
            else:
                other += 1
            if item.success_or_failure == "success":
                successes += 1
        return {
            "rows": rows,
            "tournament": tournament_count,
            "other": other,
            "count": len(rows),
            "successes": successes,
        }

    def _similarity(
        self,
        setup: dict[str, Any],
        same: list[PatternInstance],
        historical_rows: list[PatternInstance],
        vol: float,
    ) -> float:
        pool = same + [item for item in historical_rows if item not in same]
        if not pool:
            # Structural resemblance to a pullback/break shape, before it has repeated.
            if setup["norm_pullback"] >= 1.2 and setup["pattern_type"] == "PULLBACK_RECOVERY":
                return 62.0
            if setup["pattern_type"] in ("BREAKOUT", "SUPPORT_BOUNCE", "FAILED_BREAKOUT"):
                return 58.0
            return 40.0
        scores = []
        for item in pool:
            duration_ratio = 1.0
            if item.duration > 0 and setup["duration"] > 0:
                duration_ratio = setup["duration"] / item.duration
            scores.append(
                pattern_similarity_score(
                    setup["norm_pullback"],
                    item.normalized_pullback or normalized_move(max(0.0, item.start_price - item.lowest_price), vol),
                    setup["norm_recovery"],
                    item.normalized_recovery or normalized_move(max(0.0, item.recovery_price - item.lowest_price), vol),
                    duration_ratio,
                )
            )
        scores.sort(reverse=True)
        top = scores[:3]
        # Same-match examples count more than distant history.
        return _clamp(sum(top) / len(top))

    def _typical(
        self,
        same: list[PatternInstance],
        historical_rows: list[PatternInstance],
        field_name: str,
        fallback: float,
    ) -> float:
        values: list[float] = []
        for item in same:
            values.extend(self._field_values(item, field_name))
        if len(values) < 2:
            for item in historical_rows:
                if item in same:
                    continue
                values.extend(self._field_values(item, field_name))
        if not values:
            return fallback
        return sum(values) / len(values)

    def _field_values(self, item: PatternInstance, field_name: str) -> list[float]:
        if field_name == "recovery":
            return [max(0.0, item.recovery_price - item.lowest_price)] if item.success_or_failure == "success" else []
        if field_name == "pullback":
            return [max(0.0, item.start_price - item.lowest_price)]
        if field_name == "duration":
            return [item.duration] if item.duration > 0 else []
        return []

    def _component_scores(
        self,
        book: BookSnapshot,
        *,
        similarity: float,
        label: str,
        success_rate: Optional[float],
        low_sample: bool,
        momentum: float,
        progress: float,
        expected: float,
    ) -> dict[str, float]:
        repetition = {"RECURRING": 92.0, "POTENTIAL": 70.0, "OBSERVED ONLY": 28.0, "NONE": 8.0}[label]
        if success_rate is None:
            historical = 42.0
        else:
            historical = success_rate * 100.0
            if low_sample:
                historical = min(historical, 58.0)
        book_score = _clamp(58.0 + book.imbalance * 80.0)
        momentum_score = _clamp(55.0 + momentum * 8.0)
        flow = _clamp(52.0 + book.trade_velocity * 4.0 + max(0.0, momentum) * 3.0)
        liquidity = {"HIGH": 100, "GOOD": 84, "MEDIUM": 64, "LOW": 28, "VERY_LOW": 0}.get(book.liquidity_quality, 40)
        spread = {"EXCELLENT": 100, "GOOD": 86, "ACCEPTABLE": 62, "POOR": 24, "VERY_POOR": 0}.get(
            book.spread_quality, 40
        )
        reversal = _clamp(84.0 - max(0.0, -momentum) * 10.0 - max(0.0, -book.imbalance) * 30.0)
        remaining = max(0.0, 100.0 - progress)
        price = _clamp(35.0 + remaining * 0.55 + max(0.0, book.adjusted_edge) * 400.0)
        return {
            "similarity": similarity,
            "repetition": repetition,
            "historical": historical,
            "book": book_score,
            "momentum": momentum_score,
            "flow": flow,
            "liquidity": liquidity,
            "spread": spread,
            "reversal": reversal,
            "price": price,
        }

    def _weighted_confidence(self, parts: dict[str, float]) -> float:
        settings = self.settings
        return _clamp(
            settings.weight_pattern_similarity * parts["similarity"]
            + settings.weight_pattern_repetition * parts["repetition"]
            + settings.weight_pattern_historical * parts["historical"]
            + settings.weight_pattern_book * parts["book"]
            + settings.weight_pattern_momentum * parts["momentum"]
            + settings.weight_pattern_flow * parts["flow"]
            + settings.weight_pattern_liquidity * parts["liquidity"]
            + settings.weight_pattern_spread * parts["spread"]
            + settings.weight_pattern_reversal * parts["reversal"]
        )

    def _weighted_entry(self, parts: dict[str, float], confidence: float) -> float:
        settings = self.settings
        return _clamp(
            settings.weight_pattern_entry_confidence * confidence
            + settings.weight_pattern_entry_history * parts["historical"]
            + settings.weight_pattern_entry_price * parts["price"]
            + settings.weight_pattern_entry_book * parts["book"]
            + settings.weight_pattern_entry_momentum * parts["momentum"]
            + settings.weight_pattern_entry_flow * parts["flow"]
            + settings.weight_pattern_entry_liquidity * parts["liquidity"]
            + settings.weight_pattern_entry_spread * parts["spread"]
            + settings.weight_pattern_entry_reversal * parts["reversal"]
        )

    def _confirmation_signals(
        self,
        setup: dict[str, Any],
        book: BookSnapshot,
        momentum: float,
        points: list[SeriesPoint],
    ) -> list[str]:
        signals: list[str] = []
        if setup["recovery"] > 0.2 and points[-1].price >= setup["low"] + 0.2:
            signals.append("Price begins recovering")
        micro = book.microprice or (points[-1].microprice if points else 0)
        if micro and micro >= book.price - 0.05:
            signals.append("Microprice improves")
        if book.imbalance > 0.05 or (book.depth_bid > book.depth_ask and book.depth_bid > 0):
            signals.append("Bid depth increases")
        if book.depth_ask < book.depth_bid:
            signals.append("Ask depth decreases")
        if momentum > 0:
            signals.append("Momentum turns positive")
        if book.liquidity_quality in ("HIGH", "GOOD", "MEDIUM"):
            signals.append("Liquidity remains healthy")
        if book.spread_quality in ("EXCELLENT", "GOOD", "ACCEPTABLE"):
            signals.append("Spread remains acceptable")
        return signals

    def _decide(
        self,
        *,
        pattern_type: str,
        label: str,
        stage: str,
        progress: float,
        confidence: float,
        entry_score: float,
        confirmation_count: int,
        confirming_signals: int,
        book: BookSnapshot,
        evidence: list[str],
    ) -> tuple[str, bool, list[str], list[str]]:
        settings = self.settings
        blockers: list[str] = []
        reasons: list[str] = []
        if pattern_type == "FAILED_BREAKOUT":
            return "FAILED_BREAKOUT", False, ["Price fell back through resistance"], ["Failed breakout. No entry."]
        if pattern_type in ORDERBOOK_ONLY:
            return (
                "SEARCHING",
                False,
                ["Order-book pattern alone cannot trigger entry"],
                evidence or ["Order-book evidence only"],
            )
        if stage in ("LATE", "COMPLETED") or progress >= settings.pattern_late_stage_percent:
            return (
                "PATTERN_ALREADY_ADVANCED",
                False,
                ["Pattern already advanced. Do not chase."],
                ["Do not chase — move already advanced."],
            )
        if label not in ("POTENTIAL", "RECURRING"):
            reasons.append("This pattern has been seen. Repetition is not required before the alert.")
        else:
            reasons.append("I have seen this market behave this way before.")
        reasons.append("The same pattern appears to be forming.")
        market_ok = (
            book.fresh
            and not book.extreme
            and book.liquidity_quality not in ("LOW", "VERY_LOW")
            and book.spread_quality not in ("POOR", "VERY_POOR")
        )
        if not book.fresh:
            blockers.append("Market data is stale")
        if book.extreme:
            blockers.append("Volatility is extreme for this match")
        if book.liquidity_quality in ("LOW", "VERY_LOW"):
            blockers.append("Liquidity is not acceptable")
        if book.spread_quality in ("POOR", "VERY_POOR"):
            blockers.append("Spread is not acceptable")
        # A pattern that exists and is still early is the alert. Do not wait for
        # three confirmations or three historical repeats.
        if market_ok and stage in ("EARLY", "DEVELOPING"):
            reasons.append("Pattern exists and is about to begin. Bet behind the player or the market.")
            if (
                entry_score >= settings.strong_pattern_entry_score
                and confidence >= settings.strong_pattern_confidence
                and book.liquidity_quality in ("HIGH", "GOOD")
                and book.spread_quality in ("EXCELLENT", "GOOD")
            ):
                return "STRONG_PATTERN_SIGNAL", True, [], reasons
            return "PATTERN_ENTRY_SIGNAL", True, [], reasons
        if not market_ok:
            reasons.append("Waiting for the book, spread, and liquidity to agree.")
        else:
            reasons.append("Pattern has already moved. Do not chase a late stage.")
        return "PATTERN_DEVELOPING", False, blockers, reasons

    def _explain(self, **kwargs: Any) -> str:
        decision = kwargs["decision"]
        name = pattern_name(kwargs["pattern_type"])
        rate = kwargs["success_rate"]
        rate_text = "n/a" if rate is None else f"{rate * 100:.0f}% observed"
        sample = " LOW SAMPLE SIZE." if kwargs["low_sample"] else ""
        base = (
            f"{name}. Similarity {kwargs['similarity']:.0f}%. "
            f"Pattern confidence {kwargs['confidence']:.0f}. Entry score {kwargs['entry_score']:.0f}. "
            f"Stage {kwargs['stage']}. Progress {kwargs['progress']:.0f}%. "
            f"Repetition {kwargs['label']}. Previous occurrences {kwargs['occurrences']}. "
            f"Successful continuations {kwargs['successes']}. Observed success {rate_text}.{sample} "
            f"Confirmation {kwargs['confirmation_count']}/{kwargs['needed']}. "
            f"Possible entry zone {kwargs['zone_low']:.0f}¢–{kwargs['zone_high']:.0f}¢. "
            f"Maximum entry {kwargs['maximum']:.0f}¢. Current {kwargs['price']:.0f}¢. "
            "Observed success is not a guaranteed future probability."
        )
        if decision in ("PATTERN_ENTRY_SIGNAL", "STRONG_PATTERN_SIGNAL"):
            headline = kwargs.get("bet_headline") or "Bet behind the market"
            detail = kwargs.get("bet_detail") or "You place the bet manually on Kalshi."
            return f"{headline}. {detail} " + base
        if decision == "PATTERN_WATCH":
            return "PATTERN WATCH. " + base + " Do not enter yet."
        if decision == "PATTERN_ALREADY_ADVANCED":
            return "PATTERN ALREADY ADVANCED. DO NOT CHASE. " + base
        if decision == "FAILED_BREAKOUT":
            return "FAILED BREAKOUT. NO ENTRY. Price crossed resistance and fell back."
        if decision == "PATTERN_DEVELOPING":
            return "PATTERN DEVELOPING. " + base + " Waiting for confirmation."
        return base

    def _cluster_name(self, pattern_type: str, setup: dict[str, Any], vol: float) -> str:
        key = (
            pattern_type,
            round(setup["norm_pullback"] * 2) / 2,
            round(setup["norm_recovery"] * 2) / 2,
        )
        if key not in self._clusters:
            self._clusters[key] = self._next_cluster
            self._next_cluster += 1
        cluster_id = self._clusters[key]
        pull = setup["pullback"]
        rec = setup["recovery"]
        return (
            f"Pattern Cluster {cluster_id}: {pattern_name(pattern_type)}. "
            f"Typical behavior near {pull:.0f}¢ pullback and {rec:.0f}¢ continuation "
            f"against {vol:.1f}¢ volatility."
        )

    def _assign_cluster(self, instance: PatternInstance) -> tuple[int, str]:
        key = (
            instance.pattern_type,
            round(instance.normalized_pullback * 2) / 2,
            round(instance.normalized_recovery * 2) / 2,
        )
        if key not in self._clusters:
            self._clusters[key] = self._next_cluster
            self._next_cluster += 1
        cluster_id = self._clusters[key]
        name = f"Pattern Cluster {cluster_id}: {pattern_name(instance.pattern_type)}"
        return cluster_id, name

    def _tennis_pattern(self, ticker: str, tennis: Any) -> Optional[str]:
        if tennis is None or not getattr(tennis, "available", False):
            return None
        server = getattr(tennis, "server", None)
        previous = self._last_server.get(ticker)
        self._last_server[ticker] = server
        breaks = (getattr(tennis, "break_points_a", None), getattr(tennis, "break_points_b", None))
        previous_breaks = self._last_break_points.get(ticker)
        self._last_break_points[ticker] = breaks
        if previous_breaks and breaks != previous_breaks and any(item is not None for item in breaks):
            return "BREAK_POINT_REACTION"
        if previous and server and previous != server:
            return "SERVE_CHANGE"
        return None


def series_from_ticks(ticks: list[Any]) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    previous: Optional[float] = None
    for tick in ticks:
        price = tick.last_trade if getattr(tick, "last_trade", None) else tick.mid
        direction = 0.0
        if previous is not None:
            direction = 1.0 if price > previous else -1.0 if price < previous else 0.0
        points.append(
            SeriesPoint(
                ts_ms=tick.ts_ms,
                price=price,
                bid=tick.yes_bid,
                ask=tick.yes_ask,
                spread=tick.spread,
                microprice=tick.microprice,
                imbalance=tick.imbalance,
                depth_bid=tick.depth_yes,
                depth_ask=tick.depth_no,
                volume=getattr(tick, "volume", 0.0) or 0.0,
                trade_direction=direction,
            )
        )
        previous = price
    return points


def book_from_market(market: Any, settings: Settings, adjusted_edge: float = 0.0) -> BookSnapshot:
    from app.services.signals.mispricing import liquidity_quality, spread_quality
    window = market.windows.get(5000) if getattr(market, "windows", None) else None
    momentum = (window.momentum if window else 0.0) or 0.0
    acceleration = (window.price_acceleration if window else 0.0) or 0.0
    velocity = (window.trade_velocity if window else 0.0) or 0.0
    price = market.last_trade or market.mid
    return BookSnapshot(
        price=price,
        spread=market.spread,
        imbalance=market.imbalance,
        depth_bid=market.depth_yes,
        depth_ask=market.depth_no,
        momentum=momentum,
        acceleration=acceleration,
        trade_velocity=velocity,
        liquidity_quality=liquidity_quality(market, settings),
        spread_quality=spread_quality(market, settings),
        fresh=market.data_age_ms <= settings.max_data_age_ms,
        adjusted_edge=adjusted_edge,
        microprice=market.microprice,
        extreme=False,
    )


def make_point(ts_ms: float, price: float, **kwargs: Any) -> SeriesPoint:
    bid = kwargs.get("bid", price - 0.5)
    ask = kwargs.get("ask", price + 0.5)
    return SeriesPoint(
        ts_ms=ts_ms,
        price=price,
        bid=bid,
        ask=ask,
        spread=kwargs.get("spread", max(0.0, ask - bid)),
        microprice=kwargs.get("microprice", price + 0.1),
        imbalance=kwargs.get("imbalance", 0.2),
        depth_bid=kwargs.get("depth_bid", 900),
        depth_ask=kwargs.get("depth_ask", 500),
        volume=kwargs.get("volume", 10),
        trade_direction=kwargs.get("trade_direction", 0),
    )


def build_pattern_rows(instance: PatternInstance) -> dict[str, dict[str, Any]]:
    """Row shapes for the pattern tables. Live decisions stay in memory."""
    return {
        "pattern_definitions": {
            "pattern_type": instance.pattern_type,
            "name": pattern_name(instance.pattern_type),
            "family": instance.pattern_type,
        },
        "pattern_instances": instance.as_dict(),
        "pattern_features": {
            "pattern_id": instance.pattern_id,
            "normalized_pullback": instance.normalized_pullback,
            "normalized_recovery": instance.normalized_recovery,
            "volatility": instance.volatility,
            "spread": instance.spread,
            "liquidity": instance.liquidity,
            "trade_flow": instance.trade_flow,
        },
        "pattern_results": {
            "pattern_id": instance.pattern_id,
            "result": instance.result,
            "success_or_failure": instance.success_or_failure,
            "price_change": instance.price_change,
            "duration": instance.duration,
        },
        "pattern_similarity": {
            "pattern_id": instance.pattern_id,
            "cluster_id": instance.cluster_id,
            "cluster_name": instance.cluster_name,
        },
        "pattern_performance": {
            "pattern_type": instance.pattern_type,
            "success_or_failure": instance.success_or_failure,
        },
    }


def performance_report(engine: PatternEngine) -> dict[str, Any]:
    by_type = {}
    for pattern_type, row in engine.performance.items():
        occurrences = int(row["occurrences"])
        signals = int(row["entry_signals"])
        by_type[pattern_type] = {
            "pattern_name": pattern_name(pattern_type),
            "occurrences": occurrences,
            "entry_signals": signals,
            "successful_continuations": int(row["successful_continuations"]),
            "failed_continuations": int(row["failed_patterns"]),
            "success_rate": (
                None
                if occurrences == 0
                else round(row["successful_continuations"] / occurrences, 3)
            ),
            "average_favorable_move": round(row["favorable_sum"] / max(row["successful_continuations"], 1), 2),
            "average_adverse_move": round(row["adverse_sum"] / max(occurrences, 1), 2),
            "average_pattern_duration_ms": round(row["duration_sum"] / max(occurrences, 1), 1),
            "average_entry_timing_progress": round(row["entry_timing_sum"] / max(signals, 1), 1) if signals else None,
            "late_entry_frequency": round(row["late_entries"] / signals, 3) if signals else 0,
            "false_positives": int(row["false_positives"]),
            "signal_expiry_frequency": round(row["expiries"] / signals, 3) if signals else 0,
            "average_similarity": round(row["similarity_sum"] / signals, 1) if signals else None,
            "average_confidence": round(row["confidence_sum"] / signals, 1) if signals else None,
            "average_entry_score": round(row["entry_score_sum"] / signals, 1) if signals else None,
            "low_sample_size": occurrences < engine.settings.min_pattern_sample,
        }
    return {
        "by_pattern": by_type,
        "auto_select_enabled": False,
        "warning": (
            "Pattern stats are descriptive. Small samples are marked LOW SAMPLE SIZE. "
            "This report does not pick a configuration and does not promise future profit."
        ),
        "order_placement_enabled": False,
    }


def illustrative_pattern_backtest(settings: Settings | None = None) -> dict[str, Any]:
    """Replay two pullback sequences so the report has a concrete, non-optimized example."""
    settings = settings or Settings()
    engine = PatternEngine(settings)
    prices = [52, 51, 50, 49, 50, 52, 55, 56, 55, 54, 53, 54, 56, 60]
    points = [make_point(i * 1000, price, imbalance=0.25 if price >= 53 else -0.05) for i, price in enumerate(prices)]
    book = BookSnapshot(
        price=points[-1].price,
        spread=1,
        imbalance=0.3,
        depth_bid=1200,
        depth_ask=700,
        momentum=1.2,
        liquidity_quality="GOOD",
        spread_quality="EXCELLENT",
        fresh=True,
        adjusted_edge=0.03,
        microprice=points[-1].price + 0.2,
    )
    view = engine.assess(ticker="ILLUSTRATIVE", points=points, book=book, confirmation_count=0, player_a="Player A")
    report = performance_report(engine)
    report["illustrative_decision"] = view.decision
    report["illustrative_pattern"] = view.pattern_name
    report["illustrative_occurrences"] = view.occurrences
    return report


def seed_pullback(engine: PatternEngine, ticker: str, start_ms: float, start: float, low: float, recovery: float, vol: float = 1.0) -> PatternInstance:
    """Test helper: remember one successful pullback without replaying every tick."""
    instance = PatternInstance(
        pattern_id=f"{ticker}:PULLBACK_RECOVERY:{int(start_ms)}:{uuid4().hex[:6]}",
        pattern_type="PULLBACK_RECOVERY",
        player_side="YES",
        start_timestamp=start_ms,
        end_timestamp=start_ms + 18_000,
        start_price=start,
        lowest_price=low,
        highest_price=recovery,
        recovery_price=recovery,
        duration=18_000,
        price_change=recovery - start,
        volatility=vol,
        spread=1,
        liquidity=1500,
        orderbook_before={"imbalance": 0.0, "depth_bid": 800, "depth_ask": 800},
        orderbook_during={"imbalance": -0.1, "depth_bid": 700, "depth_ask": 900},
        orderbook_after={"imbalance": 0.35, "depth_bid": 1400, "depth_ask": 500},
        trade_flow=0.4,
        result="success",
        success_or_failure="success",
        normalized_pullback=normalized_move(start - low, vol),
        normalized_recovery=normalized_move(recovery - low, vol),
        tournament="Test",
        ticker=ticker,
    )
    instance.cluster_id, instance.cluster_name = engine._assign_cluster(instance)
    engine.match_memory[ticker].append(instance)
    engine._note_performance(instance)
    return instance
