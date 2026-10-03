"""In-memory live signal objects with timestamp-based expiration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.clock import remaining_ms, urgency_stage
from app.core.enums import (
    ACTIONABLE_SIGNAL_TYPES,
    EXPIRATION_REASON_MESSAGES,
    ExpirationReason,
    SignalStatus,
    SignalType,
    signal_display_label,
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
    pattern_type: str = ""
    pattern_name: str = ""
    pattern_similarity: Optional[float] = None
    pattern_confidence: Optional[float] = None
    pattern_entry_score: Optional[float] = None
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    pattern_progress: Optional[float] = None
    pattern_stage: str = ""
    bet_instruction: str = ""
    market_instruction: str = ""
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
        if self.signal_type not in ACTIONABLE_SIGNAL_TYPES:
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
        if not actionable and self.signal_type in ACTIONABLE_SIGNAL_TYPES:
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
                if self.signal_type in ACTIONABLE_SIGNAL_TYPES and not actionable
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
            "pattern_type": self.pattern_type,
            "pattern_name": self.pattern_name,
            "pattern_similarity": self.pattern_similarity,
            "pattern_confidence": self.pattern_confidence,
            "pattern_entry_score": self.pattern_entry_score,
            "entry_zone_low": self.entry_zone_low,
            "entry_zone_high": self.entry_zone_high,
            "pattern_progress": self.pattern_progress,
            "pattern_stage": self.pattern_stage,
            "bet_instruction": self.bet_instruction,
            "market_instruction": self.market_instruction,
            "lifecycle": self.lifecycle[-20:],
            # Never leave BET NOW visible when not actionable
            "display_label": _display_label(self, actionable, now),
            "display_sublabel": _display_sublabel(self, actionable),
        }


def _entry_type(signal: LiveSignal) -> bool:
    return signal.signal_type in (
        SignalType.ENTRY_SIGNAL,
        SignalType.STRONG_ENTRY_SIGNAL,
        SignalType.PATTERN_ENTRY_SIGNAL,
        SignalType.STRONG_PATTERN_SIGNAL,
    )


def _display_label(signal: LiveSignal, actionable: bool, now: float) -> str:
    if actionable and signal.bet_instruction:
        return signal.bet_instruction
    if actionable:
        return signal_display_label(signal.signal_type)
    if _entry_type(signal) and signal.expiration_reason == ExpirationReason.PRICE_MOVED:
        return "DO NOT ENTER"
    if _entry_type(signal) and signal.expiration_reason == ExpirationReason.PATTERN_INVALIDATED:
        return "PATTERN INVALIDATED"
    if _entry_type(signal) and signal.expiration_reason == ExpirationReason.EDGE_DISAPPEARED:
        return "ENTRY CANCELLED"
    if signal.expiration_reason == ExpirationReason.TTL_EXPIRED or (
        signal.status == SignalStatus.ACTIVE and now >= signal.expires_at_ms
    ):
        return "SIGNAL EXPIRED"
    if signal.expiration_reason in (ExpirationReason.PRICE_MOVED, ExpirationReason.EDGE_DISAPPEARED):
        return "OPPORTUNITY EXPIRED"
    return "SIGNAL EXPIRED"


def _display_sublabel(signal: LiveSignal, actionable: bool) -> str | None:
    if actionable and signal.market_instruction:
        return signal.market_instruction
    if actionable:
        return None
    if _entry_type(signal) and signal.expiration_reason == ExpirationReason.PRICE_MOVED:
        return "PRICE MOVED BEYOND ENTRY WINDOW"
    if _entry_type(signal) and signal.expiration_reason == ExpirationReason.PATTERN_INVALIDATED:
        return "PATTERN PROGRESSED BEYOND IDEAL ENTRY ZONE"
    if _entry_type(signal) and signal.expiration_reason == ExpirationReason.EDGE_DISAPPEARED:
        return "CONDITIONS CHANGED"
    if signal.expiration_reason in (ExpirationReason.TTL_EXPIRED, None):
        return "REANALYZING..."
    if signal.expiration_reason == ExpirationReason.PRICE_MOVED:
        return "DO NOT CHASE"
    return signal.expiration_message
