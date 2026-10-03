"""Dynamic signal lifetime calculation from market conditions."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.services.market.analyzer import MarketState


@dataclass
class TTLResult:
    ttl_seconds: float
    ttl_ms: int
    reason: str


class SignalTTLCalculator:
    """
    Computes signal lifetime from volatility, trade velocity, liquidity,
    spread, price acceleration, order-book changes, and match state.

    Range: MIN_SIGNAL_TTL .. MAX_SIGNAL_TTL (configurable).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def calculate(
        self,
        market: MarketState,
        *,
        confidence: float = 85.0,
        match_volatile: bool = False,
    ) -> TTLResult:
        min_ttl = self.settings.min_signal_ttl_seconds
        max_ttl = self.settings.max_signal_ttl_seconds
        default = self.settings.default_signal_ttl_seconds

        w5 = market.windows.get(5000)
        w2 = market.windows.get(2000)
        w10 = market.windows.get(10000)

        volatility = (w5.volatility if w5 else 0.0) or 0.0
        velocity = (w2.trade_velocity if w2 else 0.0) or 0.0
        accel = abs((w5.price_acceleration if w5 else 0.0) or 0.0)
        liquidity = (w10.liquidity if w10 else market.depth_yes + market.depth_no) or 0.0
        spread = market.spread

        # Start from default and adjust
        ttl = default
        reasons: list[str] = []

        # Fast / volatile markets → shorter TTL
        if volatility > 1.5 or velocity > 8 or accel > 2.0 or match_volatile:
            ttl = min_ttl + 1.2
            reasons.append("fast_market")
        elif volatility > 0.8 or velocity > 4:
            ttl = default * 0.7
            reasons.append("moderate_volatility")
        elif volatility < 0.3 and velocity < 2 and spread <= 3 and liquidity >= 100:
            ttl = max_ttl * 0.85
            reasons.append("stable_liquid")
        else:
            reasons.append("default")

        # Wide spread or thin book → shorter
        if spread > 4:
            ttl *= 0.75
            reasons.append("wide_spread")
        if liquidity < 50:
            ttl *= 0.7
            reasons.append("thin_liquidity")

        # Higher confidence can slightly extend (but never chase max without stability)
        if confidence >= 92 and "stable_liquid" in reasons:
            ttl = min(max_ttl, ttl + 2.0)
            reasons.append("high_confidence_extend")

        ttl = max(min_ttl, min(max_ttl, ttl))
        return TTLResult(ttl_seconds=round(ttl, 1), ttl_ms=int(ttl * 1000), reason="+".join(reasons))
