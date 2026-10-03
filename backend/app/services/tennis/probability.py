"""TennisProbabilityModel — model win probability vs executable Kalshi price."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import Settings, get_settings
from app.services.market.analyzer import MarketState
from app.services.tennis.provider import TennisLiveState


@dataclass
class ProbabilityResult:
    player: str
    direction: str  # YES / NO
    model_win_probability: float  # 0-1
    executable_market_probability: float  # 0-1
    raw_edge: float
    estimated_net_edge: float
    source: str  # market_implied_adjusted | tennis_enhanced
    estimated_fees: float = 0.0
    uncertainty_adjusted_edge: float | None = None
    model_uncertainty: str = "MEDIUM"


class TennisProbabilityModel:
    """
    Produces MODEL_WIN_PROBABILITY.

    When tennis live stats are unavailable, uses a conservative market-microstructure
    adjusted prior (NOT invented match stats). When tennis data is available,
    blends serve/break pressure signals with market mid as an anchor.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def estimate(
        self,
        *,
        player: str,
        market: MarketState,
        tennis: Optional[TennisLiveState] = None,
        direction: str = "YES",
    ) -> ProbabilityResult:
        # Executable probability for buying YES is ask/100
        if direction == "YES":
            exec_cents = market.executable_yes_price()
        else:
            exec_cents = market.executable_no_price()
        exec_prob = max(0.01, min(0.99, exec_cents / 100.0))

        mid_prob = max(0.01, min(0.99, market.mid / 100.0))
        if direction == "NO":
            mid_prob = 1.0 - mid_prob

        source = "market_implied_adjusted"
        model_prob = mid_prob

        # Microstructure adjustment: lean toward microprice & imbalance
        micro = market.microprice / 100.0
        if direction == "NO":
            micro = 1.0 - micro
        imbalance = market.imbalance  # positive => more YES bid depth
        if direction == "NO":
            imbalance = -imbalance

        # Small adjustment only — never invent a large edge from nowhere
        micro_adj = (micro - mid_prob) * 0.35
        imbalance_adj = max(-0.03, min(0.03, imbalance * 0.02))
        model_prob = mid_prob + micro_adj + imbalance_adj

        if tennis and tennis.available:
            source = "tennis_enhanced"
            tennis_adj = self._tennis_adjustment(tennis, player)
            model_prob = 0.65 * model_prob + 0.35 * (mid_prob + tennis_adj)

        model_prob = max(0.02, min(0.98, model_prob))
        raw_edge = model_prob - exec_prob
        # Fee scales with how contested the contract is, not with the size of our edge.
        fees = self.settings.estimated_fee_rate * exec_prob * (1.0 - exec_prob)
        net_edge = (
            raw_edge
            - fees
            - self.settings.expected_slippage
            - self.settings.safety_margin
        )

        return ProbabilityResult(
            player=player,
            direction=direction,
            model_win_probability=model_prob,
            executable_market_probability=exec_prob,
            raw_edge=raw_edge,
            estimated_net_edge=net_edge,
            source=source,
            estimated_fees=fees,
        )

    def _tennis_adjustment(self, tennis: TennisLiveState, player: str) -> float:
        """Bounded adjustment from real stats only — never invents missing values."""
        adj = 0.0
        is_a = player.lower() in (tennis.player_a or "").lower()
        # Break point pressure
        bp_a = tennis.break_points_a
        bp_b = tennis.break_points_b
        if bp_a is not None and bp_b is not None:
            if is_a and bp_a > bp_b:
                adj += 0.015
            elif not is_a and bp_b > bp_a:
                adj += 0.015
            elif is_a and bp_a < bp_b:
                adj -= 0.01
            elif not is_a and bp_b < bp_a:
                adj -= 0.01
        # Double faults (negative)
        df_a = tennis.double_faults_a
        df_b = tennis.double_faults_b
        if df_a is not None and df_b is not None:
            if is_a and df_a > df_b + 1:
                adj -= 0.01
            if not is_a and df_b > df_a + 1:
                adj -= 0.01
        # Serving advantage
        if tennis.server:
            if (tennis.server == "A" and is_a) or (tennis.server == "B" and not is_a):
                adj += 0.008
        return max(-0.05, min(0.05, adj))
