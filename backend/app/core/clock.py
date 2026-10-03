"""Clock synchronization helpers for drift-free countdowns."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ClockSync:
    """Tracks server/client clock offset and network latency."""

    offset_ms: float = 0.0  # estimated_server = client_now + offset
    latency_ms: float = 0.0
    last_sync_at_ms: float = 0.0
    samples: list[tuple[float, float]] = field(default_factory=list)

    def record_rtt(
        self,
        client_send_ms: float,
        server_time_ms: float,
        client_recv_ms: float,
    ) -> None:
        rtt = client_recv_ms - client_send_ms
        latency = rtt / 2.0
        # Server time when client received ≈ server_time + latency
        # offset such that client_now + offset ≈ server_now
        mid_client = client_send_ms + latency
        offset = server_time_ms - mid_client
        self.samples.append((offset, latency))
        if len(self.samples) > 20:
            self.samples = self.samples[-20:]
        offsets = sorted(s[0] for s in self.samples)
        latencies = [s[1] for s in self.samples]
        mid = len(offsets) // 2
        self.offset_ms = offsets[mid]
        self.latency_ms = sum(latencies) / len(latencies)
        self.last_sync_at_ms = client_recv_ms

    def estimated_server_now_ms(self, client_now_ms: float | None = None) -> float:
        if client_now_ms is None:
            client_now_ms = time.time() * 1000.0
        return client_now_ms + self.offset_ms

    def to_dict(self) -> dict:
        return {
            "offset_ms": round(self.offset_ms, 3),
            "latency_ms": round(self.latency_ms, 3),
            "last_sync_at_ms": self.last_sync_at_ms,
            "sample_count": len(self.samples),
        }


def now_ms() -> float:
    return time.time() * 1000.0


def remaining_ms(expires_at_ms: float, server_now_ms: float | None = None) -> float:
    """Authoritative remaining time — never use interval subtraction."""
    if server_now_ms is None:
        server_now_ms = now_ms()
    return max(0.0, expires_at_ms - server_now_ms)


def urgency_stage(remaining: float) -> str:
    if remaining <= 0:
        return "EXPIRED"
    if remaining < 2000:
        return "FINAL"
    if remaining <= 5000:
        return "CAUTION"
    return "NORMAL"
