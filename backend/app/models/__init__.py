"""SQLAlchemy ORM models for the Kalshi tennis signal platform."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    positions: Mapped[list["ManualPosition"]] = relationship(back_populates="user")
    risk_settings: Mapped[Optional["RiskSettings"]] = relationship(back_populates="user", uselist=False)


class TennisMatch(Base):
    __tablename__ = "tennis_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    tournament: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    player_a: Mapped[str] = mapped_column(String(255))
    player_b: Mapped[str] = mapped_column(String(255))
    surface: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="SCHEDULED")
    observation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    observation_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    signals_emitted: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_mode: Mapped[str] = mapped_column(String(32), default="OBSERVING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    markets: Mapped[list["KalshiMarket"]] = relationship(back_populates="match")
    score_updates: Mapped[list["TennisScoreUpdate"]] = relationship(back_populates="match")
    signals: Mapped[list["Signal"]] = relationship(back_populates="match")


class KalshiEvent(Base):
    __tablename__ = "kalshi_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_ticker: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    series_ticker: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    markets: Mapped[list["KalshiMarket"]] = relationship(back_populates="event")


class KalshiMarket(Base):
    __tablename__ = "kalshi_markets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticker: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("kalshi_events.id"), nullable=True)
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tennis_matches.id"), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    subtitle: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    yes_sub_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    no_sub_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    close_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    open_interest: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_update_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event: Mapped[Optional["KalshiEvent"]] = relationship(back_populates="markets")
    match: Mapped[Optional["TennisMatch"]] = relationship(back_populates="markets")
    ticks: Mapped[list["MarketTick"]] = relationship(back_populates="market")
    orderbook_updates: Mapped[list["OrderbookUpdate"]] = relationship(back_populates="market")
    signals: Mapped[list["Signal"]] = relationship(back_populates="market")


class MarketTick(Base):
    __tablename__ = "market_ticks"
    __table_args__ = (Index("ix_market_ticks_market_ts", "market_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    yes_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_trade: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    microprice: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    market: Mapped["KalshiMarket"] = relationship(back_populates="ticks")


class OrderbookUpdate(Base):
    __tablename__ = "orderbook_updates"
    __table_args__ = (Index("ix_ob_market_ts", "market_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    yes_bids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    yes_asks_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    imbalance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_no: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    market: Mapped["KalshiMarket"] = relationship(back_populates="orderbook_updates")


class TennisScoreUpdate(Base):
    __tablename__ = "tennis_score_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("tennis_matches.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    server: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    point_score: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    game_score: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    set_score: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    match_score: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    aces_a: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    aces_b: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    double_faults_a: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    double_faults_b: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    break_points_a: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    break_points_b: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    match: Mapped["TennisMatch"] = relationship(back_populates="score_updates")


class ModelPrediction(Base):
    __tablename__ = "model_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("tennis_matches.id"), index=True)
    market_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    player: Mapped[str] = mapped_column(String(255))
    model_win_probability: Mapped[float] = mapped_column(Float)
    executable_market_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_net_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="TennisProbabilityModel")


class SignalScore(Base):
    __tablename__ = "signal_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confidence: Mapped[float] = mapped_column(Float)
    net_edge: Mapped[float] = mapped_column(Float)
    model_component: Mapped[float] = mapped_column(Float, default=0)
    orderbook_component: Mapped[float] = mapped_column(Float, default=0)
    momentum_component: Mapped[float] = mapped_column(Float, default=0)
    trade_flow_component: Mapped[float] = mapped_column(Float, default=0)
    liquidity_component: Mapped[float] = mapped_column(Float, default=0)
    spread_component: Mapped[float] = mapped_column(Float, default=0)
    trend_component: Mapped[float] = mapped_column(Float, default=0)
    volatility_component: Mapped[float] = mapped_column(Float, default=0)
    confirmation_count: Mapped[int] = mapped_column(Integer, default=0)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_match_status", "match_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. SIG-ALC-SIN-28491
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tennis_matches.id"), nullable=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    signal_type: Mapped[str] = mapped_column(String(64))
    player: Mapped[str] = mapped_column(String(255))
    direction: Mapped[str] = mapped_column(String(8))  # YES / NO
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    original_ttl_ms: Mapped[int] = mapped_column(Integer)
    actual_lifetime_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signal_version: Mapped[int] = mapped_column(Integer, default=1)
    market_price: Mapped[float] = mapped_column(Float)  # cents 0-100
    target_entry_price: Mapped[float] = mapped_column(Float)
    maximum_entry_price: Mapped[float] = mapped_column(Float)
    model_probability: Mapped[float] = mapped_column(Float)
    net_edge: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    expiration_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expiration_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    creation_price: Mapped[float] = mapped_column(Float)
    expiration_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    urgency_hint: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    match: Mapped[Optional["TennisMatch"]] = relationship(back_populates="signals")
    market: Mapped["KalshiMarket"] = relationship(back_populates="signals")
    lifecycle_events: Mapped[list["SignalLifecycleEvent"]] = relationship(back_populates="signal")
    results: Mapped[list["SignalResult"]] = relationship(back_populates="signal")


class SignalLifecycleEvent(Base):
    __tablename__ = "signal_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), ForeignKey("signals.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_type: Mapped[str] = mapped_column(String(64))  # created | updated | cancelled | expired
    market_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    signal: Mapped["Signal"] = relationship(back_populates="lifecycle_events")


class SignalResult(Base):
    __tablename__ = "signal_results"
    __table_args__ = (UniqueConstraint("signal_id", "horizon_seconds", name="uq_signal_horizon"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), ForeignKey("signals.id"), index=True)
    horizon_seconds: Mapped[int] = mapped_column(Integer)  # 1,5,10,30,60
    entry_price: Mapped[float] = mapped_column(Float)
    future_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_favorable_movement: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_adverse_movement: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    signal: Mapped["Signal"] = relationship(back_populates="results")


class ManualPosition(Base):
    __tablename__ = "manual_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tennis_matches.id"), nullable=True)
    signal_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("signals.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(8))
    player: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    entry_price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    peak_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    drawdown_from_peak: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    position_state: Mapped[str] = mapped_column(String(32), default="HOLD")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="positions")
    entries: Mapped[list["ManualEntry"]] = relationship(back_populates="position")
    exits: Mapped[list["ManualExit"]] = relationship(back_populates="position")


class ManualEntry(Base):
    __tablename__ = "manual_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    position_id: Mapped[str] = mapped_column(String(36), ForeignKey("manual_positions.id"), index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    position: Mapped["ManualPosition"] = relationship(back_populates="entries")


class ManualExit(Base):
    __tablename__ = "manual_exits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    position_id: Mapped[str] = mapped_column(String(36), ForeignKey("manual_positions.id"), index=True)
    exit_price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    position: Mapped["ManualPosition"] = relationship(back_populates="exits")


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)
    max_position_size: Mapped[float] = mapped_column(Float, default=100.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=3)
    max_signals_per_match: Mapped[int] = mapped_column(Integer, default=3)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="risk_settings")


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    config_json: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    category: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PatternDefinition(Base):
    __tablename__ = "pattern_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    pattern_type: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    family: Mapped[str] = mapped_column(String(64))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PatternInstanceRecord(Base):
    __tablename__ = "pattern_instances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    match_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), index=True)
    pattern_type: Mapped[str] = mapped_column(String(64), index=True)
    player_side: Mapped[str] = mapped_column(String(8), default="YES")
    start_timestamp: Mapped[float] = mapped_column(Float)
    end_timestamp: Mapped[float] = mapped_column(Float)
    start_price: Mapped[float] = mapped_column(Float)
    lowest_price: Mapped[float] = mapped_column(Float)
    highest_price: Mapped[float] = mapped_column(Float)
    recovery_price: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    price_change: Mapped[float] = mapped_column(Float)
    volatility: Mapped[float] = mapped_column(Float)
    spread: Mapped[float] = mapped_column(Float)
    liquidity: Mapped[float] = mapped_column(Float)
    orderbook_before: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    orderbook_during: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    orderbook_after: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trade_flow: Mapped[float] = mapped_column(Float, default=0)
    result: Mapped[str] = mapped_column(String(32), default="open")
    success_or_failure: Mapped[str] = mapped_column(String(32), default="open")


class PatternFeature(Base):
    __tablename__ = "pattern_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_id: Mapped[str] = mapped_column(String(64), index=True)
    normalized_pullback: Mapped[float] = mapped_column(Float, default=0)
    normalized_recovery: Mapped[float] = mapped_column(Float, default=0)
    volatility: Mapped[float] = mapped_column(Float, default=0)
    spread: Mapped[float] = mapped_column(Float, default=0)
    liquidity: Mapped[float] = mapped_column(Float, default=0)
    trade_flow: Mapped[float] = mapped_column(Float, default=0)
    feature_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PatternResultRecord(Base):
    __tablename__ = "pattern_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_id: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(32))
    success_or_failure: Mapped[str] = mapped_column(String(32))
    price_change: Mapped[float] = mapped_column(Float, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    favorable_move: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adverse_move: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class PatternSimilarityRecord(Base):
    __tablename__ = "pattern_similarity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_id: Mapped[str] = mapped_column(String(64), index=True)
    compared_to: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0)
    cluster_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cluster_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class PatternPerformance(Base):
    __tablename__ = "pattern_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pattern_type: Mapped[str] = mapped_column(String(64), index=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    successful_continuations: Mapped[int] = mapped_column(Integer, default=0)
    failed_patterns: Mapped[int] = mapped_column(Integer, default=0)
    entry_signals: Mapped[int] = mapped_column(Integer, default=0)
    late_entries: Mapped[int] = mapped_column(Integer, default=0)
    false_positives: Mapped[int] = mapped_column(Integer, default=0)
    expiries: Mapped[int] = mapped_column(Integer, default=0)
    average_similarity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_entry_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low_sample_size: Mapped[bool] = mapped_column(Boolean, default=True)


class ReasoningRecordMixin:
    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    match_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceGameRecord(ReasoningRecordMixin, Base):
    __tablename__ = "service_games"


class ServeBlockRecord(ReasoningRecordMixin, Base):
    __tablename__ = "serve_blocks"


class ServeBlockFeatureRecord(ReasoningRecordMixin, Base):
    __tablename__ = "serve_block_features"


class ServeBlockReasoningRecord(ReasoningRecordMixin, Base):
    __tablename__ = "serve_block_reasoning"


class TennisMarketDivergenceRecord(ReasoningRecordMixin, Base):
    __tablename__ = "tennis_market_divergence"


class ReasoningHistoryRecord(ReasoningRecordMixin, Base):
    __tablename__ = "reasoning_history"


class TennisPointRecord(ReasoningRecordMixin, Base):
    __tablename__ = 'tennis_points'


class TennisPointStateRecord(ReasoningRecordMixin, Base):
    __tablename__ = 'tennis_point_states'


class GPTPatternRecord(ReasoningRecordMixin, Base):
    __tablename__ = 'gpt_pattern_analyses'


class UserStrategySettings(Base):
    """One row per user. Another user's save never writes this row."""

    __tablename__ = "user_strategy_settings"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    auto_entry: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_exit: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    bet_amount: Mapped[float] = mapped_column(Float, default=10.0)
    entry_type: Mapped[str] = mapped_column(String(16), default="YES")
    entry_price: Mapped[float] = mapped_column(Float, default=50.0)
    take_profit_percent: Mapped[float] = mapped_column(Float, default=8.0)
    stop_loss_percent: Mapped[float] = mapped_column(Float, default=4.0)
    trailing_stop_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    trailing_activation_percent: Mapped[float] = mapped_column(Float, default=5.0)
    trailing_stop_percent: Mapped[float] = mapped_column(Float, default=3.0)
    max_trades_per_match: Mapped[int] = mapped_column(Integer, default=2)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_position_per_match: Mapped[float] = mapped_column(Float, default=25.0)
    max_daily_loss: Mapped[float] = mapped_column(Float, default=25.0)
    max_daily_exposure: Mapped[float] = mapped_column(Float, default=50.0)
    market_scope: Mapped[str] = mapped_column(String(16), default="ALL_LIVE")
    selected_tickers_json: Mapped[str] = mapped_column(Text, default="[]")
    one_direction_per_market: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KalshiOrderRecord(Base):
    __tablename__ = "kalshi_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    position_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(8))
    requested_price: Mapped[float] = mapped_column(Float)
    filled_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    requested_qty: Mapped[float] = mapped_column(Float, default=0)
    filled_qty: Mapped[float] = mapped_column(Float, default=0)
    fees: Mapped[float] = mapped_column(Float, default=0)
    kalshi_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="ORDER SUBMITTED")
    created_at_ms: Mapped[float] = mapped_column(Float, default=0)


