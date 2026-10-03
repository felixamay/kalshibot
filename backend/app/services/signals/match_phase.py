"""Match-specific study baseline, entry timing, and position protection.

The first five minutes learn how THIS match trades. Later decisions compare
new prices, books, and momentum with that baseline. Nothing here places an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.services.market.analyzer import MarketState
from app.services.signals.mispricing import (
    MarketRead,
    liquidity_quality,
    spread_quality,
)
from app.services.tennis.probability import ProbabilityResult

_MOVE_WINDOWS_MS = (250, 500, 1000, 2000, 5000, 10000, 30000)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


@dataclass
class MatchBaseline:
    normal_volatility: float
    normal_spread: float
    normal_liquidity: float
    normal_trade_velocity: float
    normal_reversal_frequency: float
    normal_price_movement: float
    normal_orderbook_imbalance: float
    dominant_market_direction: str
    player_a_market_strength: float
    player_b_market_strength: float
    sample_count: int

    def as_dict(self) -> dict:
        return {
            "normal_volatility": round(self.normal_volatility, 3),
            "normal_spread": round(self.normal_spread, 3),
            "normal_liquidity": round(self.normal_liquidity, 1),
            "normal_trade_velocity": round(self.normal_trade_velocity, 3),
            "normal_reversal_frequency": round(self.normal_reversal_frequency, 3),
            "normal_price_movement": round(self.normal_price_movement, 3),
            "normal_orderbook_imbalance": round(self.normal_orderbook_imbalance, 3),
            "dominant_market_direction": self.dominant_market_direction,
            "player_a_market_strength": round(self.player_a_market_strength, 3),
            "player_b_market_strength": round(self.player_b_market_strength, 3),
            "sample_count": self.sample_count,
        }


@dataclass
class EntryView:
    entry_score: float
    decision: str
    player: str
    direction: str
    current_price: float
    maximum_entry_price: float
    confirmation_count: int
    confirmation_needed: int
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    explanation: str = ""
    qualifying: bool = False

    def as_dict(self) -> dict:
        return {
            "entry_score": round(self.entry_score, 1),
            "decision": self.decision,
            "player": self.player,
            "direction": self.direction,
            "current_price": round(self.current_price, 2),
            "maximum_entry_price": round(self.maximum_entry_price, 2),
            "confirmation_count": self.confirmation_count,
            "confirmation_needed": self.confirmation_needed,
            "reasons": self.reasons,
            "blockers": self.blockers,
            "explanation": self.explanation,
            "qualifying": self.qualifying,
        }


@dataclass
class TrackedPosition:
    position_id: str
    player: str
    direction: str
    entry_price: float
    amount: float
    entered_at_ms: float
    entry_momentum: float
    entry_imbalance: float
    entry_liquidity: float
    entry_spread: float
    entry_model_probability: float
    entry_signal_score: float
    entry_trade_flow: float
    peak_price: float
    current_price: float = 0.0
    drawdown_from_peak: float = 0.0


@dataclass
class SlipView:
    slip_score: float
    health: float
    decision: str
    emergency: bool
    confirmation_count: int
    confirmation_needed: int
    normalized_move: float
    reasons: list[str] = field(default_factory=list)
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "slip_score": round(self.slip_score, 1),
            "health": round(self.health, 1),
            "decision": self.decision,
            "emergency": self.emergency,
            "confirmation_count": self.confirmation_count,
            "confirmation_needed": self.confirmation_needed,
            "normalized_move": round(self.normalized_move, 2),
            "reasons": self.reasons,
            "explanation": self.explanation,
        }


def build_baseline(ticks: list, settings: Settings) -> MatchBaseline:
    """Summarize the study window. A thin sample still returns a usable floor."""
    floor = settings.baseline_volatility_floor
    if len(ticks) < 2:
        mid = ticks[0].mid if ticks else 50.0
        spread = ticks[0].spread if ticks else 2.0
        depth = (ticks[0].depth_yes + ticks[0].depth_no) if ticks else 0.0
        imb = ticks[0].imbalance if ticks else 0.0
        return _baseline_from_parts(
            volatility=floor,
            spread=spread or 2.0,
            liquidity=depth,
            velocity=0.0,
            reversals=0.0,
            movement=floor,
            imbalance=imb,
            first_mid=mid,
            last_mid=mid,
            sample_count=len(ticks),
        )

    mids = [t.mid for t in ticks]
    returns = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    volatility = max(floor, var ** 0.5)
    movement = max(floor, sum(abs(r) for r in returns) / len(returns))
    reversals = 0
    for i in range(1, len(returns)):
        if returns[i] == 0 or returns[i - 1] == 0:
            continue
        if (returns[i] > 0) != (returns[i - 1] > 0):
            reversals += 1
    reversal_freq = reversals / max(len(returns) - 1, 1)
    span_s = max((ticks[-1].ts_ms - ticks[0].ts_ms) / 1000.0, 1.0)
    velocity = len(ticks) / span_s
    spread = sum(t.spread for t in ticks) / len(ticks)
    liquidity = sum(t.depth_yes + t.depth_no for t in ticks) / len(ticks)
    imbalance = sum(t.imbalance for t in ticks) / len(ticks)
    return _baseline_from_parts(
        volatility=volatility,
        spread=spread,
        liquidity=liquidity,
        velocity=velocity,
        reversals=reversal_freq,
        movement=movement,
        imbalance=imbalance,
        first_mid=mids[0],
        last_mid=mids[-1],
        sample_count=len(ticks),
    )


def _baseline_from_parts(
    *,
    volatility: float,
    spread: float,
    liquidity: float,
    velocity: float,
    reversals: float,
    movement: float,
    imbalance: float,
    first_mid: float,
    last_mid: float,
    sample_count: int,
) -> MatchBaseline:
    drift = last_mid - first_mid
    if drift > movement:
        direction = "YES"
    elif drift < -movement:
        direction = "NO"
    else:
        direction = "NEUTRAL"
    strength = _clamp(last_mid, 1.0, 99.0) / 100.0
    return MatchBaseline(
        normal_volatility=volatility,
        normal_spread=max(spread, 0.5),
        normal_liquidity=liquidity,
        normal_trade_velocity=velocity,
        normal_reversal_frequency=reversals,
        normal_price_movement=movement,
        normal_orderbook_imbalance=imbalance,
        dominant_market_direction=direction,
        player_a_market_strength=strength,
        player_b_market_strength=1.0 - strength,
        sample_count=sample_count,
    )


def assess_entry(
    settings: Settings,
    market: MarketState,
    prob: ProbabilityResult,
    read: MarketRead,
    baseline: MatchBaseline,
    *,
    confirmation_count: int,
    best_seen_ask: float | None,
    player_a: str,
    player_b: str,
) -> EntryView:
    """Score whether this moment is a favorable entry. Price level alone does not decide."""
    side_yes = _side_score(
        settings, market, prob, read, baseline, direction="YES", player=player_a
    )
    no_prob = _flip_prob(prob)
    side_no = _side_score(
        settings, market, no_prob, read, baseline, direction="NO", player=player_b
    )
    # Prefer the side with the better timing score. A favorite with no price
    # opportunity loses to a cheaper side the model still likes.
    chosen = side_yes if side_yes["score"] >= side_no["score"] else side_no
    score = chosen["score"]
    direction = chosen["direction"]
    player = chosen["player"]
    ask = chosen["ask"]
    max_entry = chosen["max_entry"]
    adjusted = read.uncertainty_adjusted_edge if direction == "YES" else -read.uncertainty_adjusted_edge
    # NO side edge is not the YES read flipped inside MarketRead. Recompute from the flipped prob.
    if direction == "NO":
        adjusted = no_prob.estimated_net_edge - read.uncertainty_penalty

    liquidity = liquidity_quality(market, settings)
    spread = spread_quality(market, settings)
    fresh = market.data_age_ms <= settings.max_data_age_ms
    vol_unit = max(baseline.normal_volatility, settings.baseline_volatility_floor)
    mom = _directional_momentum(market, 5000, direction)
    strong_reversal = -mom > vol_unit * 1.5
    w5 = market.windows.get(5000)
    extreme = ((w5.volatility if w5 else 0.0) or 0.0) > max(
        settings.extreme_volatility, vol_unit * 3.5
    )
    chased = (
        best_seen_ask is not None
        and ask > best_seen_ask + settings.max_entry_slippage_cents
        and score < settings.entry_signal_score
    )
    acceptable_liq = liquidity in ("MEDIUM", "GOOD", "HIGH")
    acceptable_spread = spread in ("EXCELLENT", "GOOD", "ACCEPTABLE")
    priced_ok = adjusted >= 0
    inside_window = ask <= max_entry
    qualifying = (
        score >= settings.entry_signal_score
        and acceptable_liq
        and acceptable_spread
        and fresh
        and not strong_reversal
        and not extreme
        and priced_ok
        and inside_window
        and not chased
        and liquidity != "VERY_LOW"
        and spread != "VERY_POOR"
    )
    strong = (
        qualifying
        and score >= settings.strong_entry_score
        and liquidity in ("HIGH", "GOOD")
        and spread in ("EXCELLENT", "GOOD")
        and read.model_uncertainty != "HIGH"
        and mom >= 0
    )
    needed = settings.entry_confirmation_count

    reasons = list(chosen["reasons"])
    blockers: list[str] = []
    if not fresh:
        blockers.append("Quote is stale")
    if liquidity in ("LOW", "VERY_LOW"):
        blockers.append(f"Liquidity is {liquidity.lower().replace('_', ' ')}")
    if spread in ("POOR", "VERY_POOR"):
        blockers.append(f"Spread is {spread.lower().replace('_', ' ')}")
    if strong_reversal:
        blockers.append("A reversal is running against this side")
    if extreme:
        blockers.append("Volatility is extreme versus this match")
    if not priced_ok:
        blockers.append("Model does not beat the executable price after costs")
    if not inside_window:
        blockers.append(f"Price {ask:.0f}¢ is above the maximum entry {max_entry:.0f}¢")
    if chased:
        blockers.append("Price ran and the entry window closed")
    if confirmation_count < needed and qualifying:
        blockers.append(f"Confirmation {confirmation_count} of {needed}")

    if chased:
        decision = "OPPORTUNITY_PASSED"
    elif (not inside_window) and score >= settings.watch_entry_score and priced_ok:
        decision = "DO_NOT_ENTER"
    elif extreme or liquidity == "VERY_LOW" or spread == "VERY_POOR" or not fresh:
        decision = "DO_NOT_ENTER"
    elif strong and confirmation_count >= needed:
        decision = "STRONG_ENTRY_SIGNAL"
    elif qualifying and confirmation_count >= needed:
        decision = "ENTRY_SIGNAL"
    elif score >= settings.watch_entry_score and not extreme and liquidity != "VERY_LOW":
        decision = "ENTRY_DEVELOPING"
    elif not priced_ok or strong_reversal:
        decision = "DO_NOT_ENTER"
    else:
        decision = "SEARCHING_FOR_ENTRY"

    explanation = (
        f"{player} — {direction}. Entry score {score:.0f}/100. "
        f"Current {ask:.0f}¢. Maximum entry {max_entry:.0f}¢. "
        f"Confirmation {confirmation_count}/{needed}. "
        f"Match baseline volatility {baseline.normal_volatility:.1f}¢."
    )
    if decision == "ENTRY_DEVELOPING":
        explanation += " Watching for confirmation. This is not an instruction to enter."
    elif decision == "OPPORTUNITY_PASSED":
        explanation += " OPPORTUNITY PASSED. DO NOT CHASE."
    elif decision == "DO_NOT_ENTER" and not inside_window:
        explanation += " DO NOT ENTER. PRICE MOVED BEYOND ENTRY WINDOW."
    elif decision in ("ENTRY_SIGNAL", "STRONG_ENTRY_SIGNAL"):
        explanation += " Favorable moment versus this match's own baseline. Not a guarantee."
    if blockers and decision not in ("ENTRY_SIGNAL", "STRONG_ENTRY_SIGNAL"):
        explanation += " " + blockers[0] + "."

    return EntryView(
        entry_score=score,
        decision=decision,
        player=player,
        direction=direction,
        current_price=ask,
        maximum_entry_price=max_entry,
        confirmation_count=confirmation_count,
        confirmation_needed=needed,
        reasons=reasons,
        blockers=blockers,
        explanation=explanation,
        qualifying=qualifying or strong,
    )


def assess_slip(
    settings: Settings,
    market: MarketState,
    position: TrackedPosition,
    baseline: MatchBaseline,
    *,
    model_probability: float,
    confirmation_count: int,
    emergency_forced: bool = False,
) -> SlipView:
    """Deterioration versus this match and versus the post-entry snapshot."""
    vol = max(baseline.normal_volatility, settings.baseline_volatility_floor)
    direction = position.direction
    adverse_norms: list[float] = []
    for window in _MOVE_WINDOWS_MS:
        stats = market.windows.get(window)
        mom = (stats.momentum if stats else 0.0) or 0.0
        adverse = -mom if direction == "YES" else mom
        adverse_norms.append(max(0.0, adverse / vol))
    drawdown_norm = max(0.0, position.drawdown_from_peak / vol)
    normalized = max(drawdown_norm, max(adverse_norms) if adverse_norms else 0.0)
    short_norm = max(adverse_norms[2] if len(adverse_norms) > 2 else 0.0, adverse_norms[3] if len(adverse_norms) > 3 else 0.0)

    profit = position.peak_price - position.entry_price
    profit_vols = profit / vol
    if profit_vols >= settings.profit_protect_strong_vols:
        sensitivity = settings.profit_protect_strong_multiplier
    elif profit_vols >= settings.profit_protect_moderate_vols:
        sensitivity = settings.profit_protect_moderate_multiplier
    else:
        sensitivity = 1.0

    price_pts = _clamp(normalized * sensitivity / 3.0 * 100.0)
    imb = market.imbalance if direction == "YES" else -market.imbalance
    entry_imb = position.entry_imbalance if direction == "YES" else -position.entry_imbalance
    book_pts = _clamp(40.0 + (entry_imb - imb) * 90.0)
    mom5 = _directional_momentum(market, 5000, direction)
    mom_pts = _clamp(40.0 - mom5 * 12.0)
    accel = _directional_momentum(market, 2000, direction) - _directional_momentum(market, 10000, direction)
    # Negative acceleration on the held side is sell pressure building.
    flow_pts = _clamp(35.0 - accel * 10.0)
    depth = market.depth_yes + market.depth_no
    if position.entry_liquidity > 1:
        liq_pts = _clamp((1.0 - depth / position.entry_liquidity) * 120.0)
    else:
        liq_pts = 30.0 if depth <= 0 else 10.0
    model_drop = position.entry_model_probability - model_probability
    model_pts = _clamp(model_drop / 0.08 * 100.0)
    spread_ratio = market.spread / max(baseline.normal_spread, 0.5)
    spread_pts = _clamp((spread_ratio - 1.0) * 70.0)

    slip = (
        settings.weight_slip_price * price_pts
        + settings.weight_slip_book * book_pts
        + settings.weight_slip_momentum * mom_pts
        + settings.weight_slip_flow * flow_pts
        + settings.weight_slip_liquidity * liq_pts
        + settings.weight_slip_model * model_pts
        + settings.weight_slip_spread * spread_pts
    )
    slip = _clamp(slip)
    health = _clamp(100.0 - slip)

    status_u = (market.status or "").upper()
    fresh = market.data_age_ms <= settings.max_data_age_ms
    emergency = emergency_forced or short_norm >= settings.emergency_move_vols or not fresh or status_u == "SUSPENDED"
    if position.entry_liquidity > 20 and depth < position.entry_liquidity * 0.35:
        emergency = True
    if spread_quality(market, settings) == "VERY_POOR" and market.spread > baseline.normal_spread * 3:
        emergency = True

    reasons: list[str] = []
    if price_pts >= 55:
        reasons.append("Price is falling farther than this match usually moves")
    if book_pts >= 55:
        reasons.append("Order book reversed against the position")
    if mom_pts >= 55:
        reasons.append("Momentum reversed")
    if flow_pts >= 55:
        reasons.append("Sell pressure increased")
    if liq_pts >= 55:
        reasons.append("Bid liquidity is disappearing")
    if model_pts >= 55:
        reasons.append("Model probability deteriorated")
    if spread_pts >= 55:
        reasons.append("Spread widened versus this match")
    if not reasons and slip < settings.watch_slip_score:
        reasons.append("Momentum healthy")
        reasons.append("Order book supportive")

    needed = settings.exit_confirmation_count
    if emergency:
        decision = "STOP_EXIT_SIGNAL"
    elif slip >= settings.stop_exit_score and confirmation_count >= needed:
        decision = "STOP_EXIT_SIGNAL"
    elif slip >= settings.slipping_score:
        decision = "SLIPPING"
    elif slip >= settings.watch_slip_score:
        decision = "WATCH_CLOSELY"
    else:
        decision = "HOLD"

    health_word = {
        "HOLD": "HEALTHY",
        "WATCH_CLOSELY": "WATCH",
        "SLIPPING": "SLIPPING",
        "STOP_EXIT_SIGNAL": "STOP SIGNAL",
    }[decision]
    explanation = (
        f"Entry {position.entry_price:.0f}¢. Current {position.current_price:.0f}¢. "
        f"Peak {position.peak_price:.0f}¢. Drawdown {position.drawdown_from_peak:.1f}¢. "
        f"Slip score {slip:.0f}/100. Position health {health:.0f}/100 {health_word}. "
        f"Normalized move {normalized:.1f} versus baseline volatility {vol:.1f}¢."
    )
    if decision == "SLIPPING":
        explanation += f" PREPARE TO EXIT. Confirmation {confirmation_count}/{needed}."
    elif decision == "STOP_EXIT_SIGNAL":
        explanation += " STOP / EXIT SIGNAL. You decide. Nothing is sold automatically."
    elif decision == "WATCH_CLOSELY":
        explanation += " Still holding. Short-term conditions are weakening."
    elif decision == "HOLD":
        explanation += " Position remains healthy."

    return SlipView(
        slip_score=slip,
        health=health,
        decision=decision,
        emergency=emergency,
        confirmation_count=confirmation_count,
        confirmation_needed=needed,
        normalized_move=normalized,
        reasons=reasons,
        explanation=explanation,
    )


def mark_position(position: TrackedPosition, price: float) -> None:
    position.current_price = price
    if price > position.peak_price:
        position.peak_price = price
    position.drawdown_from_peak = max(0.0, position.peak_price - price)


def side_mark_price(market: MarketState, direction: str) -> float:
    if direction == "YES":
        if market.last_trade:
            return market.last_trade
        return market.mid
    if market.no_bid:
        return market.no_bid
    return max(1.0, 100.0 - market.yes_ask) if market.yes_ask else market.mid


def _side_score(
    settings: Settings,
    market: MarketState,
    prob: ProbabilityResult,
    read: MarketRead,
    baseline: MatchBaseline,
    *,
    direction: str,
    player: str,
) -> dict:
    adjusted = prob.estimated_net_edge - (
        read.uncertainty_penalty if direction == "YES" else read.uncertainty_penalty
    )
    if direction == "NO":
        # Flip penalty already applied by caller via a flipped ProbabilityResult
        # whose estimated_net_edge is the NO edge. Penalty is in `read` for YES.
        adjusted = prob.estimated_net_edge - read.uncertainty_penalty
    price_pts = _clamp(42.0 + adjusted * 1300.0)
    strength = _clamp(64.0 + (prob.model_win_probability - 0.5) * 40.0 + max(0.0, adjusted) * 480.0)
    imb = market.imbalance if direction == "YES" else -market.imbalance
    book = _clamp(64.0 + imb * 70.0)
    mom = _directional_momentum(market, 5000, direction)
    momentum = _clamp(60.0 + mom * 8.0)
    velocity = _trade_velocity(market)
    base_vel = baseline.normal_trade_velocity or 0.2
    flow = _clamp(55.0 + (velocity / base_vel - 1.0) * 25.0 + max(0.0, mom) * 4.0)
    liquidity = liquidity_quality(market, settings)
    spread = spread_quality(market, settings)
    liq_pts = {"HIGH": 100, "GOOD": 82, "MEDIUM": 62, "LOW": 30, "VERY_LOW": 0}[liquidity]
    spread_pts = {"EXCELLENT": 100, "GOOD": 84, "ACCEPTABLE": 60, "POOR": 25, "VERY_POOR": 0}[spread]
    against = -mom
    vol = max(baseline.normal_volatility, settings.baseline_volatility_floor)
    reversal = 82.0 if against <= vol else _clamp(82.0 - (against / vol) * 28.0)

    score = (
        settings.weight_entry_strength * strength
        + settings.weight_entry_price * price_pts
        + settings.weight_entry_book * book
        + settings.weight_entry_momentum * momentum
        + settings.weight_entry_flow * flow
        + settings.weight_entry_liquidity * liq_pts
        + settings.weight_entry_spread * spread_pts
        + settings.weight_entry_reversal * reversal
    )
    ask = market.executable_yes_price() if direction == "YES" else market.executable_no_price()
    required = read.dynamic_min_edge if read.dynamic_min_edge is not None else settings.min_net_edge
    economic = prob.model_win_probability - required - prob.estimated_fees - settings.expected_slippage - settings.safety_margin
    economic_cents = _clamp(economic * 100.0, 1.0, 99.0)
    window_cap = ask + settings.max_entry_slippage_cents
    max_entry = min(economic_cents, window_cap)
    reasons = []
    if adjusted > 0:
        reasons.append("Price is favorable versus the model after costs")
    if book >= 70:
        reasons.append("Order book supports this side")
    if momentum >= 68:
        reasons.append("Momentum agrees")
    if liq_pts >= 80:
        reasons.append("Liquidity is healthy for this match")
    return {
        "score": score,
        "direction": direction,
        "player": player,
        "ask": ask,
        "max_entry": max_entry,
        "reasons": reasons,
    }


def _flip_prob(prob: ProbabilityResult) -> ProbabilityResult:
    """NO-side view of the same model. Does not invent tennis facts."""
    model = 1.0 - prob.model_win_probability
    market_p = 1.0 - prob.executable_market_probability
    raw = model - market_p
    # Keep the same fee shape the YES result already paid, mirrored.
    net = raw - (prob.model_win_probability - prob.executable_market_probability - prob.estimated_net_edge)
    return ProbabilityResult(
        player=prob.player,
        direction="NO",
        model_win_probability=model,
        executable_market_probability=market_p,
        raw_edge=raw,
        estimated_net_edge=net,
        source=prob.source,
        estimated_fees=prob.estimated_fees,
    )


def _directional_momentum(market: MarketState, window_ms: int, direction: str) -> float:
    stats = market.windows.get(window_ms)
    mom = (stats.momentum if stats else 0.0) or 0.0
    return mom if direction == "YES" else -mom


def _trade_velocity(market: MarketState) -> float:
    stats = market.windows.get(5000) or market.windows.get(10000)
    return (stats.trade_velocity if stats else 0.0) or 0.0
