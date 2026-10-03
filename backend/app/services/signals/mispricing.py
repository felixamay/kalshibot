"""Mispricing strategy: model vs executable price, after costs and market quality.

Opportunity is not "how high is the Kalshi percentage."
A 42¢ contract with a real edge can outrank a 75¢ favorite with almost none.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.services.market.analyzer import MarketState
from app.services.tennis.probability import ProbabilityResult

LIQUIDITY_ORDER = ["VERY_LOW", "LOW", "MEDIUM", "GOOD", "HIGH"]
SPREAD_ORDER = ["EXCELLENT", "GOOD", "ACCEPTABLE", "POOR", "VERY_POOR"]


@dataclass
class MarketRead:
    raw_edge: float
    estimated_fees: float
    expected_slippage: float
    safety_margin: float
    net_edge: float
    model_uncertainty: str
    uncertainty_penalty: float
    uncertainty_adjusted_edge: float
    liquidity_quality: str
    spread_quality: str
    market_quality_score: float
    dynamic_min_edge: float | None
    extreme_volatility: bool
    mispricing_score: float
    opportunity_score: float
    max_entry_price_cents: float
    confirmation_needed: int
    decision: str
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    explanation: str = ""
    close_to_signal: bool = False

    def as_dict(self) -> dict:
        return {
            "raw_edge": self.raw_edge,
            "estimated_fees": self.estimated_fees,
            "net_edge": self.net_edge,
            "model_uncertainty": self.model_uncertainty,
            "uncertainty_penalty": self.uncertainty_penalty,
            "uncertainty_adjusted_edge": self.uncertainty_adjusted_edge,
            "liquidity_quality": self.liquidity_quality,
            "spread_quality": self.spread_quality,
            "market_quality_score": round(self.market_quality_score, 1),
            "dynamic_min_edge": self.dynamic_min_edge,
            "extreme_volatility": self.extreme_volatility,
            "mispricing_score": round(self.mispricing_score, 1),
            "opportunity_score": round(self.opportunity_score, 1),
            "max_entry_price_cents": round(self.max_entry_price_cents, 2),
            "confirmation_needed": self.confirmation_needed,
            "decision": self.decision,
            "reasons": self.reasons,
            "blockers": self.blockers,
            "explanation": self.explanation,
            "close_to_signal": self.close_to_signal,
        }


def liquidity_quality(market: MarketState, settings: Settings) -> str:
    """Relative liquidity. HIGH is not one global contract count."""
    depth = max(0.0, market.depth_yes + market.depth_no)
    recent = _recent_liquidity(market)
    ratio = depth / recent if recent > 1 else 1.0
    velocity = _trade_velocity(market)

    if depth <= 0:
        return "VERY_LOW"
    if depth >= settings.liquidity_high_contracts and ratio >= 0.75:
        base = "HIGH"
    elif depth >= settings.liquidity_good_contracts and ratio >= 0.55:
        base = "GOOD"
    elif depth >= settings.liquidity_medium_contracts:
        base = "MEDIUM"
    elif depth >= settings.liquidity_low_contracts:
        base = "LOW"
    else:
        base = "VERY_LOW"

    idx = LIQUIDITY_ORDER.index(base)
    if ratio >= 1.8 and velocity >= 0.4 and idx < len(LIQUIDITY_ORDER) - 1:
        idx += 1
    elif ratio < 0.35 and idx > 0:
        idx -= 1
    return LIQUIDITY_ORDER[idx]


def spread_quality(market: MarketState, settings: Settings) -> str:
    spread = market.spread
    if spread <= settings.spread_excellent_cents:
        quality = "EXCELLENT"
    elif spread <= settings.spread_good_cents:
        quality = "GOOD"
    elif spread <= settings.spread_acceptable_cents:
        quality = "ACCEPTABLE"
    elif spread <= settings.max_spread_cents:
        quality = "POOR"
    else:
        quality = "VERY_POOR"

    w5 = market.windows.get(5000)
    avg = w5.avg_spread if w5 else 0.0
    if avg and avg >= spread + 3 and avg >= 4:
        idx = min(len(SPREAD_ORDER) - 1, SPREAD_ORDER.index(quality) + 1)
        quality = SPREAD_ORDER[idx]
    return quality


def dynamic_min_edge(
    liquidity: str,
    spread: str,
    *,
    extreme_volatility: bool,
    settings: Settings,
) -> float | None:
    """Required net edge after uncertainty. None means the market is not betable."""
    if extreme_volatility or liquidity == "VERY_LOW" or spread == "VERY_POOR":
        return None
    if liquidity == "HIGH" and spread == "EXCELLENT":
        return settings.excellent_market_min_edge
    if liquidity in ("HIGH", "GOOD") and spread in ("EXCELLENT", "GOOD"):
        return settings.min_net_edge
    if liquidity == "MEDIUM" or spread == "ACCEPTABLE":
        return settings.medium_market_min_edge
    return settings.poor_market_min_edge


def model_uncertainty(tennis_available: bool, source: str, settings: Settings) -> tuple[str, float]:
    if tennis_available and source == "tennis_enhanced":
        return "LOW", settings.uncertainty_penalty_low
    if tennis_available:
        return "MEDIUM", settings.uncertainty_penalty_medium
    return "HIGH", settings.uncertainty_penalty_high


def max_entry_price_cents(
    model_probability: float,
    required_edge: float,
    fees: float,
    slippage: float,
    safety: float,
) -> float:
    """Highest executable price that still clears the required edge after costs."""
    cap = model_probability - required_edge - fees - slippage - safety
    return max(1.0, min(99.0, cap * 100.0))


def assess_market(
    settings: Settings,
    market: MarketState,
    prob: ProbabilityResult,
    *,
    confidence: float,
    confirmation_count: int,
    tennis_available: bool,
    best_seen_ask: float | None = None,
) -> MarketRead:
    liquidity = liquidity_quality(market, settings)
    spread = spread_quality(market, settings)
    w5 = market.windows.get(5000)
    vol = (w5.volatility if w5 else 0.0) or 0.0
    extreme = vol > settings.extreme_volatility
    uncertainty, penalty = model_uncertainty(tennis_available, prob.source, settings)
    adjusted = prob.estimated_net_edge - penalty
    required = dynamic_min_edge(
        liquidity, spread, extreme_volatility=extreme, settings=settings
    )
    quality = _market_quality_score(market, settings, liquidity, spread, vol)
    fees = prob.estimated_fees
    required_for_cap = required if required is not None else settings.poor_market_min_edge
    max_entry = max_entry_price_cents(
        prob.model_win_probability,
        required_for_cap,
        fees,
        settings.expected_slippage,
        settings.safety_margin,
    )

    needed = settings.entry_confirmation_count
    exec_p = prob.executable_market_probability
    if (exec_p < 0.20 or exec_p > 0.85) and liquidity not in ("HIGH", "GOOD"):
        needed += 1

    mispricing = _mispricing_score(adjusted, exec_p, quality, confidence, liquidity, spread)
    mom = (w5.momentum if w5 else 0.0) or 0.0
    if prob.direction == "NO":
        mom = -mom
    trend = 80.0 if mom >= 0 else 25.0
    opportunity = _opportunity_score(adjusted, confidence, quality, liquidity, spread, trend)

    ask = market.executable_yes_price() if prob.direction == "YES" else market.executable_no_price()
    fresh = market.data_age_ms <= settings.max_data_age_ms
    mom_ok = mom >= -1.5
    imb = market.imbalance if prob.direction == "YES" else -market.imbalance
    book_ok = imb >= -0.4

    reasons: list[str] = []
    blockers: list[str] = []
    if adjusted > 0:
        reasons.append("Model estimates a higher win probability than the executable price")
    if required is not None and adjusted >= required:
        reasons.append("Net advantage remains after fees, slippage, safety margin, and model uncertainty")
    if liquidity in ("HIGH", "GOOD"):
        reasons.append(f"{liquidity.lower().replace('_', ' ')} executable depth")
    if spread in ("EXCELLENT", "GOOD"):
        reasons.append(f"Spread is {spread.lower()} ({market.spread:.1f}¢)")
    if mom > 0.2:
        reasons.append("Short-term momentum agrees")
    if book_ok and imb > 0.05:
        reasons.append("Order book is not leaning against the side")

    if extreme:
        blockers.append("Volatility is extreme")
    if liquidity == "VERY_LOW":
        blockers.append("Liquidity is very poor")
    if spread == "VERY_POOR":
        blockers.append("Spread is too wide or unstable")
    if not fresh:
        blockers.append("Quote is stale")
    if confidence < settings.min_bet_confidence:
        blockers.append(
            f"Confidence {confidence:.0f} is below {settings.min_bet_confidence:.0f}"
        )
    if required is None:
        blockers.append("This book is not betable at any listed edge")
    elif adjusted < required:
        blockers.append(
            f"Adjusted edge {adjusted * 100:+.1f}pp is below the required {required * 100:.1f}pp"
        )
    if not mom_ok:
        blockers.append("Momentum is against the side")
    if not book_ok:
        blockers.append("Order book is leaning the other way")
    if ask > max_entry:
        blockers.append(
            f"Price {ask:.0f}¢ is above the maximum entry {max_entry:.0f}¢"
        )
    if (
        best_seen_ask is not None
        and ask > best_seen_ask + settings.max_entry_slippage_cents
        and (required is None or adjusted < required)
    ):
        blockers.append(
            f"Price ran from {best_seen_ask:.0f}¢ to {ask:.0f}¢ and the edge no longer clears"
        )

    chased = (
        best_seen_ask is not None
        and ask > best_seen_ask + settings.max_entry_slippage_cents
        and (required is None or adjusted < required)
    )
    core_ok = (
        not extreme
        and liquidity != "VERY_LOW"
        and spread != "VERY_POOR"
        and fresh
        and required is not None
        and adjusted >= required
        and confidence >= settings.min_bet_confidence
        and mom_ok
        and book_ok
        and ask <= max_entry
        and not chased
    )
    strong = (
        core_ok
        and confidence >= settings.strong_bet_confidence
        and adjusted >= settings.strong_net_edge
        and liquidity in ("HIGH", "GOOD")
        and spread in ("EXCELLENT", "GOOD")
        and uncertainty != "HIGH"
        and mom >= 0
    )

    if chased:
        decision = "OPPORTUNITY_MISSED"
    elif (
        ask > max_entry
        and required is not None
        and adjusted >= required
        and confidence >= settings.min_bet_confidence
    ):
        decision = "DO_NOT_CHASE"
    elif extreme:
        decision = "MATCH_TOO_VOLATILE"
    elif strong and confirmation_count >= needed:
        decision = "STRONG_BET_SIGNAL"
    elif core_ok and confirmation_count >= needed:
        decision = "BET_SIGNAL"
    elif core_ok and confirmation_count > 0:
        decision = "CLOSE_TO_SIGNAL"
    elif core_ok:
        decision = "WATCH"
    elif (
        not extreme
        and liquidity != "VERY_LOW"
        and spread != "VERY_POOR"
        and fresh
        and settings.watch_confidence <= confidence < settings.min_bet_confidence
        and adjusted > 0
    ):
        decision = "WATCH"
    elif (
        not extreme
        and liquidity != "VERY_LOW"
        and required is not None
        and adjusted >= required * 0.6
        and confidence >= settings.watch_confidence
        and confirmation_count < needed
    ):
        decision = "WATCH"
    else:
        decision = "NO_BET"

    close = decision == "CLOSE_TO_SIGNAL"
    if decision in ("WATCH", "CLOSE_TO_SIGNAL") and confirmation_count < needed:
        blockers.append(
            f"Confirmation {confirmation_count} of {needed} required updates"
        )

    explanation = _explanation(
        decision=decision,
        prob=prob,
        adjusted=adjusted,
        required=required,
        confidence=confidence,
        liquidity=liquidity,
        spread=spread,
        market=market,
        uncertainty=uncertainty,
        penalty=penalty,
        reasons=reasons,
        blockers=blockers,
        confirmation_count=confirmation_count,
        needed=needed,
        settings=settings,
    )

    return MarketRead(
        raw_edge=prob.raw_edge,
        estimated_fees=fees,
        expected_slippage=settings.expected_slippage,
        safety_margin=settings.safety_margin,
        net_edge=prob.estimated_net_edge,
        model_uncertainty=uncertainty,
        uncertainty_penalty=penalty,
        uncertainty_adjusted_edge=adjusted,
        liquidity_quality=liquidity,
        spread_quality=spread,
        market_quality_score=quality,
        dynamic_min_edge=required,
        extreme_volatility=extreme,
        mispricing_score=mispricing,
        opportunity_score=opportunity,
        max_entry_price_cents=max_entry,
        confirmation_needed=needed,
        decision=decision,
        reasons=reasons,
        blockers=blockers,
        explanation=explanation,
        close_to_signal=close,
    )


def compare_strategy_profiles(samples: list[dict]) -> dict:
    """Describe old fixed gates vs the dynamic mispricing gates.

    Does not pick a winner from historical profit.
    """
    old_hits = []
    new_hits = []
    for sample in samples:
        confidence = float(sample["confidence"])
        edge = float(sample["adjusted_edge"])
        liquidity = sample.get("liquidity_quality", "MEDIUM")
        spread = sample.get("spread_quality", "GOOD")
        extreme = bool(sample.get("extreme_volatility", False))
        old_ok = confidence >= 85 and edge >= 0.04 and not extreme and liquidity != "VERY_LOW"
        required = sample.get("dynamic_min_edge")
        new_ok = (
            confidence >= 80
            and required is not None
            and edge >= float(required)
            and not extreme
            and liquidity != "VERY_LOW"
            and spread != "VERY_POOR"
        )
        row = {
            "name": sample.get("name"),
            "confidence": confidence,
            "adjusted_edge": edge,
            "market_quality": sample.get("market_quality"),
            "price_after_5s": sample.get("price_after_5s"),
            "price_after_15s": sample.get("price_after_15s"),
            "price_after_30s": sample.get("price_after_30s"),
            "max_favorable_movement": sample.get("max_favorable_movement"),
            "max_adverse_movement": sample.get("max_adverse_movement"),
        }
        if old_ok:
            old_hits.append(row)
        if new_ok:
            new_hits.append(row)

    def _pack(hits: list[dict]) -> dict:
        n = len(hits)
        adverse = [h["max_adverse_movement"] for h in hits if h.get("max_adverse_movement") is not None]
        false_pos = 0
        for h in hits:
            future = h.get("price_after_15s")
            entry = None
            if future is not None and h.get("max_adverse_movement") is not None:
                if h["max_adverse_movement"] < -3:
                    false_pos += 1
        return {
            "signals": n,
            "avg_adjusted_edge": (sum(h["adjusted_edge"] for h in hits) / n) if n else None,
            "avg_adverse_movement": (sum(adverse) / len(adverse)) if adverse else None,
            "false_positive_proxy": false_pos,
            "samples": hits,
        }

    return {
        "matches": len(samples),
        "old": {"label": "85 confidence / 4% fixed edge", **_pack(old_hits)},
        "new": {"label": "80 confidence / dynamic 1.5–4% edge", **_pack(new_hits)},
        "auto_select_enabled": False,
        "warning": (
            "Do not adopt the profile with the larger historical profit. "
            "Compare drawdown, false positives, consistency, and out-of-sample results first."
        ),
    }


def _recent_liquidity(market: MarketState) -> float:
    for window in (60000, 30000, 10000, 5000):
        stats = market.windows.get(window)
        if stats and stats.liquidity > 0:
            return stats.liquidity
    return max(0.0, market.depth_yes + market.depth_no)


def _trade_velocity(market: MarketState) -> float:
    stats = market.windows.get(5000) or market.windows.get(10000)
    return (stats.trade_velocity if stats else 0.0) or 0.0


def _market_quality_score(
    market: MarketState,
    settings: Settings,
    liquidity: str,
    spread: str,
    volatility: float,
) -> float:
    spread_pts = {"EXCELLENT": 100, "GOOD": 82, "ACCEPTABLE": 60, "POOR": 28, "VERY_POOR": 0}[spread]
    liq_pts = {"HIGH": 100, "GOOD": 82, "MEDIUM": 60, "LOW": 30, "VERY_LOW": 0}[liquidity]
    if market.data_age_ms <= settings.max_data_age_ms:
        fresh = max(0.0, 100.0 - (market.data_age_ms / max(settings.max_data_age_ms, 1)) * 40.0)
    else:
        fresh = 0.0
    vol_pts = max(0.0, min(100.0, 100.0 - volatility * 25.0))
    trade_pts = min(100.0, _trade_velocity(market) * 25.0)
    return 0.30 * spread_pts + 0.30 * liq_pts + 0.15 * fresh + 0.15 * vol_pts + 0.10 * trade_pts


def _mispricing_score(
    adjusted_edge: float,
    exec_prob: float,
    quality: float,
    confidence: float,
    liquidity: str,
    spread: str,
) -> float:
    edge_pts = max(0.0, min(100.0, (adjusted_edge / 0.08) * 100.0))
    relative = adjusted_edge / max(exec_prob, 0.05)
    relative_pts = max(0.0, min(100.0, (relative / 0.20) * 100.0))
    execution = ({"HIGH": 100, "GOOD": 80, "MEDIUM": 55, "LOW": 25, "VERY_LOW": 0}[liquidity] +
                 {"EXCELLENT": 100, "GOOD": 80, "ACCEPTABLE": 55, "POOR": 25, "VERY_POOR": 0}[spread]) / 2
    return (
        0.30 * edge_pts
        + 0.20 * relative_pts
        + 0.20 * quality
        + 0.15 * max(0.0, min(100.0, confidence))
        + 0.15 * execution
    )


def _opportunity_score(
    adjusted_edge: float,
    confidence: float,
    quality: float,
    liquidity: str,
    spread: str,
    trend: float,
) -> float:
    """Dashboard ranking only. Never the sole trigger for a bet."""
    edge_pts = max(0.0, min(100.0, (adjusted_edge / 0.06) * 100.0))
    liq_pts = {"HIGH": 100, "GOOD": 80, "MEDIUM": 55, "LOW": 25, "VERY_LOW": 0}[liquidity]
    spread_pts = {"EXCELLENT": 100, "GOOD": 80, "ACCEPTABLE": 55, "POOR": 25, "VERY_POOR": 0}[spread]
    return (
        0.30 * edge_pts
        + 0.25 * max(0.0, min(100.0, confidence))
        + 0.20 * quality
        + 0.10 * liq_pts
        + 0.10 * spread_pts
        + 0.05 * trend
    )


def _explanation(
    *,
    decision: str,
    prob: ProbabilityResult,
    adjusted: float,
    required: float | None,
    confidence: float,
    liquidity: str,
    spread: str,
    market: MarketState,
    uncertainty: str,
    penalty: float,
    reasons: list[str],
    blockers: list[str],
    confirmation_count: int,
    needed: int,
    settings: Settings,
) -> str:
    market_cents = prob.executable_market_probability * 100
    model_pct = prob.model_win_probability * 100
    header = (
        f"Market {market_cents:.0f}¢. Model {model_pct:.0f}%. "
        f"Raw difference {prob.raw_edge * 100:+.1f}pp. "
        f"Costs {(prob.estimated_fees + settings.expected_slippage + settings.safety_margin) * 100:.1f}pp. "
        f"Uncertainty penalty {penalty * 100:.1f}pp ({uncertainty}). "
        f"Adjusted edge {adjusted * 100:+.1f}pp. "
        f"Confidence {confidence:.0f}. "
        f"Liquidity {liquidity}. Spread {spread} ({market.spread:.1f}¢)."
    )
    if required is not None:
        header += f" Required edge {required * 100:.1f}pp."
    if decision == "WATCH":
        why = " This is not an instruction to bet."
        if blockers:
            why += " " + " ".join(blockers[:3]) + "."
        return header + why
    if decision == "CLOSE_TO_SIGNAL":
        return (
            header
            + f" Close to a bet: confidence {confidence:.0f}/{settings.min_bet_confidence:.0f},"
            + f" confirmation {confirmation_count}/{needed}."
        )
    if decision in ("BET_SIGNAL", "STRONG_BET_SIGNAL"):
        extra = " ".join(f"+ {r}" for r in reasons[:4])
        note = ""
        if decision == "STRONG_BET_SIGNAL":
            note = " 90 here is signal confidence, not a 90% win probability."
        return header + (" " + extra if extra else "") + note
    if blockers:
        return header + " " + blockers[0] + "."
    return header + " No bet."
