"""In-memory live signal objects with timestamp-based expiration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.clock import remaining_ms, urgency_stage
from app.core.enums import (
    EXPIRATION_REASON_MESSAGES,
    ExpirationReason,
    SignalStatus,
    SignalType,
)


@dataclass
class LiveSignal:
    signal_id: str
    signal_version: int
    match_id: Optional[str]
    market_id: str
    market_ticker: str
    signal_type: SignalType
    player: str
    direction: str
    created_at_ms: float
    expires_at_ms: float
    original_ttl_ms: int
    market_price: float
    target_entry_price: float
    maximum_entry_price: float
    model_probability: float
    net_edge: float
    confidence: float
    status: SignalStatus = SignalStatus.ACTIVE
    expiration_reason: Optional[ExpirationReason] = None
    expiration_message: Optional[str] = None
    cancelled_at_ms: Optional[float] = None
    creation_price: float = 0.0
    expiration_price: Optional[float] = None
    tournament: Optional[str] = None
    analysis_mode: str = "MARKET_ONLY"
    confirmation_count: int = 0
    lifecycle: list[dict[str, Any]] = field(default_factory=list)

    def remaining_ms(self, server_now_ms: float | None = None) -> float:
        now = server_now_ms if server_now_ms is not None else time.time() * 1000.0
        if self.status != SignalStatus.ACTIVE:
            return 0.0
        return remaining_ms(self.expires_at_ms, now)

    def is_actionable(self, server_now_ms: float | None = None) -> bool:
        """
        ABSOLUTE SAFETY RULE:
        Actionable ONLY when server_now < expires_at AND status == ACTIVE.
        """
        now = server_now_ms if server_now_ms is not None else time.time() * 1000.0
        if self.status != SignalStatus.ACTIVE:
            return False
        if now >= self.expires_at_ms:
            return False
        if self.signal_type not in (SignalType.BET_NOW, SignalType.STRONG_BET_SIGNAL):
            return False
        return True

    def urgency(self, server_now_ms: float | None = None) -> str:
        return urgency_stage(self.remaining_ms(server_now_ms))

    def progress(self, server_now_ms: float | None = None) -> float:
        """remaining / original_ttl — for progress bar."""
        if self.original_ttl_ms <= 0:
            return 0.0
        return max(0.0, min(1.0, self.remaining_ms(server_now_ms) / self.original_ttl_ms))

    def cancel(
        self,
        reason: ExpirationReason,
        *,
        price: Optional[float] = None,
        message: Optional[str] = None,
        server_now_ms: float | None = None,
    ) -> None:
        now = server_now_ms if server_now_ms is not None else time.time() * 1000.0
        if self.status != SignalStatus.ACTIVE:
            return
        self.status = SignalStatus.CANCELLED if reason != ExpirationReason.TTL_EXPIRED else SignalStatus.EXPIRED
        if reason == ExpirationReason.TTL_EXPIRED:
            self.status = SignalStatus.EXPIRED
        elif reason == ExpirationReason.SUPERSEDED:
            self.status = SignalStatus.SUPERSEDED
        else:
            self.status = SignalStatus.CANCELLED
        self.expiration_reason = reason
        self.expiration_message = message or EXPIRATION_REASON_MESSAGES.get(
            reason, "Signal is no longer valid."
        )
        self.cancelled_at_ms = now
        self.expiration_price = price
        self.lifecycle.append(
            {
                "event_type": "cancelled" if self.status == SignalStatus.CANCELLED else "expired",
                "ts_ms": now,
                "reason": reason.value,
                "market_price": price,
                "detail": self.expiration_message,
            }
        )

    def to_public_dict(self, server_now_ms: float | None = None) -> dict[str, Any]:
        now = server_now_ms if server_now_ms is not None else time.time() * 1000.0
        rem = self.remaining_ms(now)
        actionable = self.is_actionable(now)
        display_type = self.signal_type.value
        if not actionable and self.signal_type in (SignalType.BET_NOW, SignalType.STRONG_BET_SIGNAL):
            if self.status == SignalStatus.ACTIVE and now >= self.expires_at_ms:
                display_type = SignalType.SIGNAL_EXPIRED.value
            elif self.expiration_reason == ExpirationReason.PRICE_MOVED:
                display_type = SignalType.OPPORTUNITY_EXPIRED.value
            elif self.status != SignalStatus.ACTIVE:
                display_type = (
                    self.expiration_reason.value
                    if self.expiration_reason
                    else SignalType.SIGNAL_EXPIRED.value
                )

        return {
            "signal_id": self.signal_id,
            "signal_version": self.signal_version,
            "match_id": self.match_id,
            "market_id": self.market_id,
            "market_ticker": self.market_ticker,
            "signal_type": display_type if actionable else (
                SignalType.SIGNAL_EXPIRED.value
                if self.signal_type in (SignalType.BET_NOW, SignalType.STRONG_BET_SIGNAL)
                and not actionable
                else display_type
            ),
            "raw_signal_type": self.signal_type.value,
            "player": self.player,
            "direction": self.direction,
            "created_at_ms": self.created_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "original_ttl_ms": self.original_ttl_ms,
            "remaining_ms": rem,
            "remaining_seconds": round(rem / 1000.0, 1),
            "progress": self.progress(now),
            "urgency": self.urgency(now),
            "market_price": self.market_price,
            "target_entry_price": self.target_entry_price,
            "maximum_entry_price": self.maximum_entry_price,
            "model_probability": self.model_probability,
            "net_edge": self.net_edge,
            "confidence": self.confidence,
            "status": self.status.value,
            "actionable": actionable,
            "expiration_reason": self.expiration_reason.value if self.expiration_reason else None,
            "expiration_message": self.expiration_message,
            "cancelled_at_ms": self.cancelled_at_ms,
            "creation_price": self.creation_price,
            "expiration_price": self.expiration_price,
            "actual_lifetime_ms": (
                int((self.cancelled_at_ms or now) - self.created_at_ms)
                if self.status != SignalStatus.ACTIVE
                else int(now - self.created_at_ms)
            ),
            "tournament": self.tournament,
            "analysis_mode": self.analysis_mode,
            "server_time_ms": now,
            "confirmation_count": self.confirmation_count,
            "lifecycle": self.lifecycle[-20:],
            # Never leave BET NOW visible when not actionable
            "display_label": (
                self.signal_type.value
                if actionable
                else (
                    "SIGNAL EXPIRED"
                    if self.expiration_reason == ExpirationReason.TTL_EXPIRED
                    or (self.status == SignalStatus.ACTIVE and now >= self.expires_at_ms)
                    else (
                        "OPPORTUNITY EXPIRED"
                        if self.expiration_reason
                        in (
                            ExpirationReason.PRICE_MOVED,
                            ExpirationReason.EDGE_DISAPPEARED,
                        )
                        else "SIGNAL EXPIRED"
                    )
                )
            ),
            "display_sublabel": (
                None
                if actionable
                else (
                    "REANALYZING..."
                    if self.expiration_reason in (ExpirationReason.TTL_EXPIRED, None)
                    else (
                        "DO NOT CHASE"
                        if self.expiration_reason == ExpirationReason.PRICE_MOVED
                        else self.expiration_message
                    )
                )
            ),
        }
