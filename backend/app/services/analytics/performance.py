"""Track signal price performance at fixed horizons after creation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


HORIZONS = (1, 5, 10, 30, 60)


@dataclass
class TrackedSignal:
    signal_id: str
    direction: str
    entry_price: float
    created_at_ms: float
    original_ttl_ms: int
    expiration_reason: Optional[str] = None
    prices: list[tuple[float, float]] = field(default_factory=list)  # (ts_ms, price)
    results: dict[int, dict[str, Any]] = field(default_factory=dict)


class PerformanceTracker:
    def __init__(self) -> None:
        self.tracked: dict[str, TrackedSignal] = {}

    def register(self, signal_id: str, direction: str, entry_price: float, created_at_ms: float, original_ttl_ms: int) -> None:
        self.tracked[signal_id] = TrackedSignal(
            signal_id=signal_id,
            direction=direction,
            entry_price=entry_price,
            created_at_ms=created_at_ms,
            original_ttl_ms=original_ttl_ms,
        )

    def on_price(self, signal_id: str, price: float, ts_ms: float | None = None) -> None:
        t = self.tracked.get(signal_id)
        if not t:
            return
        now = ts_ms or time.time() * 1000.0
        t.prices.append((now, price))
        age_s = (now - t.created_at_ms) / 1000.0
        for h in HORIZONS:
            if h in t.results:
                continue
            if age_s >= h:
                t.results[h] = self._compute(t, h)

    def mark_expired(self, signal_id: str, reason: str) -> None:
        t = self.tracked.get(signal_id)
        if t:
            t.expiration_reason = reason

    def _compute(self, t: TrackedSignal, horizon: int) -> dict[str, Any]:
        cutoff = t.created_at_ms + horizon * 1000
        window = [p for ts, p in t.prices if ts <= cutoff] or [t.entry_price]
        future = window[-1]
        if t.direction == "YES":
            moves = [p - t.entry_price for p in window]
        else:
            moves = [t.entry_price - p for p in window]
        return {
            "horizon_seconds": horizon,
            "entry_price": t.entry_price,
            "future_price": future,
            "max_favorable_movement": max(moves) if moves else 0,
            "max_adverse_movement": min(moves) if moves else 0,
        }

    def summary(self) -> dict[str, Any]:
        by_ttl: dict[str, list] = {}
        rows = []
        for t in self.tracked.values():
            row = {
                "signal_id": t.signal_id,
                "entry_price": t.entry_price,
                "original_ttl_ms": t.original_ttl_ms,
                "expiration_reason": t.expiration_reason,
                "results": t.results,
            }
            rows.append(row)
            key = str(int(t.original_ttl_ms / 1000))
            by_ttl.setdefault(key, []).append(row)
        return {"signals": rows[-100:], "by_ttl_seconds": {k: len(v) for k, v in by_ttl.items()}}


performance_tracker = PerformanceTracker()
