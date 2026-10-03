"""Rolling-window market microstructure indicators."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass
class TickPoint:
    ts_ms: float
    yes_bid: float
    yes_ask: float
    last_trade: Optional[float] = None
    volume: float = 0.0
    imbalance: float = 0.0
    depth_yes: float = 0.0
    depth_no: float = 0.0

    @property
    def mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> float:
        return max(0.0, self.yes_ask - self.yes_bid)

    @property
    def microprice(self) -> float:
        # volume-weighted mid approximation using depth
        d_yes = max(self.depth_yes, 1e-9)
        d_no = max(self.depth_no, 1e-9)
        return (self.yes_ask * d_yes + self.yes_bid * d_no) / (d_yes + d_no)


@dataclass
class WindowStats:
    window_ms: int
    count: int = 0
    volatility: float = 0.0
    momentum: float = 0.0
    trade_velocity: float = 0.0
    avg_spread: float = 0.0
    avg_imbalance: float = 0.0
    price_acceleration: float = 0.0
    liquidity: float = 0.0


@dataclass
class MarketState:
    ticker: str
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    last_trade: Optional[float] = None
    volume: float = 0.0
    imbalance: float = 0.0
    depth_yes: float = 0.0
    depth_no: float = 0.0
    last_update_ms: float = 0.0
    status: str = "OPEN"
    windows: dict[int, WindowStats] = field(default_factory=dict)

    @property
    def spread(self) -> float:
        return max(0.0, self.yes_ask - self.yes_bid)

    @property
    def mid(self) -> float:
        if self.yes_bid or self.yes_ask:
            return (self.yes_bid + self.yes_ask) / 2.0
        return self.last_trade or 0.0

    @property
    def microprice(self) -> float:
        d_yes = max(self.depth_yes, 1e-9)
        d_no = max(self.depth_no, 1e-9)
        if self.yes_bid or self.yes_ask:
            return (self.yes_ask * d_yes + self.yes_bid * d_no) / (d_yes + d_no)
        return self.mid

    @property
    def data_age_ms(self) -> float:
        if not self.last_update_ms:
            return float("inf")
        return time.time() * 1000.0 - self.last_update_ms

    def executable_yes_price(self) -> float:
        """Price to buy YES (ask)."""
        return self.yes_ask if self.yes_ask > 0 else (self.last_trade or 50.0)

    def executable_no_price(self) -> float:
        return self.no_ask if self.no_ask > 0 else (100.0 - (self.yes_bid or 50.0))


class RollingMarketAnalyzer:
    """Maintains multi-horizon rolling windows for a single market."""

    def __init__(self, ticker: str, window_sizes_ms: list[int]) -> None:
        self.ticker = ticker
        self.window_sizes_ms = sorted(window_sizes_ms)
        # Cap history. The longest window is 5 minutes; unbounded scans were
        # blocking the API on every WebSocket tick.
        self._ticks: Deque[TickPoint] = deque(maxlen=8_000)
        self._last_recompute_ms = 0.0
        self.state = MarketState(ticker=ticker)
        for w in self.window_sizes_ms:
            self.state.windows[w] = WindowStats(window_ms=w)

    def update(
        self,
        *,
        yes_bid: Optional[float] = None,
        yes_ask: Optional[float] = None,
        no_bid: Optional[float] = None,
        no_ask: Optional[float] = None,
        last_trade: Optional[float] = None,
        volume: Optional[float] = None,
        imbalance: Optional[float] = None,
        depth_yes: Optional[float] = None,
        depth_no: Optional[float] = None,
        status: Optional[str] = None,
        ts_ms: Optional[float] = None,
    ) -> MarketState:
        ts = ts_ms if ts_ms is not None else time.time() * 1000.0
        if yes_bid is not None:
            self.state.yes_bid = float(yes_bid)
        if yes_ask is not None:
            self.state.yes_ask = float(yes_ask)
        if no_bid is not None:
            self.state.no_bid = float(no_bid)
        elif yes_ask is not None:
            self.state.no_bid = 100.0 - float(yes_ask)
        if no_ask is not None:
            self.state.no_ask = float(no_ask)
        elif yes_bid is not None:
            self.state.no_ask = 100.0 - float(yes_bid)
        if last_trade is not None:
            self.state.last_trade = float(last_trade)
        if volume is not None:
            self.state.volume = float(volume)
        if imbalance is not None:
            self.state.imbalance = float(imbalance)
        if depth_yes is not None:
            self.state.depth_yes = float(depth_yes)
        if depth_no is not None:
            self.state.depth_no = float(depth_no)
        if status is not None:
            self.state.status = status
        self.state.last_update_ms = ts

        tick = TickPoint(
            ts_ms=ts,
            yes_bid=self.state.yes_bid,
            yes_ask=self.state.yes_ask,
            last_trade=self.state.last_trade,
            volume=self.state.volume,
            imbalance=self.state.imbalance,
            depth_yes=self.state.depth_yes,
            depth_no=self.state.depth_no,
        )
        self._ticks.append(tick)
        # Drop anything older than the longest window, then recompute at most
        # a few times a second so a hot order book cannot stall HTTP.
        max_w = self.window_sizes_ms[-1] if self.window_sizes_ms else 0
        while self._ticks and ts - self._ticks[0].ts_ms > max_w:
            self._ticks.popleft()
        if ts - self._last_recompute_ms >= 200 or self._last_recompute_ms == 0:
            self._recompute_windows(ts)
            self._last_recompute_ms = ts
        return self.state

    def _recompute_windows(self, now_ms: float) -> None:
        ticks = list(self._ticks)
        n = len(ticks)
        start = 0
        for w in self.window_sizes_ms:
            while start < n and now_ms - ticks[start].ts_ms > w:
                start += 1
            pts = ticks[start:]
            stats = WindowStats(window_ms=w, count=len(pts))
            if len(pts) >= 2:
                mids = [p.mid for p in pts]
                returns = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
                mean_r = sum(returns) / len(returns)
                var = sum((r - mean_r) ** 2 for r in returns) / max(len(returns), 1)
                stats.volatility = math.sqrt(var)
                stats.momentum = mids[-1] - mids[0]
                # acceleration: recent half vs earlier half momentum
                mid_idx = len(mids) // 2
                early = mids[mid_idx] - mids[0] if mid_idx > 0 else 0.0
                late = mids[-1] - mids[mid_idx]
                stats.price_acceleration = late - early
                stats.trade_velocity = len(pts) / (w / 1000.0)
                stats.avg_spread = sum(p.spread for p in pts) / len(pts)
                stats.avg_imbalance = sum(p.imbalance for p in pts) / len(pts)
                stats.liquidity = sum(p.depth_yes + p.depth_no for p in pts) / len(pts)
            elif len(pts) == 1:
                stats.avg_spread = pts[0].spread
                stats.avg_imbalance = pts[0].imbalance
                stats.liquidity = pts[0].depth_yes + pts[0].depth_no
            self.state.windows[w] = stats

    def snapshot(self) -> dict:
        s = self.state
        return {
            "ticker": s.ticker,
            "yes_bid": s.yes_bid,
            "yes_ask": s.yes_ask,
            "no_bid": s.no_bid,
            "no_ask": s.no_ask,
            "last_trade": s.last_trade,
            "spread": s.spread,
            "mid": s.mid,
            "microprice": s.microprice,
            "volume": s.volume,
            "imbalance": s.imbalance,
            "depth_yes": s.depth_yes,
            "depth_no": s.depth_no,
            "data_age_ms": s.data_age_ms,
            "status": s.status,
            "windows": {
                str(k): {
                    "count": v.count,
                    "volatility": round(v.volatility, 4),
                    "momentum": round(v.momentum, 4),
                    "trade_velocity": round(v.trade_velocity, 4),
                    "avg_spread": round(v.avg_spread, 4),
                    "avg_imbalance": round(v.avg_imbalance, 4),
                    "price_acceleration": round(v.price_acceleration, 4),
                    "liquidity": round(v.liquidity, 4),
                }
                for k, v in s.windows.items()
            },
        }