class StrategyPosition(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), index=True)
    player_label: Mapped[str] = mapped_column(String(255), default="")
    side: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(40), default="WAITING FOR ENTRY")
    bet_amount: Mapped[float] = mapped_column(Float, default=0)
    requested_price: Mapped[float] = mapped_column(Float, default=0)
    entry_price: Mapped[float] = mapped_column(Float, default=0)
    filled_qty: Mapped[float] = mapped_column(Float, default=0)
    fees: Mapped[float] = mapped_column(Float, default=0)
    peak_price: Mapped[float] = mapped_column(Float, default=0)
    current_exit_price: Mapped[float] = mapped_column(Float, default=0)
    trailing_active: Mapped[bool] = mapped_column(Boolean, default=False)
    rules_json: Mapped[str] = mapped_column(Text, default="{}")
    opened_at_ms: Mapped[float] = mapped_column(Float, default=0)
    closed_at_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)


class PositionEvent(Base):
    __tablename__ = "position_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    position_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at_ms: Mapped[float] = mapped_column(Float, default=0)


class StrategyEvent(Base):
    __tablename__ = "strategy_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at_ms: Mapped[float] = mapped_column(Float, default=0)


class DailyRiskState(Base):
    __tablename__ = "daily_risk_state"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    realized_loss: Mapped[float] = mapped_column(Float, default=0)
    exposure: Mapped[float] = mapped_column(Float, default=0)
