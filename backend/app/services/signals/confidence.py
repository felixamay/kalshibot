"""Signal confidence scoring (0–100) with configurable weights."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.services.market.analyzer import MarketState
from app.services.tennis.probability import ProbabilityResult


@dataclass
class ConfidenceBreakdown:
    confidence: float
    model_component: float
    orderbook_component: float
    momentum_component: float
    trade_flow_component: float
    liquidity_component: float
    spread_component: float
    trend_component: float
    volatility_component: float


class SignalConfidenceCalculator:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def calculate(
        self,
        market: MarketState,
        prob: ProbabilityResult,
        *,
        confirmation_count: int = 0,
        required_confirmations: int | None = None,
    ) -> ConfidenceBreakdown:
        s = self.settings
        req = required_confirmations or s.entry_confirmation_count

        # Model/mispricing edge (0-100). Prefer uncertainty-adjusted edge.
        edge = (
            prob.uncertainty_adjusted_edge
            if prob.uncertainty_adjusted_edge is not None
            else prob.estimated_net_edge
        )
        model_c = max(0.0, min(100.0, (edge / 0.045) * 100.0))  # 4.5pp adjusted edge => 100
        if getattr(prob, "model_uncertainty", "MEDIUM") == "HIGH":
            model_c *= max(0.0, 1.0 - s.uncertainty_confidence_haircut)

        # Order book: imbalance aligned with direction
        imb = market.imbalance
        if prob.direction == "NO":
            imb = -imb
        orderbook_c = max(0.0, min(100.0, 65 + imb * 40))

        # Momentum over 5s / 10s
        w5 = market.windows.get(5000)
        w10 = market.windows.get(10000)
        mom = (w5.momentum if w5 else 0.0) or 0.0
        if prob.direction == "NO":
            mom = -mom
        momentum_c = max(0.0, min(100.0, 65 + mom * 8))

        # Trade flow / velocity (moderate is good; extreme is risky)
        vel = (w5.trade_velocity if w5 else 0.0) or 0.0
        if 1 <= vel <= 6:
            flow_c = 85.0
        elif vel < 1:
            flow_c = 55.0
        else:
            flow_c = max(20.0, 90.0 - (vel - 6) * 8)

        # Liquidity
        liq = market.depth_yes + market.depth_no
        liquidity_c = max(0.0, min(100.0, (liq / max(s.min_liquidity_contracts, 1)) * 70))

        # Spread (tighter = better)
        spread = market.spread
        if spread <= 2:
            spread_c = 95.0
        elif spread <= s.max_spread_cents:
            spread_c = 75.0
        else:
            spread_c = max(0.0, 40.0 - (spread - s.max_spread_cents) * 10)

        # Trend consistency: 5s and 10s momentum same sign
        mom10 = (w10.momentum if w10 else 0.0) or 0.0
        if prob.direction == "NO":
            mom10 = -mom10
        if mom * mom10 > 0:
            trend_c = 80.0
        elif mom == 0 and mom10 == 0:
            trend_c = 65.0
        else:
            trend_c = 30.0

        # Volatility / reversal risk (lower vol = higher score)
        vol = (w5.volatility if w5 else 0.0) or 0.0
        vol_c = max(0.0, min(100.0, 100.0 - vol * 25))

        conf = (
            s.weight_model_edge * model_c
            + s.weight_orderbook * orderbook_c
            + s.weight_momentum * momentum_c
            + s.weight_trade_flow * flow_c
            + s.weight_liquidity * liquidity_c
            + s.weight_spread * spread_c
            + s.weight_trend * trend_c
            + s.weight_volatility_risk * vol_c
        )

        # Confirmation count is enforced separately by the signal engine
        # (ENTRY_CONFIRMATION_COUNT). Do not scale confidence here — that
        # created a deadlock where the counter could never increment.
        _ = (confirmation_count, req)

        return ConfidenceBreakdown(
            confidence=round(min(100.0, max(0.0, conf)), 1),
            model_component=round(model_c, 1),
            orderbook_component=round(orderbook_c, 1),
            momentum_component=round(momentum_c, 1),
            trade_flow_component=round(flow_c, 1),
            liquidity_component=round(liquidity_c, 1),
            spread_component=round(spread_c, 1),
            trend_component=round(trend_c, 1),
            volatility_component=round(vol_c, 1),
        )
