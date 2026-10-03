"""Shared enums for signals, markets, and positions."""

from enum import Enum


class SignalType(str, Enum):
    BET_NOW = "BET_NOW"  # legacy alias of BET_SIGNAL
    BET_SIGNAL = "BET_SIGNAL"
    STRONG_BET_SIGNAL = "STRONG_BET_SIGNAL"
    WATCH = "WATCH"
    CLOSE_TO_SIGNAL = "CLOSE_TO_SIGNAL"
    WAIT = "WAIT"
    NO_BET = "NO_BET"
    EDGE_DISAPPEARING = "EDGE_DISAPPEARING"
    CONSIDER_EXIT_NOW = "CONSIDER_EXIT_NOW"
    EXIT_WARNING = "EXIT_WARNING"
    MATCH_TOO_VOLATILE = "MATCH_TOO_VOLATILE"
    OPPORTUNITY_EXPIRED = "OPPORTUNITY_EXPIRED"
    STUDYING_MATCH = "STUDYING_MATCH"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    REANALYZING = "REANALYZING"
    DO_NOT_CHASE = "DO_NOT_CHASE"
    OPPORTUNITY_MISSED = "OPPORTUNITY_MISSED"
    DATA_DELAY = "DATA_DELAY"
    HOLD = "HOLD"
    CONDITIONS_IMPROVING = "CONDITIONS_IMPROVING"
    KEEP_WATCHING = "KEEP_WATCHING"


class SignalStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"
    CONVERTED = "CONVERTED"  # user placed bet


class ExpirationReason(str, Enum):
    TTL_EXPIRED = "TTL_EXPIRED"
    PRICE_MOVED = "PRICE_MOVED"
    EDGE_DISAPPEARED = "EDGE_DISAPPEARED"
    CONFIDENCE_DROPPED = "CONFIDENCE_DROPPED"
    ORDERBOOK_REVERSAL = "ORDERBOOK_REVERSAL"
    MOMENTUM_REVERSAL = "MOMENTUM_REVERSAL"
    LIQUIDITY_LOSS = "LIQUIDITY_LOSS"
    SPREAD_EXPANSION = "SPREAD_EXPANSION"
    STALE_DATA = "STALE_DATA"
    CONNECTION_LOST = "CONNECTION_LOST"
    MARKET_SUSPENDED = "MARKET_SUSPENDED"
    MARKET_CLOSED = "MARKET_CLOSED"
    TENNIS_MODEL_CHANGED = "TENNIS_MODEL_CHANGED"
    RISK_LIMIT = "RISK_LIMIT"
    SUPERSEDED = "SUPERSEDED"
    USER_PLACED = "USER_PLACED"
    USER_CANCELLED = "USER_CANCELLED"


EXPIRATION_REASON_MESSAGES: dict[ExpirationReason, str] = {
    ExpirationReason.TTL_EXPIRED: "The signal validity window ended.",
    ExpirationReason.PRICE_MOVED: "The market moved beyond the maximum acceptable entry price. Do not chase.",
    ExpirationReason.EDGE_DISAPPEARED: "Estimated net edge fell below the minimum threshold.",
    ExpirationReason.CONFIDENCE_DROPPED: "Signal confidence dropped below the required level.",
    ExpirationReason.ORDERBOOK_REVERSAL: "Order-book pressure reversed against the signal direction.",
    ExpirationReason.MOMENTUM_REVERSAL: "Short-term momentum reversed.",
    ExpirationReason.LIQUIDITY_LOSS: "Executable liquidity disappeared or became insufficient.",
    ExpirationReason.SPREAD_EXPANSION: "The bid-ask spread widened beyond acceptable limits.",
    ExpirationReason.STALE_DATA: "Market data became stale. Do not bet on delayed information.",
    ExpirationReason.CONNECTION_LOST: "Live connection lost. Signals cancelled for safety.",
    ExpirationReason.MARKET_SUSPENDED: "The market was suspended.",
    ExpirationReason.MARKET_CLOSED: "The market closed.",
    ExpirationReason.TENNIS_MODEL_CHANGED: "The tennis probability model changed materially.",
    ExpirationReason.RISK_LIMIT: "A risk limit was hit for this match or account.",
    ExpirationReason.SUPERSEDED: "A newer signal version replaced this one.",
    ExpirationReason.USER_PLACED: "You recorded a manual bet entry for this signal.",
    ExpirationReason.USER_CANCELLED: "Signal dismissed by user.",
}


class MarketStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    SUSPENDED = "SUSPENDED"
    SETTLED = "SETTLED"
    UNKNOWN = "UNKNOWN"


class ConnectionStatus(str, Enum):
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    DISCONNECTED = "DISCONNECTED"


class AnalysisMode(str, Enum):
    MARKET_ONLY = "MARKET_ONLY"
    TENNIS_ENHANCED = "TENNIS_ENHANCED"
    OBSERVING = "OBSERVING"
    COOLDOWN = "COOLDOWN"


class Direction(str, Enum):
    YES = "YES"
    NO = "NO"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    COOLDOWN = "COOLDOWN"


ACTIONABLE_SIGNAL_TYPES = {
    SignalType.BET_NOW,
    SignalType.BET_SIGNAL,
    SignalType.STRONG_BET_SIGNAL,
}

SIGNAL_DISPLAY_LABELS = {
    SignalType.BET_NOW: "BET SIGNAL",
    SignalType.BET_SIGNAL: "BET SIGNAL",
    SignalType.STRONG_BET_SIGNAL: "STRONG BET SIGNAL",
    SignalType.WATCH: "WATCH",
    SignalType.CLOSE_TO_SIGNAL: "CLOSE TO SIGNAL",
    SignalType.NO_BET: "NO BET",
    SignalType.OPPORTUNITY_MISSED: "OPPORTUNITY MISSED",
    SignalType.DO_NOT_CHASE: "DO NOT CHASE",
    SignalType.STUDYING_MATCH: "STUDYING MATCH",
}


def signal_display_label(signal_type: SignalType | str) -> str:
    try:
        key = signal_type if isinstance(signal_type, SignalType) else SignalType(signal_type)
    except ValueError:
        return str(signal_type).replace("_", " ")
    return SIGNAL_DISPLAY_LABELS.get(key, key.value.replace("_", " "))


class UrgencyStage(str, Enum):
    NORMAL = "NORMAL"  # > 5s
    CAUTION = "CAUTION"  # 2–5s
    FINAL = "FINAL"  # < 2s
    EXPIRED = "EXPIRED"
